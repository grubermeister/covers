"""Resolving a "state" value to a Region, on the WRITE paths.

Found live on 2026-08-19. Approving ten VPHC submissions on woco.dev created
ten DUPLICATE post offices attached to Accomack County, each with no state,
because _resolve_post_office resolved the payload's "VA" with

    Region.objects.filter(name__iexact=v).first()
    or Region.objects.filter(abbrev__iexact=v).first()

and the VPHC import gave all 141 county rows their state's abbrev (#103), so
the second arm matched 98 regions. Region.Meta.ordering is ['name'], so this
was not flaky -- it returned the alphabetically first county every time, and
would have done so for all 2,443 queued rows.

⚠️ THE FIXTURE IS THE TEST. A setUp with one clean Virginia row passes against
the broken code as happily as the fixed code and proves nothing. Every case
here builds the real shape: a STATE row plus COUNTY rows carrying the SAME
abbrev, with a county that sorts before the state alphabetically.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from common.contribution_apply import _resolve_post_office
from common.models import PostOffice, PostOfficeRegion, Region

User = get_user_model()


class StateTermResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("importer", password="pw")
        cls.virginia = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=cls.user, modified_by=cls.user)
        # "Accomack" sorts before "Virginia", which is exactly why the old code
        # picked it: Meta.ordering = ['name'].
        cls.accomack = Region.objects.create(
            code="USA-VA-ACC", name="Accomack", abbrev="VA", region_tier="COUNTY",
            created_by=cls.user, modified_by=cls.user)
        cls.campbell = Region.objects.create(
            code="USA-VA-CAM", name="Campbell", abbrev="VA", region_tier="COUNTY",
            created_by=cls.user, modified_by=cls.user)

    def test_abbrev_resolves_to_the_state_not_a_county(self):
        """The defect, stated directly."""
        self.assertEqual(Region.primary_for_state_term("VA"), self.virginia)

    def test_abbrev_is_case_insensitive(self):
        self.assertEqual(Region.primary_for_state_term("va"), self.virginia)

    def test_full_state_name_still_works(self):
        """The arm that was already correct must not be traded away."""
        self.assertEqual(Region.primary_for_state_term("Virginia"), self.virginia)

    def test_a_county_name_still_resolves_to_that_county(self):
        """Name matches anything -- "Accomack" is a legitimate lookup.

        Only the ABBREVIATION is restricted to primary jurisdictions.
        """
        self.assertEqual(Region.primary_for_state_term("Accomack"), self.accomack)

    def test_unknown_and_blank_terms_resolve_to_nothing(self):
        for value in ("", "   ", None, "Atlantis"):
            self.assertIsNone(Region.primary_for_state_term(value), value)

    def test_resolution_is_stable_across_calls(self):
        """A resolver whose answer can move is a resolver that will move."""
        answers = {Region.primary_for_state_term("VA").pk for _ in range(5)}
        self.assertEqual(len(answers), 1)


class ResolvePostOfficeTests(TestCase):
    """The consequence: what the wrong region did to the catalog."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("importer", password="pw")
        cls.virginia = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=cls.user, modified_by=cls.user)
        cls.accomack = Region.objects.create(
            code="USA-VA-ACC", name="Accomack", abbrev="VA", region_tier="COUNTY",
            created_by=cls.user, modified_by=cls.user)

        # ⚠️ The town is linked to the state and to ITS OWN county -- Campbell,
        # not Accomack. This detail is the whole test. An earlier version of
        # this fixture linked Lynchburg to Accomack as well, and every case
        # below then PASSED against the broken code: the old resolver picked
        # Accomack, looked for a Lynchburg linked to Accomack, and found one.
        # On woco.dev it found nothing and created the duplicate. A fixture that
        # cannot reproduce the defect is not a regression test.
        cls.campbell = Region.objects.create(
            code="USA-VA-CAM", name="Campbell", abbrev="VA", region_tier="COUNTY",
            created_by=cls.user, modified_by=cls.user)
        cls.lynchburg = PostOffice.objects.create(
            name="LYNCHBURG", created_by=cls.user, modified_by=cls.user)
        for region in (cls.virginia, cls.campbell):
            PostOfficeRegion.objects.create(
                post_office=cls.lynchburg, region=region,
                created_by=cls.user, modified_by=cls.user)

    def test_reuses_the_existing_town_instead_of_duplicating_it(self):
        """The live failure: this created "Lynchburg" beside "LYNCHBURG"."""
        before = PostOffice.objects.count()

        po = _resolve_post_office("VA", "LYNCHBURG", self.user)

        self.assertEqual(po, self.lynchburg)
        self.assertEqual(PostOffice.objects.count(), before)

    def test_the_resolved_town_still_has_its_state(self):
        """The symptom an editor sees: a record with a blank state.

        The duplicate was linked only to a county, and PostOffice.region
        excludes SUBREGION_TIERS, so the record displayed no state at all.
        """
        po = _resolve_post_office("VA", "LYNCHBURG", self.user)

        self.assertEqual(po.region.name, "Virginia")

    def test_two_approvals_for_one_town_create_one_post_office(self):
        """At 2,443 rows the old behaviour doubled every town in the catalog."""
        before = PostOffice.objects.count()

        _resolve_post_office("VA", "LYNCHBURG", self.user)
        _resolve_post_office("VA", "Lynchburg", self.user)

        self.assertEqual(PostOffice.objects.count(), before)

    def test_a_genuinely_new_town_is_still_created_against_the_state(self):
        """Auto-creation is intended behaviour; it just has to pick the state."""
        po = _resolve_post_office("VA", "NEW TOWN", self.user)

        self.assertEqual(po.name, "New Town")
        self.assertEqual(po.region.name, "Virginia")

    def test_an_unknown_state_is_still_a_hard_error(self):
        from common.contribution_apply import ContributionApplyError

        with self.assertRaises(ContributionApplyError):
            _resolve_post_office("Atlantis", "LYNCHBURG", self.user)
