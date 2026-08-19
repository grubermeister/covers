"""What one approval actually costs, at production queue size.

Issue #101's bulk approve loops this path once per row from the browser, so the
per-row cost is what sets the batch size. Guessing it is how you end up with a
button that times out halfway through a 2,443-row queue -- on a one-way door.

This drives the REAL endpoint through APIClient rather than calling
apply_contribution_to_catalog directly, so the number includes permission
checks, serialization, catalog-code minting, the before/after snapshots,
consolidate_superseded_contributions and the audit rows -- everything a bulk
request would pay for.

Prints a measurement rather than asserting a threshold, so it cannot go flaky
on a loaded CI box. Not part of the normal suite's value; run it directly:

    manage.py test common.tests.test_bulk_approve_perf
"""
import time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection, CollectionAssignment, Contribution, Marking, PostOffice,
    PostOfficeRegion, Region,
)

User = get_user_model()

PREFIX = "ASCC6-VA-M"
PENDING = 2443    # woco.dev, 2026-08-17 census
MARKINGS = 2309   # ?state=VA after the fan-out fix
SAMPLE = 12       # approvals to time; enough to see variance, cheap to run

# Proxy/gunicorn territory. A batch should sit well inside this.
TIMEOUT_BUDGET_S = 30


class ApprovalCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        cls.editor = User.objects.create_user("editor", password="pw")
        group = Group.objects.create(name="Editors")
        group.permissions.add(Permission.objects.get(codename="review_contribution"))
        cls.editor.groups.add(group)

        region = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=cls.admin, modified_by=cls.admin)
        cls.collection = Collection.objects.create(
            name="Virginia", region=region, is_active=True,
            created_by=cls.admin, modified_by=cls.admin)
        CollectionAssignment.objects.create(
            user=cls.editor, collection=cls.collection,
            created_by=cls.admin, modified_by=cls.admin)
        po = PostOffice.objects.create(
            name="RICHMOND", created_by=cls.admin, modified_by=cls.admin)
        PostOfficeRegion.objects.create(
            post_office=po, region=region,
            created_by=cls.admin, modified_by=cls.admin)

        Marking.objects.bulk_create([
            Marking(code="{}{:04d}".format(PREFIX, i), type="TOWNMARK",
                    catalog_txt="RICHMOND/VA.", inscription_txt="RICHMOND VA",
                    is_manuscript=False, is_irreg=False, post_office=po,
                    created_by=cls.admin, modified_by=cls.admin)
            for i in range(1, MARKINGS + 1)
        ])

        # Payloads shaped like the real queue -- the vphc blob and the generated
        # prose are what a bulk run actually carries, and a bare payload would
        # flatter the measurement.
        Contribution.objects.bulk_create([
            Contribution(
                contributor=cls.admin, collection=cls.collection,
                status=Contribution.STATUS_PENDING,
                submitted_data={
                    "type": "TOWNMARK", "state": "Virginia", "town": "RICHMOND",
                    "inscription_txt": "RICHMOND VA %d" % i,
                    "is_manuscript": False, "is_irreg": False,
                    "no_marking_image": True,
                    "desc": "Virginia Postal History Catalog Richmond #%d "
                            "(T1:r%d). [VPHC: ambiguous]" % (i, 6000 + i),
                    "vphc": {"key": "va-richmond-%d" % i, "src": "T1:r%d" % (6000 + i),
                             "flags": ["ambiguous"], "cancel_no": str(i)},
                },
                created_by=cls.admin, modified_by=cls.admin)
            for i in range(1, PENDING + 1)
        ])

    def test_report_per_approval_cost(self):
        client = APIClient()
        client.force_authenticate(self.editor)

        ids = list(
            Contribution.objects.filter(status=Contribution.STATUS_PENDING)
            .order_by("pk").values_list("pk", flat=True)[:SAMPLE]
        )

        timings = []
        for pk in ids:
            start = time.perf_counter()
            response = client.post(
                "/api/v2/contributions/{}/approve/".format(pk), {}, format="json")
            timings.append((time.perf_counter() - start) * 1000)
            self.assertEqual(response.status_code, 200, response.data)

        avg = sum(timings) / len(timings)
        worst = max(timings)
        # Size the batch off the worst case, not the mean: the batch that times
        # out is the slow one, and a half-applied batch is the bad outcome.
        batch_for_budget = int(TIMEOUT_BUDGET_S * 1000 / worst)

        print(
            "\n  one approval over a {:,}-row queue ({:,} markings)"
            "\n    mean {:.0f} ms | worst {:.0f} ms | first {:.0f} ms | last {:.0f} ms"
            "\n    25-row batch  ~= {:.1f} s  (worst case {:.1f} s)"
            "\n    all {:,} rows ~= {:.1f} min"
            "\n    rows that fit a {}s request at worst case: {}\n".format(
                PENDING, MARKINGS, avg, worst, timings[0], timings[-1],
                avg * 25 / 1000, worst * 25 / 1000,
                PENDING, avg * PENDING / 60000,
                TIMEOUT_BUDGET_S, batch_for_budget)
        )
