"""
Issues #135 / #137 -- a cover draft on the editor dashboard must name its town.

Ian's two complaints were filed separately ("town name on dashboard screen",
"put the town and state before cover draft") but they are one defect with one
cause: a cover contribution has NO town in its submitted_data. CoverEdit never
sends one, and it must not -- _contribution_submitted_data_is_cover keys "this
is a cover" partly on the ABSENCE of a town, so denormalizing one into the blob
would reclassify every cover draft as a marking. The town belongs to the parent
marking and has to be resolved.

The rows therefore read "Cover draft - Folded Cover - 1850-06-01 - Marking #12":
no town at all, and the record TYPE leading the line an editor scans. Marking
contributions were never affected -- they already led with their location.

⭐ The query-count test is not hygiene, it is the acceptance criterion. Resolving
a parent per row is an N+1 on the busiest editor screen against a queue that has
been over 2,400 rows since the VPHC ingest, so "it renders the town" and "it
renders the town without melting the dashboard" are two different claims and
only the second one is worth shipping.

Run: uv run python backend/manage.py test common.tests.test_contribution_queue_cover_location
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from common.models import (
    Collection,
    CollectionAssignment,
    Color,
    Contribution,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)


User = get_user_model()

URL = "/api/v2/contributions/"


class CoverDraftLocationTests(TestCase):
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
        # The county link is the trap this fix has to survive: since the VPHC
        # ingest a VA/WV town is linked to its county as well as its state
        # (issue #103), and taking the first link you are handed reports the
        # county as the state.
        self.accomack = Region.objects.create(
            name="Accomack",
            abbrev="",
            region_tier="COUNTY",
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
        self.color, _ = Color.objects.get_or_create(
            name="Black",
            defaults={"created_by": self.admin, "modified_by": self.admin},
        )

    # -- fixtures ------------------------------------------------------------

    def _marking(self, town, *, regions=()):
        po = PostOffice.objects.create(
            name=town, created_by=self.admin, modified_by=self.admin
        )
        for region in regions:
            PostOfficeRegion.objects.create(
                post_office=po,
                region=region,
                created_by=self.admin,
                modified_by=self.admin,
            )
        # is_manuscript=True keeps shape/lettering/is_irreg null, which satisfies
        # marking_manuscript_consistency without extra fixtures.
        return Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="{} VA".format(town.upper()),
            is_manuscript=True,
            color=self.color,
            post_office=po,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _cover_draft(self, parent_marking_id, **overrides):
        sd = {
            "submission_kind": "cover",
            "type": "FC",
            "parent_marking_id": parent_marking_id,
            "marking_id": parent_marking_id,
            "cover_date": "1850-06-01",
            "cover_granularity": "DAY",
        }
        sd.update(overrides)
        return Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            status=Contribution.STATUS_PENDING,
            submitted_data=sd,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def _rows(self):
        self.client.force_authenticate(user=self.editor)
        response = self.client.get(URL, {"mode": "editor", "page_size": 100})
        self.assertEqual(response.status_code, 200)
        return response.json()["results"]

    # -- the asks ------------------------------------------------------------

    def test_cover_draft_leads_with_town_and_state_from_its_parent(self):
        """#135 + #137: the town is present, and it comes first."""
        marking = self._marking("Onancock", regions=[self.virginia])
        self._cover_draft(marking.pk)

        row = self._rows()[0]

        self.assertTrue(
            row["display_name"].startswith("Onancock, VA - "),
            "expected the location to lead the label, got {!r}".format(row["display_name"]),
        )
        # #137 is specifically about ORDER: the record type has to trail. A
        # label that merely contains the town somewhere still fails the ask.
        self.assertLess(
            row["display_name"].index("Onancock"),
            row["display_name"].index("Cover draft"),
            "town must precede the cover/draft label",
        )
        # #135: the row's own town field, which is what the dashboard sorts and
        # falls back to, must stop reporting "-".
        #
        # The casing is PostOffice.name's, not the shouty form the VPHC blob
        # uses -- a cover draft inherits the catalogue's spelling of the town
        # because that is the only place it has one.
        self.assertEqual(row["town_display"], "Onancock")
        self.assertEqual(row["state_display"], "VA")

    def test_county_link_is_not_mistaken_for_the_state(self):
        """Issue #103's trap, on a new code path (see setUp)."""
        marking = self._marking("Onancock", regions=[self.accomack, self.virginia])
        self._cover_draft(marking.pk)

        self.assertEqual(self._rows()[0]["state_display"], "VA")

    def test_a_state_on_the_draft_wins_over_the_parents(self):
        """What the submitter chose is not overwritten by an inference."""
        marking = self._marking("Onancock", regions=[self.virginia])
        self._cover_draft(marking.pk, state="WV")

        self.assertEqual(self._rows()[0]["state_display"], "WV")

    def test_an_unresolvable_parent_still_renders(self):
        """A draft whose marking is gone must degrade, never 500."""
        self._cover_draft(999_999)

        row = self._rows()[0]
        self.assertTrue(row["display_name"].startswith("Cover draft - "))
        self.assertEqual(row["town_display"], "-")

    def test_marking_contributions_are_untouched(self):
        """They already led with their location; this must not disturb them."""
        Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            status=Contribution.STATUS_PENDING,
            submitted_data={
                "submission_kind": "marking",
                "type": "TOWNMARK",
                "town": "RICHMOND",
                "state": "VA",
            },
            created_by=self.admin,
            modified_by=self.admin,
        )

        self.assertEqual(self._rows()[0]["display_name"], "RICHMOND, VA - TOWNMARK")

    def test_query_count_does_not_grow_with_the_page(self):
        """⭐ The acceptance criterion: resolve the page, not each row.

        Ten drafts across ten DISTINCT parent markings and post offices -- one
        shared parent would pass this by accident. The count is asserted against
        a single-row baseline rather than a hardcoded number so the test keeps
        meaning if unrelated queue work changes the fixed cost.

        ⛔ The baseline is taken from the SECOND request, not the first. Django
        caches a user's permissions on the user object, so request one pays two
        extra auth_permission queries that request two does not. Measuring the
        first request against a later one reports a 2-query *drop* and reads as
        a passing N+1 test failing, or worse, a failing one passing.
        """
        self.client.force_authenticate(user=self.editor)
        self._cover_draft(self._marking("Onancock", regions=[self.virginia]).pk)
        self._page_query_count()  # warm the permission cache; discard
        baseline = self._page_query_count()

        for i in range(9):
            self._cover_draft(self._marking("Town{}".format(i), regions=[self.virginia]).pk)

        self.assertEqual(
            self._page_query_count(),
            baseline,
            "the parent-marking lookup is running per row, not per page",
        )

    def _page_query_count(self):
        """Queries used to serve one full page of the editor queue."""
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(URL, {"mode": "editor", "page_size": 100})
            self.assertEqual(response.status_code, 200)
        return len(ctx)
