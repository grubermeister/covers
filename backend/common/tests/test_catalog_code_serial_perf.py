"""Scale benchmark for catalog serial allocation.

Not a pass/fail gate -- it prints a measurement. `_next_serial` used to be
O(markings + all pending contributions) in Python, with a json.loads per queued
row, which made draining a queue of N rows O(N^2). This builds a queue at the
VPHC ingest's real size and reports what one allocation costs.

Run it directly, it is excluded from the normal suite by the `perf` prefix:
    manage.py test common.tests.test_catalog_code_serial_perf
"""
import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from common.catalog_codes import _next_serial
from common.models import Collection, Contribution, Marking, PostOffice, Region

PREFIX = "ASCC6-VA-M"
PENDING = 2443       # woco.dev, 2026-08-17 census
MARKINGS = 2309      # ?state=VA after the fan-out fix


class NextSerialScaleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user("editor", password="x")
        region = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=user, modified_by=user)
        post_office = PostOffice.objects.create(
            name="Richmond", created_by=user, modified_by=user)
        collection = Collection.objects.create(
            name="Virginia", region=region, is_active=True,
            created_by=user, modified_by=user)

        Marking.objects.bulk_create([
            Marking(
                code="{}{:04d}".format(PREFIX, i), type="TOWNMARK",
                catalog_txt="RICHMOND/VA.", inscription_txt="RICHMOND VA",
                is_manuscript=False, is_irreg=False, post_office=post_office,
                created_by=user, modified_by=user)
            for i in range(1, MARKINGS + 1)
        ])
        # Payloads shaped like the real queue: the VPHC blob is what makes
        # json.loads-per-row expensive, so a bare {"catalog_code": ...} would
        # flatter the old implementation.
        Contribution.objects.bulk_create([
            Contribution(
                contributor=user, collection=collection,
                status=Contribution.STATUS_PENDING,
                submitted_data={
                    "catalog_code": "{}{:04d}".format(PREFIX, MARKINGS + i),
                    "town": "RICHMOND", "state": "VA", "type": "TOWNMARK",
                    "desc": "Imported from the Virginia Postal History Catalog "
                            "(Richmond #%d, T1:r%d). [VPHC: ambiguous]" % (i, 6000 + i),
                    "vphc": {"key": "va-richmond-%d" % i, "flags": ["ambiguous"],
                             "src": "T1", "row": 6000 + i},
                },
                created_by=user, modified_by=user)
            for i in range(1, PENDING + 1)
        ])

    def test_report_allocation_cost(self):
        with CaptureQueriesContext(connection) as ctx:
            start = time.perf_counter()
            serial = _next_serial(subject_type="MARKING", prefix=PREFIX, exclude_id=None)
            elapsed = time.perf_counter() - start

        print(
            "\n  _next_serial over {:,} markings + {:,} pending contributions"
            "\n    {:.1f} ms, {} queries, next serial = {:,}"
            "\n    a full queue drain is {:,} allocations\n".format(
                MARKINGS, PENDING, elapsed * 1000, len(ctx), serial, PENDING)
        )
        self.assertEqual(serial, MARKINGS + PENDING + 1)
