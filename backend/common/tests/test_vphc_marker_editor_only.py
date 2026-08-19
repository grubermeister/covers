"""Issue #110 -- the ingest's `[VPHC: ...]` markers are editor-only.

The VPHC ingest appends bracketed markers to `desc` so the doubt survives
approval. `desc` is served by the AllowAny markings API and rendered on the
public record page, so approving published the internal flag vocabulary and the
sheet-cell references as public catalog text. Ian's call, 2026-08-19: keep the
doubt, make it editor-only.

Two halves, and both are pinned here:

  * contribution_apply strips the markers on approval, and the contribution
    keeps the original text -- that is what makes the strip non-destructive.
  * MarkingDetailSerializer.vphc_provenance gives editors the doubt back, gated.
    That gate is the security surface of the change: the endpoint is AllowAny,
    so an ungated blob would republish exactly what the strip removed.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from common.contribution_apply import apply_contribution_to_catalog
from common.models import (
    Collection,
    Contribution,
    Marking,
    PostOffice,
    PostOfficeRegion,
    Region,
)

User = get_user_model()

LEAD = "Virginia Postal History Catalog Wytheville #2 (T1:r6495)."
MARKER = "[VPHC: ambiguous]"
# apply_vphc_ledger appends this one separately from the crossexam marker
# (apply_vphc_ledger.py:454), so 118 of the 2,062 measured rows carry both.
TYPE_MARKER = (
    "[VPHC: device code 'unknown' not recognised — type defaulted to "
    "TOWNMARK, please correct]"
)
VPHC_BLOB = {"key": "va-wytheville-2", "flags": ["ambiguous"], "src": "T1:r6495"}


class VphcMarkerStripTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("importer", password="pw")
        self.region = Region.objects.create(
            name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.user, modified_by=self.user)
        self.collection = Collection.objects.create(
            name="Virginia", region=self.region, is_active=True,
            created_by=self.user, modified_by=self.user)
        self.post_office = PostOffice.objects.create(
            name="WYTHEVILLE", created_by=self.user, modified_by=self.user)
        PostOfficeRegion.objects.create(
            post_office=self.post_office, region=self.region,
            created_by=self.user, modified_by=self.user)

    def _contribution(self, **payload):
        base = {
            "type": "TOWNMARK", "state": "VA", "town": "WYTHEVILLE",
            "inscription_txt": "WYTHEVILLE VA", "is_manuscript": False,
            "is_irreg": False, "vphc": VPHC_BLOB,
            # _sync_images requires an image or an explicit declaration of none;
            # images are irrelevant to what these tests pin.
            "no_marking_image": True,
        }
        base.update(payload)
        return Contribution.objects.create(
            contributor=self.user, collection=self.collection,
            status=Contribution.STATUS_PENDING, submitted_data=base,
            created_by=self.user, modified_by=self.user)

    def _marking(self, desc):
        return Marking.objects.create(
            code="ASCC6-VA-M9001", type="TOWNMARK", catalog_txt="WYTHEVILLE/VA.",
            inscription_txt="WYTHEVILLE VA", is_manuscript=False, is_irreg=False,
            desc=desc, post_office=self.post_office,
            created_by=self.user, modified_by=self.user)

    def test_create_strips_both_markers(self):
        """The two-marker shape, which a trailing-only strip would half-miss."""
        contrib = self._contribution(
            desc="{} {} {}".format(LEAD, MARKER, TYPE_MARKER))

        marking = apply_contribution_to_catalog(contrib)

        self.assertEqual(marking.desc, LEAD)
        self.assertNotIn("[VPHC:", marking.desc)

    def test_contribution_keeps_the_original_text(self):
        """What makes the strip non-destructive rather than a deletion.

        The marker is the only record of why this row was flagged, so if the
        contribution did not keep it the doubt would be gone for good.
        """
        contrib = self._contribution(desc="{} {}".format(LEAD, MARKER))

        apply_contribution_to_catalog(contrib)

        contrib.refresh_from_db()
        self.assertIn(MARKER, contrib.submitted_data["desc"])

    def test_description_without_a_marker_is_untouched(self):
        contrib = self._contribution(desc="A perfectly ordinary description.")

        marking = apply_contribution_to_catalog(contrib)

        self.assertEqual(marking.desc, "A perfectly ordinary description.")

    def test_non_vphc_submission_keeps_bracketed_text(self):
        """Gated on the `vphc` key, not on the text.

        A contributor who types "[VPHC: ...]" into a description is writing
        their own words; the ingest is the only thing whose markers we remove.
        """
        payload = {
            "type": "TOWNMARK", "state": "VA", "town": "WYTHEVILLE",
            "inscription_txt": "WYTHEVILLE VA", "is_manuscript": False,
            "is_irreg": False, "no_marking_image": True,
            "desc": "Seen in a dealer list. {}".format(MARKER),
        }
        contrib = Contribution.objects.create(
            contributor=self.user, collection=self.collection,
            status=Contribution.STATUS_PENDING, submitted_data=payload,
            created_by=self.user, modified_by=self.user)

        marking = apply_contribution_to_catalog(contrib)

        self.assertIn(MARKER, marking.desc)

    def test_edit_strips_the_marker_and_keeps_the_rest(self):
        marking = self._marking("Original description.")
        contrib = self._contribution(
            edit_marking_id=marking.pk, desc="{} {}".format(LEAD, MARKER))

        apply_contribution_to_catalog(contrib)

        marking.refresh_from_db()
        self.assertEqual(marking.desc, LEAD)

    def test_marker_only_edit_does_not_clear_the_description(self):
        """The one shape where stripping could destroy data.

        An edit merges rather than replaces, so an explicitly empty value
        clears the stored description. A payload whose desc is nothing but a
        marker strips to "" -- which must not be read as "clear it", because
        the submission never spoke to the description at all.
        """
        marking = self._marking("A description worth keeping.")
        contrib = self._contribution(edit_marking_id=marking.pk, desc=MARKER)

        apply_contribution_to_catalog(contrib)

        marking.refresh_from_db()
        self.assertEqual(marking.desc, "A description worth keeping.")


class VphcProvenanceVisibilityTests(TestCase):
    """The gate on MarkingDetailSerializer.vphc_provenance.

    The markings endpoint is AllowAny. If this field leaked, it would republish
    the flag vocabulary the strip just removed -- in richer form than the desc
    marker carried.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pw")
        self.editor = User.objects.create_user(username="editor", password="pw")
        editors = Group.objects.create(name="Editors")
        editors.permissions.add(
            Permission.objects.get(codename="review_contribution"))
        self.editor.groups.add(editors)
        self.stranger = User.objects.create_user(username="stranger", password="pw")

        region = Region.objects.create(
            name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.admin, modified_by=self.admin)
        collection = Collection.objects.create(
            name="Virginia", region=region, is_active=True,
            created_by=self.admin, modified_by=self.admin)
        post_office = PostOffice.objects.create(
            name="WYTHEVILLE", created_by=self.admin, modified_by=self.admin)
        PostOfficeRegion.objects.create(
            post_office=post_office, region=region,
            created_by=self.admin, modified_by=self.admin)

        self.marking = Marking.objects.create(
            code="ASCC6-VA-M9002", type="TOWNMARK", catalog_txt="WYTHEVILLE/VA.",
            inscription_txt="WYTHEVILLE VA", is_manuscript=False, is_irreg=False,
            desc=LEAD, post_office=post_office,
            created_by=self.admin, modified_by=self.admin)
        Contribution.objects.create(
            contributor=self.admin, collection=collection, marking=self.marking,
            status=Contribution.STATUS_APPROVED,
            submitted_data={"vphc": VPHC_BLOB, "desc": "{} {}".format(LEAD, MARKER)},
            created_by=self.admin, modified_by=self.admin)

        self.url = "/api/v2/markings/{}/".format(self.marking.pk)

    def test_anonymous_sees_no_provenance_and_no_marker(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data.get("vphc_provenance"))
        # The whole point: nothing bracketed reaches the public payload.
        self.assertNotIn("[VPHC:", str(response.data))

    def test_unrelated_logged_in_user_sees_no_provenance(self):
        self.client.force_authenticate(self.stranger)

        response = self.client.get(self.url)

        self.assertIsNone(response.data.get("vphc_provenance"))

    def test_editor_sees_the_provenance(self):
        self.client.force_authenticate(self.editor)

        response = self.client.get(self.url)

        self.assertEqual(response.data.get("vphc_provenance"), VPHC_BLOB)
