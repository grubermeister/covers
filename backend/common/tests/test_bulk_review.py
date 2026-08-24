"""Bulk approve / reject on the editor queue (issue #101).

The VPHC ingest put 2,443 pending rows in the queue and every review action was
one row at a time. These pin the parts that are dangerous rather than merely
new:

  * REGION SCOPING. Bulk is the first detail=False WRITE action in this API.
    Every other write is detail=True, so get_object() runs
    check_object_permissions and CanReviewContribution enforces collection
    assignment for free. detail=False never calls get_object(), so that check
    vanishes unless the view re-asserts it per row.
  * PARTIAL FAILURE. A batch that rolls back its successes because row 17 was
    bad is worse than no batch at all -- approval mints permanent codes and
    consolidate_superseded_contributions deletes rows, so re-running is not
    free.
  * THE HUMAN SUBMISSIONS. The queue is live. "Select all matching" on an
    unfiltered queue would sweep up the eight real people who submitted into
    it; the source filter is what keeps them out of the default match set.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection, CollectionAssignment, Contribution, Marking, PostOffice,
    PostOfficeRegion, Region, SubmissionTransaction,
)

User = get_user_model()

BULK_APPROVE = "/api/v2/contributions/bulk-approve/"
BULK_REJECT = "/api/v2/contributions/bulk-reject/"
IDS = "/api/v2/contributions/ids/"


class BulkReviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser("admin", "a@example.com", "pw")

        editors = Group.objects.create(name="Editors")
        editors.permissions.add(
            Permission.objects.get(codename="review_contribution"))

        self.va, self.va_collection = self._region("Virginia", "VA")
        self.wv, self.wv_collection = self._region("West Virginia", "WV")

        # Assigned to Virginia only. The whole point of the security test.
        self.editor = User.objects.create_user("va_editor", password="pw")
        self.editor.groups.add(editors)
        CollectionAssignment.objects.create(
            user=self.editor, collection=self.va_collection,
            created_by=self.admin, modified_by=self.admin)

        self.client.force_authenticate(self.editor)

    def _region(self, name, abbrev):
        region = Region.objects.create(
            code="USA-{}1".format(abbrev), name=name, abbrev=abbrev,
            region_tier="STATE", created_by=self.admin, modified_by=self.admin)
        collection = Collection.objects.create(
            name=name, region=region, is_active=True,
            created_by=self.admin, modified_by=self.admin)
        po = PostOffice.objects.create(
            name="TOWN-{}".format(abbrev),
            created_by=self.admin, modified_by=self.admin)
        PostOfficeRegion.objects.create(
            post_office=po, region=region,
            created_by=self.admin, modified_by=self.admin)
        return region, collection

    def _pending(self, collection=None, *, vphc=True, town=None, **extra):
        collection = collection or self.va_collection
        payload = {
            "type": "TOWNMARK",
            "state": collection.region.name,
            "town": town or "TOWN-{}".format(collection.region.abbrev),
            "inscription_txt": "INSC",
            "is_manuscript": False, "is_irreg": False,
            "no_marking_image": True,
        }
        if vphc:
            payload["vphc"] = {"key": "k", "flags": [], "src": "T1:r1"}
        payload.update(extra)
        return Contribution.objects.create(
            contributor=self.admin, collection=collection,
            status=Contribution.STATUS_PENDING, submitted_data=payload,
            created_by=self.admin, modified_by=self.admin)

    # ---------------------------------------------------------------- security

    def test_editor_cannot_bulk_approve_outside_their_region(self):
        """The check that detail=False silently removes.

        CanReviewContribution.has_object_permission would have caught this on
        any detail=True action, because get_object() triggers it. A bulk
        endpoint never calls get_object(), so without an explicit per-row check
        a Virginia editor approves West Virginia rows 50 at a time.
        """
        mine = self._pending(self.va_collection)
        theirs = self._pending(self.wv_collection)

        response = self.client.post(
            BULK_APPROVE, {"ids": [mine.pk, theirs.pk]}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["approved"], [mine.pk])
        self.assertEqual([f["id"] for f in response.data["failed"]], [theirs.pk])

        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Contribution.STATUS_PENDING)

    def test_editor_cannot_bulk_reject_outside_their_region(self):
        theirs = self._pending(self.wv_collection)

        response = self.client.post(
            BULK_REJECT, {"ids": [theirs.pk]}, format="json")

        self.assertEqual(response.data["rejected"], [])
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Contribution.STATUS_PENDING)

    def test_anonymous_is_refused(self):
        contrib = self._pending()
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.post(BULK_APPROVE, {"ids": [contrib.pk]}, format="json")
            .status_code, 403)

    # -------------------------------------------------------- partial failure

    def test_a_bad_row_does_not_roll_back_the_good_ones(self):
        """#101 AC-4. Each row commits in its own transaction.

        The single-row action wraps everything in one atomic block; if bulk
        kept that boundary, one unapplyable payload would silently undo every
        approval before it -- and the editor would have no way to tell which.
        """
        good_before = self._pending()
        broken = self._pending(inscription_txt="")   # required, fails apply
        good_after = self._pending()

        response = self.client.post(
            BULK_APPROVE,
            {"ids": [good_before.pk, broken.pk, good_after.pk]}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data["approved"]), {good_before.pk, good_after.pk})
        self.assertEqual([f["id"] for f in response.data["failed"]], [broken.pk])

        good_before.refresh_from_db()
        good_after.refresh_from_db()
        broken.refresh_from_db()
        self.assertEqual(good_before.status, Contribution.STATUS_APPROVED)
        self.assertEqual(good_after.status, Contribution.STATUS_APPROVED)
        self.assertEqual(broken.status, Contribution.STATUS_PENDING)

    def test_a_non_pending_row_is_reported_not_fatal(self):
        pending = self._pending()
        already = self._pending()
        already.status = Contribution.STATUS_APPROVED
        already.save(update_fields=["status"])

        response = self.client.post(
            BULK_APPROVE, {"ids": [pending.pk, already.pk]}, format="json")

        self.assertEqual(response.data["approved"], [pending.pk])
        self.assertIn("not pending", response.data["failed"][0]["reason"])

    def test_unknown_id_is_reported(self):
        response = self.client.post(BULK_APPROVE, {"ids": [999999]}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("not found", response.data["failed"][0]["reason"].lower())

    # ------------------------------------------------------------ audit trail

    def test_every_approved_row_writes_its_own_transaction(self):
        """#101 AC-3. The audit trail must not thin out because it was a batch."""
        rows = [self._pending() for _ in range(3)]
        before = SubmissionTransaction.objects.filter(
            action=SubmissionTransaction.ACTION_APPROVE).count()

        self.client.post(
            BULK_APPROVE, {"ids": [r.pk for r in rows]}, format="json")

        after = SubmissionTransaction.objects.filter(
            action=SubmissionTransaction.ACTION_APPROVE).count()
        self.assertEqual(after - before, 3)

    def test_bulk_approve_mints_distinct_catalog_codes(self):
        """Sequential same-prefix approvals are exactly the shape that used to
        race: _next_serial reads a max, then the save writes it."""
        rows = [self._pending() for _ in range(5)]

        response = self.client.post(
            BULK_APPROVE, {"ids": [r.pk for r in rows]}, format="json")

        self.assertEqual(len(response.data["approved"]), 5)
        codes = [
            Contribution.objects.get(pk=pk).marking.code
            for pk in response.data["approved"]
        ]
        self.assertEqual(len(set(codes)), 5, codes)
        self.assertTrue(all(c for c in codes), codes)

    # ----------------------------------------------------------------- limits

    def test_batch_over_the_cap_is_rejected(self):
        response = self.client.post(
            BULK_APPROVE, {"ids": list(range(1, 52))}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.data["detail"])

    def test_empty_and_malformed_ids_are_rejected(self):
        for body in ({"ids": []}, {"ids": "12"}, {}, {"ids": ["abc"]}):
            self.assertEqual(
                self.client.post(BULK_APPROVE, body, format="json").status_code,
                400, body)

    # ------------------------------------------------------------ select-all

    def test_ids_endpoint_matches_the_list_endpoint(self):
        """"Select all matching" must mean the same set the banner counts.

        Both run filter_queryset(get_queryset()); this pins that they agree,
        because a divergence is invisible until an editor approves rows they
        never saw.
        """
        for _ in range(3):
            self._pending(town="FARMVILLE")
        self._pending(town="RICHMOND")

        listed = self.client.get(
            "/api/v2/contributions/",
            {"mode": "editor", "town": "FARMVILLE", "page_size": 100})
        ids = self.client.get(IDS, {"mode": "editor", "town": "FARMVILLE"})

        self.assertEqual(ids.data["count"], listed.data["count"])
        self.assertEqual(
            set(ids.data["ids"]), {r["id"] for r in listed.data["results"]})

    def test_ids_route_is_not_shadowed_by_the_detail_route(self):
        """`/contributions/ids/` must not resolve as `/contributions/<pk>/`."""
        response = self.client.get(IDS, {"mode": "editor"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("ids", response.data)

    def test_ids_respects_region_scoping(self):
        mine = self._pending(self.va_collection)
        self._pending(self.wv_collection)

        response = self.client.get(IDS, {"mode": "editor"})

        self.assertEqual(response.data["ids"], [mine.pk])

    # -------------------------------------------------------- source filter

    def test_source_filter_separates_ingest_from_human_submissions(self):
        """The control that keeps the eight real people out of a select-all.

        Keyed on the `vphc` key being present, never on status -- the standing
        rule from the 2026-08-16 near-miss, where "all pending" turned out to
        include live human submissions.
        """
        ingest = self._pending(vphc=True)
        human = self._pending(vphc=False)

        vphc_ids = self.client.get(IDS, {"mode": "editor", "source": "vphc"}).data
        human_ids = self.client.get(IDS, {"mode": "editor", "source": "human"}).data
        all_ids = self.client.get(IDS, {"mode": "editor", "source": "all"}).data

        self.assertEqual(vphc_ids["ids"], [ingest.pk])
        self.assertEqual(human_ids["ids"], [human.pk])
        self.assertEqual(set(all_ids["ids"]), {ingest.pk, human.pk})

    def test_source_filter_is_key_presence_not_contents(self):
        """An ingest row with an empty blob is still an ingest row."""
        empty_blob = self._pending(vphc=False)
        empty_blob.submitted_data = {**empty_blob.submitted_data, "vphc": {}}
        empty_blob.save(update_fields=["submitted_data"])

        response = self.client.get(IDS, {"mode": "editor", "source": "vphc"})

        self.assertIn(empty_blob.pk, response.data["ids"])
