"""
Tests for cover-contribution materialization on approval.

Run from the backend repo root (with the project venv active and DATABASE_URL
set if the project requires it):

    python manage.py test common.tests.test_cover_contribution_apply -v 2

Expected exit code 0.

These cover the change that lets a cover Contribution flow through the same
approve workflow as a marking: a draft cover lives only as a Contribution, and
on editor approval a Cover + CoverMarking (and child rows) are materialized --
mirroring how a marking Contribution materializes a Marking.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from common.contribution_apply import (
    ContributionApplyError,
    apply_contribution_to_catalog,
    apply_cover_contribution_to_catalog,
)
from common.api.v2.serializers import ContributionDetailSerializer, ContributionListSerializer
from common.models import (
    Citation,
    Collection,
    Color,
    Contribution,
    Cover,
    CoverMarking,
    CoverVersion,
    DateSeen,
    Image,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    SubmissionTransaction,
)

User = get_user_model()


def _make_user(username):
    return User.objects.create_user(username=username, password="pw")


def _make_collection(user, name="Virginia", abbrev="VA"):
    region = Region.objects.create(
        name=name,
        abbrev=abbrev,
        region_tier="STATE",
        created_by=user,
        modified_by=user,
    )
    return Collection.objects.create(
        name=name,
        region=region,
        created_by=user,
        modified_by=user,
    )


def _make_parent_marking(user, region=None, town="Richmond"):
    color, _created = Color.objects.get_or_create(
        name="Black",
        defaults={"created_by": user, "modified_by": user},
    )
    po = PostOffice.objects.create(name=town, created_by=user, modified_by=user)
    if region is not None:
        PostOfficeRegion.objects.create(
            post_office=po,
            region=region,
            created_by=user,
            modified_by=user,
        )
    # is_manuscript=True keeps shape/lettering/is_irreg null (satisfies the
    # marking_manuscript_consistency check constraint without extra fixtures).
    return Marking.objects.create(
        type="TOWNMARK",
        inscription_txt="{} {}".format(town.upper(), region.abbrev if region else ""),
        is_manuscript=True,
        color=color,
        post_office=po,
        created_by=user,
        modified_by=user,
    )


def _cover_submitted_data(parent_marking, **overrides):
    """Build a cover Contribution.submitted_data blob shaped like the one the
    v2 ContributionSubmitView writes for a cover draft."""
    sd = {
        "submission_kind": "cover",
        "entity_type": "cover",
        "type": "FC",
        "parent_marking_id": parent_marking.pk,
        "marking_id": parent_marking.pk,
        "state": "VA",
        "cover_date": "1850-06-01",
        "cover_granularity": "DAY",
        "is_institutional": "false",
        "is_backstamp": "true",
        "contributor_comment": "Found in an estate sale.",
        "cover_image_metas": [
            {
                "storage_filename": "va/abc123.jpg",
                "original_filename": "front.jpg",
                "file_checksum": "deadbeef",
                "mime_type": "image/jpeg",
                "image_width": 800,
                "image_height": 600,
                "file_size_bytes": 12345,
            }
        ],
        "cover_image_tags": ["photograph"],
    }
    sd.update(overrides)
    return sd


def _make_cover_contribution(user, submitted_data, collection, status=Contribution.STATUS_PENDING):
    return Contribution.objects.create(
        contributor=user,
        collection=collection,
        submitted_data=submitted_data,
        status=status,
        created_by=user,
        modified_by=user,
    )


def _make_reference_work(user, code="ASCC1", title="A Catalog"):
    return ReferenceWork.objects.create(
        code=code,
        title=title,
        authorship="Author",
        publisher="Pub",
        publication_year=1900,
        created_by=user,
        modified_by=user,
    )


class CoverContributionApplyFunctionTests(TestCase):
    """Direct tests of the materialization helper (no HTTP / permissions)."""

    def setUp(self):
        self.user = _make_user("contributor")
        self.collection = _make_collection(self.user)
        self.parent = _make_parent_marking(self.user, self.collection.region)
        self.parent_reference = _make_reference_work(self.user, "ASCC1")
        Citation.objects.create(
            reference_work=self.parent_reference,
            subject_type="MARKING",
            subject_id=self.parent.pk,
            citation_detail="p. 1",
            created_by=self.user,
            modified_by=self.user,
        )

    def test_materializes_cover_and_children(self):
        rw = self.parent_reference
        sd = _cover_submitted_data(
            self.parent,
            catalog_code="ASCC1-VA-C0001",
            reference_work_ids=[rw.pk],
            reference_work_details=[{"reference_work_id": rw.pk, "page_number": "42"}],
        )
        contrib = _make_cover_contribution(self.user, sd, self.collection)

        result = apply_cover_contribution_to_catalog(contrib)

        self.assertEqual(result["kind"], "cover")
        cover = result["cover"]
        cover_marking = result["cover_marking"]
        self.assertEqual(result["parent_marking"].pk, self.parent.pk)

        # Cover row
        self.assertEqual(Cover.objects.count(), 1)
        self.assertEqual(cover.type, "FC")
        self.assertEqual(cover.code, "ASCC1-VA-C0001")

        # CoverMarking row: approved, linked to parent, reviewer NOT set here
        # (the approve view backfills the approving editor's identity).
        self.assertEqual(cover_marking.review_status, CoverMarking.REVIEW_APPROVED)
        self.assertIsNotNone(cover_marking.reviewed_at)
        self.assertIsNone(cover_marking.reviewer_id)
        self.assertEqual(cover_marking.marking_id, self.parent.pk)
        self.assertTrue(cover_marking.is_backstamp)

        # DateSeen child (COVER subject)
        ds = DateSeen.objects.get(subject_type="COVER", subject_id=cover.pk)
        self.assertEqual(ds.granularity, "DAY")
        self.assertEqual(ds.date_year, 1850)
        self.assertEqual(ds.date_month, 6)
        self.assertEqual(ds.date_day, 1)

        # Image child (COVER subject) with a cover-valid view
        img = Image.objects.get(subject_type="COVER", subject_id=cover.pk)
        self.assertEqual(img.image_view, "FRONT")

        # Citation child (COVER subject)
        self.assertEqual(
            Citation.objects.filter(subject_type="COVER", subject_id=cover.pk).count(),
            1,
        )

    def test_display_submitter_name_opt_in_flows_to_cover(self):
        # The submitter's opt-in choice rides submitted_data and must land on
        # the materialized Cover so the public detail page can honor it.
        sd = _cover_submitted_data(self.parent, display_submitter_name="true")
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        cover = apply_cover_contribution_to_catalog(contrib)["cover"]
        self.assertTrue(cover.display_submitter_name)

    def test_display_submitter_name_defaults_false_when_absent(self):
        # Privacy by default: no opt-in key -> name stays hidden.
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        cover = apply_cover_contribution_to_catalog(contrib)["cover"]
        self.assertFalse(cover.display_submitter_name)

    def test_unknown_cover_date_creates_no_date_seen(self):
        sd = _cover_submitted_data(
            self.parent,
            cover_date="",
            cover_granularity="",
            cover_date_unknown="true",
        )
        contrib = _make_cover_contribution(self.user, sd, self.collection)

        cover = apply_cover_contribution_to_catalog(contrib)["cover"]

        self.assertFalse(
            DateSeen.objects.filter(
                subject_type=DateSeen.SUBJECT_COVER,
                subject_id=cover.pk,
            ).exists()
        )

    def test_partial_cover_date_components_materialize(self):
        cases = [
            ({"cover_date_year": "1850"}, "YEAR", "1850-01-01", 1850, None, None),
            ({"cover_date_year": "1850", "cover_date_month": "6"}, "MONTH", "1850-06-01", 1850, 6, None),
            (
                {"cover_date_year": "1850", "cover_date_month": "6", "cover_date_day": "14"},
                "DAY",
                "1850-06-14",
                1850,
                6,
                14,
            ),
            ({"cover_date_month": "6"}, "MONTH_ONLY", None, None, 6, None),
            ({"cover_date_day": "14"}, "DAY_ONLY", None, None, None, 14),
            ({"cover_date_year": "1850", "cover_date_day": "14"}, "YEAR_DAY", None, 1850, None, 14),
            ({"cover_date_month": "6", "cover_date_day": "14"}, "MONTH_DAY", None, None, 6, 14),
        ]

        for fields, granularity, stored_date, year, month, day in cases:
            with self.subTest(granularity=granularity):
                sd = _cover_submitted_data(
                    self.parent,
                    cover_date="",
                    cover_granularity="",
                    **fields,
                )
                contrib = _make_cover_contribution(self.user, sd, self.collection)
                cover = apply_cover_contribution_to_catalog(contrib)["cover"]
                ds = DateSeen.objects.get(
                    subject_type=DateSeen.SUBJECT_COVER,
                    subject_id=cover.pk,
                )
                self.assertEqual(ds.granularity, granularity)
                self.assertEqual(ds.date.isoformat() if ds.date else None, stored_date)
                self.assertEqual(ds.date_year, year)
                self.assertEqual(ds.date_month, month)
                self.assertEqual(ds.date_day, day)

    def test_description_flows_to_cover(self):
        # #39: the submitter's free-text description rides submitted_data and
        # must land on the materialized Cover for the public detail page.
        sd = _cover_submitted_data(
            self.parent, description="  Blue CDS, light cover toning.  "
        )
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        cover = apply_cover_contribution_to_catalog(contrib)["cover"]
        self.assertEqual(cover.description, "Blue CDS, light cover toning.")

    def test_description_defaults_empty_when_absent(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        cover = apply_cover_contribution_to_catalog(contrib)["cover"]
        self.assertEqual(cover.description, "")

    def test_cover_code_increments_for_same_reference_region_prefix(self):
        first = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                self.parent,
                catalog_code="ASCC1-VA-C0001",
                reference_work_ids=[self.parent_reference.pk],
            ),
            self.collection,
        )
        second = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                self.parent,
                catalog_code="ASCC1-VA-C0002",
                reference_work_ids=[self.parent_reference.pk],
            ),
            self.collection,
        )

        first_cover = apply_cover_contribution_to_catalog(first)["cover"]
        second_cover = apply_cover_contribution_to_catalog(second)["cover"]

        self.assertEqual(first_cover.code, "ASCC1-VA-C0001")
        self.assertEqual(second_cover.code, "ASCC1-VA-C0002")

    def test_cover_code_sequence_resets_for_different_region_prefix(self):
        other_collection = _make_collection(self.user, name="New York", abbrev="NY")
        other_parent = _make_parent_marking(
            self.user,
            other_collection.region,
            town="Albany",
        )
        Citation.objects.create(
            reference_work=self.parent_reference,
            subject_type="MARKING",
            subject_id=other_parent.pk,
            citation_detail="p. 2",
            created_by=self.user,
            modified_by=self.user,
        )

        va_contrib = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                self.parent,
                catalog_code="ASCC1-VA-C0001",
                reference_work_ids=[self.parent_reference.pk],
            ),
            self.collection,
        )
        ny_contrib = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                other_parent,
                catalog_code="ASCC1-NY-C0001",
                reference_work_ids=[self.parent_reference.pk],
                state="NY",
            ),
            other_collection,
        )

        va_cover = apply_cover_contribution_to_catalog(va_contrib)["cover"]
        ny_cover = apply_cover_contribution_to_catalog(ny_contrib)["cover"]

        self.assertEqual(va_cover.code, "ASCC1-VA-C0001")
        self.assertEqual(ny_cover.code, "ASCC1-NY-C0001")

    def test_cover_code_sequence_resets_for_different_reference_prefix(self):
        other_reference = _make_reference_work(
            self.user,
            code="VPHC1",
            title="Virginia Postal History Catalog",
        )
        ascc_contrib = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                self.parent,
                catalog_code="ASCC1-VA-C0001",
                reference_work_ids=[self.parent_reference.pk],
            ),
            self.collection,
        )
        vphc_contrib = _make_cover_contribution(
            self.user,
            _cover_submitted_data(
                self.parent,
                catalog_code="VPHC1-VA-C0001",
                reference_work_ids=[other_reference.pk],
            ),
            self.collection,
        )

        ascc_cover = apply_cover_contribution_to_catalog(ascc_contrib)["cover"]
        vphc_cover = apply_cover_contribution_to_catalog(vphc_contrib)["cover"]

        self.assertEqual(ascc_cover.code, "ASCC1-VA-C0001")
        self.assertEqual(vphc_cover.code, "VPHC1-VA-C0001")

    def test_image_view_is_cover_valid_never_full(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        result = apply_cover_contribution_to_catalog(contrib)
        cover = result["cover"]
        views = set(
            Image.objects.filter(subject_type="COVER", subject_id=cover.pk).values_list(
                "image_view", flat=True
            )
        )
        self.assertEqual(views, {"FRONT"})
        self.assertNotIn("FULL", views)

    def test_missing_parent_marking_raises(self):
        sd = _cover_submitted_data(self.parent)
        sd.pop("parent_marking_id")
        sd.pop("marking_id")
        sd.pop("marking", None)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        with self.assertRaises(ContributionApplyError) as ctx:
            apply_cover_contribution_to_catalog(contrib)
        self.assertIn("parent_marking_id", str(ctx.exception))

    def test_unknown_parent_marking_raises(self):
        sd = _cover_submitted_data(self.parent, parent_marking_id=999999, marking_id=999999)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        with self.assertRaises(ContributionApplyError) as ctx:
            apply_cover_contribution_to_catalog(contrib)
        self.assertIn("Unknown parent marking id", str(ctx.exception))

    def test_requires_at_least_one_image(self):
        sd = _cover_submitted_data(self.parent, cover_image_metas=[])
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        with self.assertRaises(ContributionApplyError) as ctx:
            apply_cover_contribution_to_catalog(contrib)
        self.assertIn("image", str(ctx.exception).lower())

    def test_dispatch_returns_dict_for_cover(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        result = apply_contribution_to_catalog(contrib)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("kind"), "cover")

    def test_marking_dispatch_still_returns_marking(self):
        # Regression guard: the cover dispatch must not break the marking path.
        Region.objects.create(
            name="Maryland",
            abbrev="MD",
            region_tier="STATE",
            created_by=self.user,
            modified_by=self.user,
        )
        # Supply an explicit color so this dispatch regression also exercises
        # the normal color resolution path.
        color = Color.objects.create(
            name="MarkingColor", created_by=self.user, modified_by=self.user
        )
        sd = {
            "submission_kind": "marking",
            "type": "TOWNMARK",
            "state": "MD",
            "town": "Baltimore",
            "inscription_txt": "BALTIMORE MD",
            "is_manuscript": "true",
            "color_id": color.pk,
            "marking_image_metas": [{"storage_filename": "md/x.jpg"}],
        }
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        result = apply_contribution_to_catalog(contrib)
        self.assertIsInstance(result, Marking)

    def test_marking_create_allows_non_manuscript_without_shape_or_color(self):
        sd = {
            "submission_kind": "marking",
            "type": "TOWNMARK",
            "state": "VA",
            "town": "Richmond",
            "inscription_txt": "RICHMOND VA",
            "is_manuscript": "false",
            "is_irreg": "false",
            "marking_image_metas": [{"storage_filename": "va/no-shape.jpg"}],
        }
        contrib = _make_cover_contribution(self.user, sd, self.collection)

        marking = apply_contribution_to_catalog(contrib)

        self.assertIsInstance(marking, Marking)
        self.assertFalse(marking.is_manuscript)
        self.assertIsNone(marking.shape_id)
        self.assertIsNone(marking.color_id)
        self.assertFalse(marking.is_irreg)

    def test_marking_edit_can_clear_color_without_requiring_shape(self):
        color = Color.objects.create(
            name="EditColor",
            created_by=self.user,
            modified_by=self.user,
        )
        marking = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=False,
            is_irreg=False,
            color=color,
            post_office=self.parent.post_office,
            created_by=self.user,
            modified_by=self.user,
        )
        sd = {
            "submission_kind": "marking",
            "edit_marking_id": marking.pk,
            "type": "TOWNMARK",
            "state": "VA",
            "town": "Richmond",
            "inscription_txt": "RICHMOND VA",
            "is_manuscript": "false",
            "is_irreg": "false",
            "color_id": "",
            "marking_image_metas": [{"storage_filename": "va/edit-clear-color.jpg"}],
        }
        contrib = _make_cover_contribution(self.user, sd, self.collection)

        updated = apply_contribution_to_catalog(contrib)

        self.assertEqual(updated.pk, marking.pk)
        self.assertIsNone(updated.shape_id)
        self.assertIsNone(updated.color_id)


class CoverContributionApproveEndpointTests(APITestCase):
    """End-to-end tests of POST /contributions/<pk>/approve/ for covers.

    A superuser bypasses the editor-assignment filter for action endpoints
    (ContributionViewSet.get_queryset), so no CollectionAssignment is needed.
    """

    def setUp(self):
        self.contributor = _make_user("submitter")
        self.editor = User.objects.create_superuser(username="editor", password="pw")
        self.collection = _make_collection(self.contributor)
        self.parent = _make_parent_marking(self.contributor, self.collection.region)
        self.parent_reference = _make_reference_work(self.contributor, "ASCC1")
        Citation.objects.create(
            reference_work=self.parent_reference,
            subject_type="MARKING",
            subject_id=self.parent.pk,
            citation_detail="p. 1",
            created_by=self.contributor,
            modified_by=self.contributor,
        )

    def _approve_url(self, pk):
        return "/api/v2/contributions/{}/approve/".format(pk)

    def _suggestion_url(self, pk):
        return "/api/v2/contributions/{}/catalog-code-suggestion/".format(pk)

    def test_rejected_cover_contribution_can_be_saved_again(self):
        sd = _cover_submitted_data(self.parent, cover_date="1850-06-01")
        contrib = _make_cover_contribution(
            self.contributor,
            sd,
            self.collection,
            status=Contribution.STATUS_REJECTED,
        )
        self.client.force_authenticate(self.contributor)

        resp = self.client.post(
            "/api/v2/contributions/",
            {
                "edit_contribution_id": contrib.pk,
                "submission_kind": "cover",
                "save_as_draft": "true",
                "state": "VA",
                "type": "FC",
                "parent_marking_id": self.parent.pk,
                "marking_id": self.parent.pk,
                "cover_date": "1850-06-02",
                "cover_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        contrib.refresh_from_db()
        self.assertEqual(contrib.status, Contribution.STATUS_REJECTED)
        self.assertEqual(contrib.submitted_data["cover_date"], "1850-06-02")

    def test_rejected_cover_contribution_can_be_resubmitted(self):
        sd = _cover_submitted_data(self.parent, cover_date="1850-06-01")
        contrib = _make_cover_contribution(
            self.contributor,
            sd,
            self.collection,
            status=Contribution.STATUS_REJECTED,
        )
        self.client.force_authenticate(self.contributor)

        resp = self.client.post(
            "/api/v2/contributions/",
            {
                "edit_contribution_id": contrib.pk,
                "submission_kind": "cover",
                "state": "VA",
                "type": "FC",
                "parent_marking_id": self.parent.pk,
                "marking_id": self.parent.pk,
                "cover_date": "1850-06-03",
                "cover_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        contrib.refresh_from_db()
        self.assertEqual(contrib.status, Contribution.STATUS_PENDING)
        self.assertEqual(contrib.submitted_data["cover_date"], "1850-06-03")

    def test_pending_cover_contribution_can_be_updated_before_review(self):
        sd = _cover_submitted_data(self.parent, cover_date="1850-06-01")
        contrib = _make_cover_contribution(
            self.contributor,
            sd,
            self.collection,
            status=Contribution.STATUS_PENDING,
        )
        self.client.force_authenticate(self.contributor)

        resp = self.client.post(
            "/api/v2/contributions/",
            {
                "edit_contribution_id": contrib.pk,
                "submission_kind": "cover",
                "state": "VA",
                "type": "FC",
                "parent_marking_id": self.parent.pk,
                "marking_id": self.parent.pk,
                "cover_date": "1850-06-04",
                "cover_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        contrib.refresh_from_db()
        self.assertEqual(contrib.status, Contribution.STATUS_PENDING)
        self.assertEqual(contrib.submitted_data["cover_date"], "1850-06-04")

    def test_draft_cover_edit_does_not_supersede_prior_rows(self):
        cover = Cover.objects.create(
            code="ASCC1-VA-C0001",
            type="FC",
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        cover_marking = CoverMarking.objects.create(
            cover=cover,
            marking=self.parent,
            review_status=CoverMarking.REVIEW_APPROVED,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        original = Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            submitted_data=_cover_submitted_data(
                self.parent,
                cover_id=cover.pk,
                cover_marking_id=cover_marking.pk,
            ),
            status=Contribution.STATUS_APPROVED,
            marking=self.parent,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        self.client.force_authenticate(self.contributor)

        resp = self.client.post(
            "/api/v2/contributions/",
            {
                "submission_kind": "cover",
                "save_as_draft": "true",
                "edit_cover_id": cover.pk,
                "edit_cover_marking_id": cover_marking.pk,
                "state": "VA",
                "type": "FC",
                "parent_marking_id": self.parent.pk,
                "marking_id": self.parent.pk,
                "cover_date": "1850-06-02",
                "cover_granularity": "DAY",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(Contribution.objects.filter(pk=original.pk).exists())
        draft = Contribution.objects.get(pk=resp.data["id"])
        self.assertEqual(draft.status, Contribution.STATUS_DRAFT)
        self.assertEqual(resp.data["cover_id"], cover.pk)
        self.assertEqual(ContributionListSerializer(draft).data["cover_id"], cover.pk)
        self.assertFalse(
            SubmissionTransaction.objects.filter(
                action=SubmissionTransaction.ACTION_CONTRIBUTION_SUPERSEDED,
                cover=cover,
            ).exists()
        )

    def test_approve_endpoint_materializes_and_records(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.contributor, sd, self.collection)
        self.client.force_authenticate(self.editor)

        resp = self.client.post(
            self._approve_url(contrib.pk), {"review_notes": "looks good"}, format="json"
        )

        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))
        self.assertIn("coverId", resp.data)
        cover_id = resp.data["coverId"]
        cover = Cover.objects.get(pk=cover_id)
        self.assertEqual(cover.code, "ASCC1-VA-C0001")

        contrib.refresh_from_db()
        self.assertEqual(contrib.status, Contribution.STATUS_APPROVED)
        self.assertEqual(contrib.marking_id, self.parent.pk)
        self.assertEqual(contrib.submitted_data.get("cover_marking_id"),
                         CoverMarking.objects.get(cover_id=cover_id).pk)
        list_data = ContributionListSerializer(contrib).data
        detail_data = ContributionDetailSerializer(contrib).data
        self.assertEqual(list_data["cover_id"], cover_id)
        self.assertEqual(detail_data["cover_id"], cover_id)

        cover_marking = CoverMarking.objects.get(cover_id=cover_id)
        self.assertEqual(cover_marking.review_status, CoverMarking.REVIEW_APPROVED)
        self.assertEqual(cover_marking.reviewer_id, self.editor.id)
        self.assertEqual(cover_marking.review_notes, "looks good")

        self.assertTrue(
            SubmissionTransaction.objects.filter(
                cover_id=cover_id, action=SubmissionTransaction.ACTION_APPROVE
            ).exists()
        )
        self.assertTrue(CoverVersion.objects.filter(cover_id=cover_id).exists())

    def test_approve_cover_edit_supersedes_prior_same_contributor_cover_row(self):
        cover = Cover.objects.create(
            code="ASCC1-VA-C0001",
            type="FC",
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        cover_marking = CoverMarking.objects.create(
            cover=cover,
            marking=self.parent,
            review_status=CoverMarking.REVIEW_APPROVED,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        original = Contribution.objects.create(
            contributor=self.contributor,
            collection=self.collection,
            submitted_data=_cover_submitted_data(
                self.parent,
                cover_id=cover.pk,
                cover_marking_id=cover_marking.pk,
            ),
            status=Contribution.STATUS_APPROVED,
            marking=self.parent,
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        edit = _make_cover_contribution(
            self.contributor,
            _cover_submitted_data(
                self.parent,
                edit_cover_id=cover.pk,
                edit_cover_marking_id=cover_marking.pk,
                cover_date="1850-06-02",
            ),
            self.collection,
        )
        self.client.force_authenticate(self.editor)

        resp = self.client.post(
            self._approve_url(edit.pk),
            {"review_notes": "updated"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["coverId"], cover.pk)
        self.assertFalse(Contribution.objects.filter(pk=original.pk).exists())
        edit.refresh_from_db()
        self.assertEqual(edit.status, Contribution.STATUS_APPROVED)
        self.assertEqual(edit.marking_id, self.parent.pk)
        self.assertEqual(edit.submitted_data["cover_id"], cover.pk)
        tombstone = SubmissionTransaction.objects.get(
            action=SubmissionTransaction.ACTION_CONTRIBUTION_SUPERSEDED,
            cover=cover,
        )
        self.assertEqual(tombstone.before_payload["contribution_id"], original.pk)
        self.assertEqual(
            tombstone.extra_payload["superseded_by_contribution_id"],
            edit.pk,
        )

    def test_contribution_no_longer_pending_after_approval(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.contributor, sd, self.collection)
        self.client.force_authenticate(self.editor)

        resp = self.client.post(self._approve_url(contrib.pk), {"review_notes": ""}, format="json")
        self.assertEqual(resp.status_code, 200, getattr(resp, "data", resp))

        contrib.refresh_from_db()
        self.assertNotEqual(contrib.status, Contribution.STATUS_PENDING)

    def test_catalog_code_suggestion_persists_on_pending_cover(self):
        sd = _cover_submitted_data(self.parent)
        contrib = _make_cover_contribution(self.contributor, sd, self.collection)
        self.client.force_authenticate(self.editor)

        resp = self.client.post(self._suggestion_url(contrib.pk), {}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["catalog_code"], "ASCC1-VA-C0001")
        contrib.refresh_from_db()
        self.assertEqual(contrib.submitted_data["catalog_code"], "ASCC1-VA-C0001")

    def test_cover_suggestion_uses_newest_parent_marking_citation(self):
        newer = _make_reference_work(
            self.contributor,
            code="VPHC1",
            title="Virginia Postal History Catalog",
        )
        Citation.objects.create(
            reference_work=newer,
            subject_type="MARKING",
            subject_id=self.parent.pk,
            citation_detail="p. 99",
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        contrib = _make_cover_contribution(
            self.contributor,
            _cover_submitted_data(self.parent),
            self.collection,
        )
        self.client.force_authenticate(self.editor)

        resp = self.client.post(self._suggestion_url(contrib.pk), {}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["catalog_code"], "VPHC1-VA-C0001")

    def test_cover_suggestion_falls_back_to_apmc_without_reference(self):
        parent = _make_parent_marking(self.contributor, self.collection.region)
        contrib = _make_cover_contribution(
            self.contributor,
            _cover_submitted_data(parent),
            self.collection,
        )
        self.client.force_authenticate(self.editor)

        resp = self.client.post(self._suggestion_url(contrib.pk), {}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["catalog_code"], "APMC-VA-C0001")

    def test_approve_blocks_conflicting_catalog_code_override(self):
        Cover.objects.create(
            code="ASCC1-VA-C0001",
            type="FC",
            created_by=self.contributor,
            modified_by=self.contributor,
        )
        contrib = _make_cover_contribution(
            self.contributor,
            _cover_submitted_data(self.parent),
            self.collection,
        )
        self.client.force_authenticate(self.editor)

        resp = self.client.post(
            self._approve_url(contrib.pk),
            {"catalog_code": "ASCC1-VA-C0001"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("already exists", resp.data["detail"])
        contrib.refresh_from_db()
        self.assertEqual(contrib.status, Contribution.STATUS_PENDING)

    def test_direct_cover_suggestion_does_not_persist(self):
        self.client.force_authenticate(self.editor)

        resp = self.client.post(
            "/api/v2/catalog-code-suggestions/",
            {"subject_type": "COVER", "marking_id": self.parent.pk},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["catalog_code"], "ASCC1-VA-C0001")


class CoverSubmitterNameSerializerTests(TestCase):
    """CoverSerializer exposes the submitter name ONLY when opted in.

    This is the privacy boundary: opting out (the default) must never leak the
    name through the API, regardless of what the UI does.
    """

    def _cover(self, user, display):
        return Cover.objects.create(
            type="FC",
            display_submitter_name=display,
            created_by=user,
            modified_by=user,
        )

    def test_name_returned_when_opted_in(self):
        from common.api.v2.serializers import CoverSerializer

        user = User.objects.create_user(
            username="jsmith", password="pw", first_name="Jane", last_name="Smith"
        )
        data = CoverSerializer(self._cover(user, True)).data
        self.assertTrue(data["display_submitter_name"])
        self.assertEqual(data["submitter_name"], "Jane Smith")

    def test_name_hidden_when_opted_out(self):
        from common.api.v2.serializers import CoverSerializer

        user = User.objects.create_user(
            username="hidden", password="pw", first_name="Jane", last_name="Smith"
        )
        data = CoverSerializer(self._cover(user, False)).data
        self.assertFalse(data["display_submitter_name"])
        self.assertIsNone(data["submitter_name"])

    def test_falls_back_to_username_without_full_name(self):
        from common.api.v2.serializers import CoverSerializer

        user = User.objects.create_user(username="nameless", password="pw")
        data = CoverSerializer(self._cover(user, True)).data
        self.assertEqual(data["submitter_name"], "nameless")


class MarkingErdLrdApplyTests(TestCase):
    """Issue #21 Group C: manual ERD/LRD on Submit New / Edit Marking.

    The boundaries land as MARKING-subject DateSeen rows; the apply path must be
    additive so editing a catalog marking never destroys its imported dates.
    """

    def setUp(self):
        self.user = _make_user("erd-contributor")
        self.collection = _make_collection(self.user)  # Region "Virginia"/"VA"
        self.color = Color.objects.create(
            name="Black", created_by=self.user, modified_by=self.user
        )

    def _marking_sd(self, **overrides):
        sd = {
            "submission_kind": "marking",
            "type": "TOWNMARK",
            "state": "VA",
            "town": "Norfolk",
            "inscription_txt": "NORFOLK VA",
            "is_manuscript": "true",
            "color_id": self.color.pk,
            "marking_image_metas": [{"storage_filename": "va/x.jpg"}],
        }
        sd.update(overrides)
        return sd

    def _direct_dates(self, marking_id):
        return sorted(
            DateSeen.objects.filter(
                subject_type=DateSeen.SUBJECT_MARKING, subject_id=marking_id
            ).values_list("date", "granularity")
        )

    def test_new_marking_records_erd_and_lrd(self):
        sd = self._marking_sd(
            marking_erd="1845-01-01",
            marking_erd_granularity="YEAR",
            marking_lrd="1851-01-01",
            marking_lrd_granularity="YEAR",
        )
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        marking = apply_contribution_to_catalog(contrib)

        self.assertEqual(
            self._direct_dates(marking.pk),
            [(date(1845, 1, 1), "YEAR"), (date(1851, 1, 1), "YEAR")],
        )
        annotated = Marking.objects.with_date_range().get(pk=marking.pk)
        self.assertEqual(annotated.earliest_seen, date(1845, 1, 1))
        self.assertEqual(annotated.latest_seen, date(1851, 1, 1))

    def test_new_marking_records_partial_erd_and_lrd_components(self):
        sd = self._marking_sd(
            marking_erd_date_month="12",
            marking_erd_date_day="1",
            marking_lrd_date_year="1851",
            marking_lrd_date_day="14",
        )
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        marking = apply_contribution_to_catalog(contrib)

        rows = sorted(
            DateSeen.objects.filter(
                subject_type=DateSeen.SUBJECT_MARKING,
                subject_id=marking.pk,
            ).values_list(
                "date",
                "granularity",
                "date_year",
                "date_month",
                "date_day",
            ),
            key=lambda row: row[1],
        )

        self.assertEqual(
            rows,
            [
                (None, "MONTH_DAY", None, 12, 1),
                (None, "YEAR_DAY", 1851, None, 14),
            ],
        )
        annotated = Marking.objects.with_date_range().get(pk=marking.pk)
        self.assertIsNone(annotated.earliest_seen)
        self.assertIsNone(annotated.latest_seen)

    def test_invalid_erd_raises(self):
        sd = self._marking_sd(marking_erd="not-a-date")
        contrib = _make_cover_contribution(self.user, sd, self.collection)
        with self.assertRaises(ContributionApplyError):
            apply_contribution_to_catalog(contrib)

    def test_edit_without_dates_preserves_imported_history(self):
        # A bulk-imported marking carries several MARKING DateSeen rows. Editing
        # an unrelated field with no date fields must leave all of them intact.
        marking = apply_contribution_to_catalog(
            _make_cover_contribution(self.user, self._marking_sd(), self.collection)
        )
        for yr in (1845, 1848, 1851):
            DateSeen.objects.create(
                subject_type=DateSeen.SUBJECT_MARKING,
                subject_id=marking.pk,
                date=date(yr, 1, 1),
                granularity="YEAR",
                created_by=self.user,
                modified_by=self.user,
            )
        before = self._direct_dates(marking.pk)

        edit_sd = self._marking_sd(
            edit_marking_id=marking.pk, inscription_txt="NORFOLK VA (corrected)"
        )
        apply_contribution_to_catalog(
            _make_cover_contribution(self.user, edit_sd, self.collection)
        )

        self.assertEqual(self._direct_dates(marking.pk), before)

    def test_edit_extends_range_additively(self):
        marking = apply_contribution_to_catalog(
            _make_cover_contribution(
                self.user,
                self._marking_sd(
                    marking_erd="1845-01-01",
                    marking_erd_granularity="YEAR",
                    marking_lrd="1851-01-01",
                    marking_lrd_granularity="YEAR",
                ),
                self.collection,
            )
        )
        edit_sd = self._marking_sd(
            edit_marking_id=marking.pk,
            marking_lrd="1860-01-01",
            marking_lrd_granularity="YEAR",
        )
        apply_contribution_to_catalog(
            _make_cover_contribution(self.user, edit_sd, self.collection)
        )

        # The 1860 boundary is added; 1845 and 1851 remain (non-destructive).
        self.assertEqual(
            self._direct_dates(marking.pk),
            [(date(1845, 1, 1), "YEAR"), (date(1851, 1, 1), "YEAR"), (date(1860, 1, 1), "YEAR")],
        )
        annotated = Marking.objects.with_date_range().get(pk=marking.pk)
        self.assertEqual(annotated.latest_seen, date(1860, 1, 1))

    def test_manual_erd_merges_with_earlier_cover_date(self):
        # Michael's model: a catalog ERD is later corrected by real covers. A
        # cover dated before the manual ERD must pull the displayed earliest down.
        marking = apply_contribution_to_catalog(
            _make_cover_contribution(
                self.user, self._marking_sd(marking_erd="1850-01-01"), self.collection
            )
        )
        cover = Cover.objects.create(
            code="C-erd", created_by=self.user, modified_by=self.user
        )
        CoverMarking.objects.create(
            cover=cover, marking=marking, created_by=self.user, modified_by=self.user
        )
        DateSeen.objects.create(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=cover.pk,
            date=date(1845, 6, 14),
            granularity="DAY",
            created_by=self.user,
            modified_by=self.user,
        )

        annotated = Marking.objects.with_date_range().get(pk=marking.pk)
        self.assertEqual(annotated.earliest_seen, date(1845, 6, 14))
