"""
Issue #109 -- the editor review queue's search and filters must run in the
database, not over the page the browser happens to be holding.

Reproduced on woco.dev at 2,440 contributions: searching "farm" at 100 rows per
page returned nothing on page 1 of 25. Farmville was on page 16. The filters
lived in Dashboard.tsx and only ever saw the fetched page, so a lookup that
missed did not read as a broken filter -- it read as "the record isn't there".

The tests that matter here WALK EVERY PAGE at PAGE_SIZE = 3 and assert on sets
of ids, the technique from test_marking_list_fanout.py. A correct total is not
evidence that pagination is correct, and page 1 looking fine is precisely the
failure being fixed.

Run: uv run python backend/manage.py test common.tests.test_contribution_queue_search
"""
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Contribution,
    ContributionRecycleBin,
    Region,
    Shape,
)


User = get_user_model()

# Small enough that a walk crosses several page boundaries, which is where
# LIMIT/OFFSET drops or repeats rows.
PAGE_SIZE = 3

URL = "/api/v2/contributions/"


class ContributionQueueSearchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw"
        )
        self.editor = User.objects.create_user(username="editor", password="pw")
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(Permission.objects.get(codename="review_contribution"))
        self.editor.groups.add(editors)
        self.contributor = User.objects.create_user(username="gstone", password="pw")

        self.virginia = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.collection = Collection.objects.create(
            name="Virginia Collection",
            region=self.virginia,
            created_by=self.admin,
            modified_by=self.admin,
        )
        CollectionAssignment.objects.create(
            user=self.editor,
            collection=self.collection,
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.circle = Shape.objects.create(
            name="C - Circle", created_by=self.admin, modified_by=self.admin
        )

        # The needle goes in FIRST so that under the default `-created_date`
        # ordering it lands on the LAST page. A page-local implementation
        # cannot find it from page 1; that is the whole point of the AC test.
        self.farmville = self._contribution(
            town="Farmville",
            extra={
                "type": "RATEMARK",
                "inscription_txt": "PAID 10",
                "color": "RED",
                "shape": "C - Circle",
                "vphc": {"vphc_code": "VPHC-VA-FARMVILLE-6"},
            },
        )
        # Enough filler to guarantee several pages at PAGE_SIZE = 3.
        self.filler = [self._contribution(town=f"Richmond {n}") for n in range(9)]

    def _contribution(self, town="Richmond", status=Contribution.STATUS_PENDING,
                      collection=None, contributor=None, extra=None):
        payload = {"state": "Virginia", "town": town}
        payload.update(extra or {})
        return Contribution.objects.create(
            contributor=contributor or self.contributor,
            collection=collection or self.collection,
            submitted_data=payload,
            status=status,
            created_by=self.admin,
            modified_by=self.admin,
        )

    @staticmethod
    def _backdate(contribution, when):
        # created_date is auto_now_add, so it cannot be set on create() or by
        # save(). .update() writes the column directly.
        Contribution.objects.filter(pk=contribution.pk).update(created_date=when)

    def _get(self, **params):
        self.client.force_authenticate(self.editor)
        response = self.client.get(URL, {"mode": "editor", **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def _ids(self, **params):
        response = self._get(**params)
        results = response.data.get("results", response.data)
        return [row["id"] for row in results], response.data.get("count")

    def _walk(self, **params):
        """Every id the list endpoint serves, in order, across all pages."""
        ids = []
        page = 1
        while True:
            response = self._get(page_size=PAGE_SIZE, page=page, **params)
            ids.extend(row["id"] for row in response.data["results"])
            if not response.data["next"]:
                return ids, response.data["count"]
            page += 1

    def assertWalkIsClean(self, label, **params):
        ids, count = self._walk(**params)
        self.assertEqual(
            len(ids) - len(set(ids)), 0, f"{label}: served a row twice across pages"
        )
        self.assertEqual(
            len(ids), count, f"{label}: walked {len(ids)} rows but count says {count}"
        )
        return set(ids)

    # -- the acceptance criterion ------------------------------------------

    def test_search_finds_a_town_that_is_not_on_the_first_page(self):
        """`farm` finds Farmville from page 1. Fails against a page-local filter."""
        ids, count = self._ids(q="farm", page_size=PAGE_SIZE, page=1)

        self.assertEqual(count, 1)
        self.assertEqual(ids, [self.farmville.pk])

        # Prove the premise: without the filter it really is on the last page.
        unfiltered, _ = self._walk()
        self.assertNotIn(
            self.farmville.pk,
            unfiltered[:PAGE_SIZE],
            "fixture is wrong -- the needle must not start on page 1",
        )

    def test_search_walk_serves_every_match_exactly_once(self):
        self._contribution(town="Farmington")
        matched = self.assertWalkIsClean("q=farm", q="farm")
        self.assertEqual(len(matched), 2)

    # -- what the search box matches ---------------------------------------

    def test_search_matches_each_field_the_editors_actually_type(self):
        for term, label in [
            ("farmville", "town"),
            ("virginia", "state"),
            ("ratemark", "type"),
            ("paid 10", "inscription"),
            ("VPHC-VA-FARMVILLE-6", "vphc code"),
            ("gstone", "contributor username"),
        ]:
            with self.subTest(field=label):
                ids, _ = self._ids(q=term, page_size=100)
                self.assertIn(self.farmville.pk, ids, f"{label} did not match")

    def test_search_by_a_bare_entry_number(self):
        ids, count = self._ids(q=str(self.farmville.pk), page_size=100)
        self.assertEqual(count, 1)
        self.assertEqual(ids, [self.farmville.pk])

    def test_a_long_digit_string_does_not_error(self):
        # Guards the len<=9 bound -- an unbounded int would overflow the column.
        _, count = self._ids(q="1234567890123456789012345678901234567890")
        self.assertEqual(count, 0)

    # -- the individual filters --------------------------------------------

    def test_blank_filters_are_no_ops(self):
        _, everything = self._ids(page_size=100)
        _, blanked = self._ids(
            q="   ", town="", shape="", color="", page_size=100,
        )
        self.assertEqual(blanked, everything)

    def test_town_is_contains_and_case_insensitive(self):
        ids, count = self._ids(town="FARM", page_size=100)
        self.assertEqual(count, 1)
        self.assertEqual(ids, [self.farmville.pk])

    def test_shape_and_color_match_by_name_case_insensitively(self):
        for param, value in [("shape", "c - circle"), ("color", "red")]:
            with self.subTest(filter=param):
                ids, count = self._ids(**{param: value}, page_size=100)
                self.assertEqual(count, 1)
                self.assertEqual(ids, [self.farmville.pk])

    def test_shape_accepts_a_legacy_numeric_id(self):
        # The dashboard used to emit Shape ids into ?e_shape=; a bookmark from
        # then must still resolve.
        ids, count = self._ids(shape=str(self.circle.pk), page_size=100)
        self.assertEqual(count, 1)
        self.assertEqual(ids, [self.farmville.pk])

    def test_submitted_date_range_is_inclusive_of_both_endpoints(self):
        early = self._contribution(town="Early")
        late = self._contribution(town="Late")
        self._backdate(early, datetime(2026, 7, 1, 9, 0, tzinfo=dt_timezone.utc))
        self._backdate(late, datetime(2026, 7, 31, 23, 30, tzinfo=dt_timezone.utc))

        ids, _ = self._ids(
            submitted_from="2026-07-01", submitted_to="2026-07-31", page_size=100,
        )
        self.assertIn(early.pk, ids, "the from endpoint must be inclusive")
        self.assertIn(late.pk, ids, "the to endpoint must be inclusive")
        self.assertNotIn(self.farmville.pk, ids)

    # -- drafts -------------------------------------------------------------

    def test_drafts_are_excluded_from_the_editor_queue_and_from_its_count(self):
        draft = self._contribution(town="Draftsville", status=Contribution.STATUS_DRAFT)

        ids, count = self._ids(page_size=100)

        self.assertNotIn(draft.pk, ids)
        # The count is the half that used to lie: the client dropped drafts
        # after the fetch, so the banner described a different set of rows.
        self.assertEqual(count, len(ids))

    def test_drafts_stay_visible_to_their_author(self):
        draft = self._contribution(town="Draftsville", status=Contribution.STATUS_DRAFT)

        self.client.force_authenticate(self.contributor)
        response = self.client.get(URL, {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data.get("results", response.data)

        self.assertIn(draft.pk, [row["id"] for row in results])

    # -- ordering -----------------------------------------------------------

    def test_ordering_by_town_walks_every_row_exactly_once(self):
        # Ten rows sharing one town: without the `id` tiebreak the sort has no
        # deterministic order within the tie and LIMIT/OFFSET repeats rows.
        tied = [self._contribution(town="Tiedtown") for _ in range(10)]
        walked = self.assertWalkIsClean("ordering=town", ordering="town")
        for row in tied:
            self.assertIn(row.pk, walked)

    def test_default_ordering_has_a_unique_tiebreak(self):
        # The production shape of the problem: one batch import wrote 2,062
        # rows, so created_date ties are the norm rather than the exception.
        stamp = datetime(2026, 8, 17, 15, 17, 34, tzinfo=dt_timezone.utc)
        for row in [self.farmville, *self.filler]:
            self._backdate(row, stamp)
        self.assertWalkIsClean("default ordering, all timestamps tied")

    def test_rows_without_a_shape_key_sort_as_one_group(self):
        # ~60% of the queued rows carry no `shape` key at all. Locks the
        # documented NULLIF behaviour so a JSON null and an absent key cannot
        # start sorting apart from each other silently.
        explicit_null = self._contribution(town="Nulltown", extra={"shape": None})
        ids, _ = self._ids(ordering="shape", page_size=100)
        absent = self.filler[0].pk
        self.assertLess(
            ids.index(explicit_null.pk) - ids.index(absent),
            len(ids),
            "a JSON null shape must group with an absent one",
        )
        # The one row that HAS a shape sorts away from the null group.
        self.assertEqual(ids[-1], self.farmville.pk)

    def test_every_ordering_key_the_dashboard_can_send_is_accepted(self):
        for key in ["status", "state", "town", "shape", "color", "submitted",
                    "created_at", "updated_at", "contributor_username", "id"]:
            for term in (key, f"-{key}"):
                with self.subTest(ordering=term):
                    self._get(ordering=term, page_size=100)

    def test_an_unknown_ordering_key_falls_back_and_does_not_error(self):
        ids, _ = self._ids(ordering="bogus", page_size=100)
        default, _ = self._ids(page_size=100)
        self.assertEqual(ids, default)

    # -- guards on behaviour this PR must not change -------------------------

    def test_status_filter_still_works(self):
        rejected = self._contribution(
            town="Rejectville", status=Contribution.STATUS_REJECTED
        )
        ids, count = self._ids(status="rejected", page_size=100)
        self.assertEqual(count, 1)
        self.assertEqual(ids, [rejected.pk])

    def test_state_filter_still_matches_region_and_the_json_key(self):
        for value in ["Virginia", "VA"]:
            with self.subTest(state=value):
                _, count = self._ids(state=value, page_size=100)
                self.assertEqual(count, len(self.filler) + 1)

    def test_archived_mode_still_scopes_to_the_recycle_bin(self):
        # Only a reviewed contribution can be archived (#89).
        archived = self._contribution(
            town="Archivedton", status=Contribution.STATUS_REJECTED
        )
        # ContributionRecycleBin is a plain Model, not TimestampedModel -- it
        # carries archived_by/archived_at instead of created_by/modified_by.
        ContributionRecycleBin.objects.create(
            contribution=archived,
            archived_by=self.editor,
            reason="Duplicate",
        )

        live, _ = self._ids(page_size=100)
        binned, _ = self._ids(mode="archived", page_size=100)

        self.assertNotIn(archived.pk, live)
        self.assertEqual(binned, [archived.pk])

    def test_filters_do_not_leak_across_collections(self):
        other = Collection.objects.create(
            name="Maryland Collection",
            region=Region.objects.create(
                name="Maryland", abbrev="MD", region_tier="STATE",
                created_by=self.admin, modified_by=self.admin,
            ),
            created_by=self.admin,
            modified_by=self.admin,
        )
        hidden = self._contribution(town="Farmville", collection=other)

        ids, _ = self._ids(q="farm", page_size=100)

        self.assertNotIn(hidden.pk, ids)
        self.assertEqual(ids, [self.farmville.pk])

    def test_an_editor_without_an_assignment_sees_nothing(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        stranger.groups.add(Group.objects.get(name="Editors"))

        self.client.force_authenticate(stranger)
        response = self.client.get(URL, {"mode": "editor", "q": "farm"})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data.get("count"), 0)
