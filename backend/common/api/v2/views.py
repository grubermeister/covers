###################################################################################################
## WoCo Commons - API v2 Views (Phase 2 rewrite)
##
## Unified Marking model with type discriminator (TOWNMARK | RATEMARK | AUXMARK).
## CoverMarking carries placement; CoverValuation belongs to Cover.
## DateSeen, Image, and Citation are polymorphic over (subject_type, subject_id),
## each referencing COVER | MARKING.
###################################################################################################
from __future__ import annotations

import json
import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, ProgrammingError, transaction
from django.db.models import Min, Max, Q
from django.db.models.functions import ExtractYear
from django.http import Http404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import (
    AllowAny,
    BasePermission,
    IsAdminUser,
    IsAuthenticated,
)
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer

from common.contribution_apply import ContributionApplyError, _parse_int
from common.audit import (
    build_cover_snapshot,
    build_marking_snapshot,
    create_cover_version,
    create_marking_version,
    log_cover_removed,
    log_cover_restored,
    log_marking_removed,
    log_marking_restored,
    log_submission_transaction,
    restore_cover_from_snapshot,
    restore_marking_from_snapshot,
)
from common.filters import CoverMarkingFilter, MarkingListFilter
from common.models import (
    Citation,
    Collection,
    CollectionAssignment,
    Color,
    Contribution,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    CoverValuation,
    CoverVersion,
    DateSeen,
    FAQEntry,
    Image,
    Lettering,
    Marking,
    MarkingRecycleBin,
    MarkingVersion,
    PostOffice,
    ReferenceWork,
    Region,
    Shape,
    SubmissionTransaction,
)
from woco.pagination import MarkingListPagination

from .permissions import (
    REVIEW_CONTRIBUTION_PERM,
    CanManageReferenceWorks,
    CanReviewContribution,
    IsDraftOwner,
    IsEditorOrAdminWrite,
    _get_user_assigned_regions,
    _user_is_responsible_for_cover,
    _user_is_responsible_for_marking,
    user_assigned_collection_ids,
)
from .serializers import (
    CitationSerializer,
    CollectionSerializer,
    ColorSerializer,
    ContributionApproveRejectSerializer,
    ContributionDetailSerializer,
    ContributionListSerializer,
    CoverMarkingSerializer,
    CoverSerializer,
    CoverValuationSerializer,
    DateSeenSerializer,
    FAQEntrySerializer,
    ImageSerializer,
    LetteringSerializer,
    MarkingListSerializer,
    MarkingSerializer,
    PostOfficeSerializer,
    ReferenceWorkSerializer,
    RegionSerializer,
    ShapeSerializer,
)


User = get_user_model()


###################################################################################################
## Helpers
###################################################################################################
# _get_user_assigned_regions and _user_is_responsible_for_marking now live in
# .permissions (imported above) so serializers.py can reuse them without a
# circular import back into views.py.


class IsResponsibleForRegion(BasePermission):
    """
    Object-level write check for Marking-bound resources.
    Reads pass; writes require the user to be assigned to the marking's
    region (or be a superuser).
    """

    def has_permission(self, request, view):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return True
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.has_perm(REVIEW_CONTRIBUTION_PERM))
        )

    def has_object_permission(self, request, view, obj):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return True
        marking = obj if isinstance(obj, Marking) else getattr(obj, "marking", None)
        return _user_is_responsible_for_marking(request.user, marking)


def _marking_list_queryset():
    """Optimized queryset for Marking list-style endpoints with date-range annotations.

    Uses MarkingQuerySet.with_date_range so earliest_seen / latest_seen aggregate
    both directly-attached DateSeen rows (subject_type='MARKING') and
    cover-mediated DateSeen rows (subject_type='COVER' via cover_markings).
    """
    return Marking.objects.select_related(
        "post_office", "shape", "lettering", "color"
    ).prefetch_related(
        "post_office__post_office_regions__region"
    ).with_date_range()


###################################################################################################
## Lookup viewsets (read-only or simple CRUD)
###################################################################################################
class ColorViewSet(viewsets.ModelViewSet):
    queryset = Color.objects.all()
    serializer_class = ColorSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering = ["name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class RegionViewSet(viewsets.ModelViewSet):
    """Regions; supports ?assigned_only=true to scope to the user's Collections."""
    queryset = Region.objects.all().select_related("parent_region")
    serializer_class = RegionSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["region_tier", "parent_region"]
    search_fields = ["name", "abbrev"]
    ordering_fields = ["name", "abbrev", "established_date", "defunct_date", "created_date"]
    ordering = ["name"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("assigned_only", "").lower() != "true":
            return qs
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser:
            return qs
        return qs.filter(collection__editor_assignments__user=user).distinct()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if request.query_params.get("assigned_only", "").lower() == "true":
            response["Cache-Control"] = "no-store, private, max-age=0"
        return response

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class PostOfficeViewSet(viewsets.ModelViewSet):
    queryset = PostOffice.objects.all().prefetch_related(
        "post_office_regions__region"
    )
    serializer_class = PostOfficeSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {"post_office_regions__region": ["exact"]}
    search_fields = [
        "name",
        "post_office_regions__region__name",
        "post_office_regions__region__abbrev",
    ]
    ordering_fields = ["name", "created_date"]
    ordering = ["name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="town-options")
    def town_options(self, request):
        """Lightweight {town, state} payload for autocomplete controls."""
        rows = (
            PostOffice.objects.prefetch_related("post_office_regions__region")
            .exclude(name__isnull=True)
            .exclude(name__exact="")
            .values_list("name", "post_office_regions__region__name")
            .order_by("name", "post_office_regions__region__name")
            .distinct()
        )
        out = [
            {"town": (town or "").strip(), "state": (state or "").strip()}
            for town, state in rows
        ]
        return Response(out, status=status.HTTP_200_OK)


class LetteringViewSet(viewsets.ModelViewSet):
    queryset = Lettering.objects.all()
    serializer_class = LetteringSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["name", "created_date"]
    ordering = ["name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class ShapeViewSet(viewsets.ModelViewSet):
    queryset = Shape.objects.all()
    serializer_class = ShapeSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["name", "code", "created_date"]
    ordering = ["name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class ReferenceWorkViewSet(viewsets.ModelViewSet):
    """Reads: any authenticated user. Writes: Editors / Administrators."""
    queryset = ReferenceWork.objects.all()
    serializer_class = ReferenceWorkSerializer
    permission_classes = [CanManageReferenceWorks]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["publication_year"]
    search_fields = ["code", "title", "authorship", "publisher", "edition", "volume", "isbn"]
    ordering_fields = ["title", "publication_year", "created_date"]
    ordering = ["title"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class FAQEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only FAQ for the public SPA homepage."""
    queryset = FAQEntry.objects.filter(is_active=True).order_by("display_order", "faq_entry_id")
    serializer_class = FAQEntrySerializer
    permission_classes = [AllowAny]

    def list(self, request, *args, **kwargs):
        try:
            return super().list(request, *args, **kwargs)
        except ProgrammingError:
            return Response({"count": 0, "next": None, "previous": None, "results": []})


###################################################################################################
## Image (polymorphic over COVER | MARKING)
###################################################################################################
class ImageViewSet(viewsets.ModelViewSet):
    """
    Polymorphic image API. Filter by `?subject_type=MARKING&subject_id=<id>`
    or `?subject_type=COVER&subject_id=<id>`.
    """
    queryset = Image.objects.all().select_related("uploaded_by")
    serializer_class = ImageSerializer
    permission_classes = [IsEditorOrAdminWrite]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["subject_type", "subject_id", "image_view", "is_tracing"]
    ordering_fields = ["display_order", "created_date"]
    ordering = ["subject_type", "subject_id", "display_order"]

    def perform_create(self, serializer):
        # Image.uploaded_by is required (PROTECT FK); TimestampedModel also needs
        # created_by / modified_by. All three must be set on create.
        serializer.save(
            created_by=self.request.user,
            modified_by=self.request.user,
            uploaded_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    def perform_destroy(self, instance):
        # "Default image" is implicit: for a given (subject_type, subject_id),
        # the main image is whichever row has the lowest (display_order,
        # image_id) -- see MarkingSerializer.get_main_image. Tracings live in
        # the same set, they are not a separate subject. After deleting an
        # image, if no row in that subject still has display_order=0, promote
        # the next-lowest row so a stable default exists.
        subject_type = instance.subject_type
        subject_id = instance.subject_id
        with transaction.atomic():
            super().perform_destroy(instance)
            siblings = Image.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).order_by("display_order", "image_id")
            if not siblings.filter(display_order=0).exists():
                next_default = siblings.first()
                if next_default is not None:
                    next_default.display_order = 0
                    next_default.modified_by = self.request.user
                    next_default.save(update_fields=["display_order", "modified_by", "modified_date"])


###################################################################################################
## Citation (subject_type COVER | MARKING)
###################################################################################################
class CitationViewSet(viewsets.ModelViewSet):
    queryset = Citation.objects.all().select_related("reference_work")
    serializer_class = CitationSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["reference_work", "subject_type", "subject_id"]
    search_fields = ["citation_detail", "reference_work__title"]
    ordering_fields = ["reference_work", "subject_type", "subject_id", "created_date"]
    ordering = ["reference_work", "subject_type", "subject_id"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


###################################################################################################
## Cover, DateSeen, CoverValuation, CoverMarking
###################################################################################################
class CoverV2ViewSet(viewsets.ModelViewSet):
    queryset = Cover.objects.all().select_related("color")
    serializer_class = CoverSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["color", "type", "has_adhesive", "is_institutional"]
    ordering_fields = ["id", "code", "created_date"]
    ordering = ["id"]
    # No raw DELETE: removing a cover goes through the audited, reversible
    # POST /covers/<pk>/remove/ (recycle bin) action instead. Custom POST
    # actions (remove, restore, restore-version) are unaffected by this.
    http_method_names = ["get", "post", "put", "patch", "head", "options", "trace"]

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            # The default manager hides removed covers. Allow ONLY the editor
            # responsible for this cover (or a superuser) to load a removed
            # cover on the detail page so they can restore it. Detail (retrieve)
            # reads only; every other caller keeps getting 404.
            if self.action != "retrieve":
                raise
            cover = (
                Cover.all_objects
                .select_related("color")
                .filter(pk=self.kwargs[self.lookup_field])
                .first()
            )
            if cover and _user_is_responsible_for_cover(self.request.user, cover):
                self.check_object_permissions(self.request, cover)
                return cover
            raise

    def perform_create(self, serializer):
        cover = serializer.save(created_by=self.request.user, modified_by=self.request.user)
        after_snapshot = build_cover_snapshot(cover)
        txn = log_submission_transaction(
            action=SubmissionTransaction.ACTION_RECORD_CREATE,
            actor=self.request.user,
            cover=cover,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload={},
            after_payload=after_snapshot,
        )
        create_cover_version(cover, txn, self.request.user)

    def perform_update(self, serializer):
        before_snapshot = build_cover_snapshot(serializer.instance)
        cover = serializer.save(modified_by=self.request.user)
        after_snapshot = build_cover_snapshot(cover)
        txn = log_submission_transaction(
            action=SubmissionTransaction.ACTION_RECORD_UPDATE,
            actor=self.request.user,
            cover=cover,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload=before_snapshot,
            after_payload=after_snapshot,
        )
        create_cover_version(cover, txn, self.request.user)

    @action(detail=True, methods=["post"], url_path="remove", permission_classes=[IsAuthenticated])
    def remove(self, request, pk=None):
        """
        Soft-REMOVE this cover into the recycle bin. The Cover row is not
        mutated or deleted; a CoverRecycleBin sidecar row is created, which
        causes the default manager to hide it. Reversible via restore.
        Permitted for the editor responsible for the cover (via its linked
        markings' regions), or a superuser. Optional JSON body: {"reason": "..."}.
        """
        cover = Cover.all_objects.filter(pk=pk).select_related("color").first()
        if not cover:
            return Response({"detail": "Cover not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_cover(request.user, cover):
            return Response(
                {"detail": "You are not allowed to remove this cover."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if CoverRecycleBin.objects.filter(cover=cover).exists():
            return Response(
                {"detail": "Cover is already in the recycle bin."},
                status=status.HTTP_409_CONFLICT,
            )
        reason = ""
        if isinstance(request.data, dict):
            reason = (request.data.get("reason") or "").strip()
        with transaction.atomic():
            log_cover_removed(cover, request.user, reason)
            CoverRecycleBin.objects.create(
                cover=cover, removed_by=request.user, reason=reason
            )
        return Response(
            {"detail": "Cover removed.", "coverId": cover.pk},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="restore", permission_classes=[IsAuthenticated])
    def restore(self, request, pk=None):
        """
        Restore this cover from the recycle bin by deleting its CoverRecycleBin
        sidecar row. Permitted for the editor responsible for the cover (via its
        linked markings' regions), or a superuser.
        """
        cover = Cover.all_objects.filter(pk=pk).select_related("color").first()
        if not cover:
            return Response({"detail": "Cover not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_cover(request.user, cover):
            return Response(
                {"detail": "You are not allowed to restore this cover."},
                status=status.HTTP_403_FORBIDDEN,
            )
        entry = CoverRecycleBin.objects.filter(cover=cover).first()
        if not entry:
            return Response(
                {"detail": "Cover is not in the recycle bin."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            entry.delete()
            log_cover_restored(cover, request.user)
        return Response(
            {"detail": "Cover restored.", "coverId": cover.pk},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="recycle-bin", permission_classes=[IsAuthenticated])
    def recycle_bin(self, request):
        """
        List covers currently in the recycle bin (removed), scoped to the
        regions the editor is responsible for (derived from each cover's linked
        markings); superusers see all. Uses Cover.all_objects since the default
        manager hides removed rows.
        """
        user = request.user
        if not (user.is_superuser or user.has_perm(REVIEW_CONTRIBUTION_PERM)):
            return Response(
                {"detail": "You are not allowed to view the recycle bin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = (
            Cover.all_objects.filter(recycle_bin_entry__isnull=False)
            .select_related("color")
            .order_by("-recycle_bin_entry__removed_at")
        )
        if not user.is_superuser:
            assigned_regions = _get_user_assigned_regions(user)
            if assigned_regions.exists():
                qs = qs.filter(
                    cover_markings__marking__post_office__post_office_regions__region__in=assigned_regions
                ).distinct()
            else:
                qs = qs.none()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="changelog", permission_classes=[IsAuthenticated])
    def changelog(self, request, pk=None):
        # Use all_objects so a removed (recycle-binned) cover's history stays
        # viewable; the default manager would 404 it. Responsibility is checked
        # explicitly below.
        cover = Cover.all_objects.filter(pk=pk).first()
        if not cover:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_cover(request.user, cover):
            return Response(
                {"detail": "You are not allowed to view changelog for this record."},
                status=status.HTTP_403_FORBIDDEN,
            )

        def _summary(snap):
            snap = snap if isinstance(snap, dict) else {}
            return {
                "code": snap.get("code") or "",
                "type": snap.get("type") or "",
                "has_adhesive": bool(snap.get("has_adhesive")),
                "is_institutional": snap.get("is_institutional"),
                "color_id": snap.get("color_id"),
                "width": snap.get("width"),
                "height": snap.get("height"),
            }

        # Covers have no contribution FK link, so filter on the cover FK only.
        txns = list(
            SubmissionTransaction.objects.filter(cover=cover)
            .select_related("actor", "contribution")
            .order_by("-created_at", "-id")
            .distinct()
        )
        versions = list(
            CoverVersion.objects.filter(cover=cover)
            .select_related("created_by", "transaction")
            .order_by("-version_no")
        )
        version_no_by_txn_id = {
            v.transaction_id: v.version_no for v in versions if v.transaction_id is not None
        }
        txn_by_id = {txn.id: txn for txn in txns}
        action_labels = dict(SubmissionTransaction.ACTION_CHOICES)

        events = []
        for txn in txns:
            actor_name = None
            actor_email = None
            if txn.actor:
                actor_email = (getattr(txn.actor, "email", "") or "").strip() or None
                actor_name = (
                    txn.actor.get_username()
                    or actor_email
                    or str(txn.actor.pk)
                )
            events.append(
                {
                    "event_id": txn.id,
                    "transaction_uuid": str(txn.transaction_uuid),
                    "timestamp": txn.created_at,
                    "action": txn.action,
                    "action_label": action_labels.get(txn.action, txn.action.replace("_", " ").title()),
                    "actor": actor_name,
                    "actor_email": actor_email,
                    "source": txn.source,
                    "contribution_id": txn.contribution_id,
                    "version_no": version_no_by_txn_id.get(txn.id),
                    "diff": txn.diff_payload or [],
                    "summary": f"{action_labels.get(txn.action, txn.action)} by {actor_email or actor_name or 'system'}",
                }
            )

        approved_actions = {
            SubmissionTransaction.ACTION_APPROVE,
            SubmissionTransaction.ACTION_CATALOG_DIRECT_EDIT,
            SubmissionTransaction.ACTION_RESTORE_VERSION,
        }
        version_rows = []
        approved_version_rows = []
        for version in versions:
            created_by_name = None
            if version.created_by:
                created_by_name = (
                    version.created_by.get_username()
                    or getattr(version.created_by, "email", "")
                    or str(version.created_by.pk)
                )
            txn = txn_by_id.get(version.transaction_id) if version.transaction_id is not None else None
            txn_action = txn.action if txn else None
            row = {
                "version_no": version.version_no,
                "created_at": version.created_at,
                "created_by": created_by_name,
                "transaction_id": version.transaction_id,
                "action": txn_action,
                "action_label": (
                    action_labels.get(txn_action, str(txn_action).replace("_", " ").title())
                    if txn_action
                    else None
                ),
                "snapshot": _summary(version.snapshot),
            }
            version_rows.append(row)
            if txn_action in approved_actions:
                approved_version_rows.append(row)

        return Response(
            {
                "cover_id": cover.pk,
                "events": events,
                "versions": version_rows,
                "approved_versions": approved_version_rows,
            }
        )

    @action(detail=True, methods=["post"], url_path="restore-version", permission_classes=[IsAuthenticated])
    def restore_version(self, request, pk=None):
        cover = self.get_object()
        if not _user_is_responsible_for_cover(request.user, cover):
            return Response(
                {"detail": "You are not allowed to restore this record."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_version_no = (request.data or {}).get("version_no")
        try:
            version_no = int(raw_version_no)
        except (TypeError, ValueError):
            return Response(
                {"detail": "version_no must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        restore_from = CoverVersion.objects.filter(
            cover=cover, version_no=version_no
        ).first()
        if not restore_from:
            return Response(
                {"detail": f"Version {version_no} not found for this record."},
                status=status.HTTP_404_NOT_FOUND,
            )
        before_snapshot = build_cover_snapshot(cover)
        with transaction.atomic():
            restore_cover_from_snapshot(cover, restore_from.snapshot or {}, request.user)
            cover.refresh_from_db()
            after_snapshot = build_cover_snapshot(cover)
            txn = log_submission_transaction(
                action=SubmissionTransaction.ACTION_RESTORE_VERSION,
                actor=request.user,
                cover=cover,
                source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
                before_payload=before_snapshot,
                after_payload=after_snapshot,
                extra_payload={"restored_from_version_no": restore_from.version_no},
            )
            new_version = create_cover_version(cover, txn, request.user)
        return Response(
            {
                "detail": f"Record restored from version {restore_from.version_no}.",
                "restored_from_version_no": restore_from.version_no,
                "new_version_no": new_version.version_no,
            },
            status=status.HTTP_200_OK,
        )


class DateSeenViewSet(viewsets.ModelViewSet):
    # DateSeen is polymorphic. Clients filter by `subject_type=COVER|MARKING`
    # plus `subject_id=<pk>` to retrieve the date observations for a given
    # cover or marking.
    queryset = DateSeen.objects.all()
    serializer_class = DateSeenSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["subject_type", "subject_id", "granularity"]
    ordering_fields = ["date", "created_date"]
    ordering = ["subject_type", "subject_id", "date"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class CoverValuationViewSet(viewsets.ModelViewSet):
    queryset = CoverValuation.objects.all().select_related("cover")
    serializer_class = CoverValuationSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["cover"]
    ordering_fields = ["appraisal_date", "amt"]
    ordering = ["-appraisal_date"]
    # DELETE is intentionally not exposed; closing the unaudited hard-delete
    # path. No sanctioned valuation-removal flow is in scope; valuations are
    # edited rather than deleted.
    http_method_names = ["get", "post", "put", "patch", "head", "options", "trace"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)


class CoverMarkingViewSet(viewsets.ModelViewSet):
    # DateSeen is polymorphic and has no FK back to Cover, so we can no longer
    # prefetch it as a reverse relation. CoverSerializer.get_dates_seen issues
    # its own query per cover; if that becomes a hotspot, swap in a
    # Prefetch('dates_seen', queryset=DateSeen.objects.filter(subject_type='COVER'))
    # gated through a custom helper or override get_queryset to attach the rows.
    queryset = (
        CoverMarking.objects.all()
        .select_related("cover", "cover__color", "marking", "reviewer")
    )
    serializer_class = CoverMarkingSerializer
    permission_classes = [IsEditorOrAdminWrite]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = CoverMarkingFilter
    ordering_fields = ["id", "created_date", "reviewed_at"]
    ordering = ["id"]
    # DELETE is intentionally not exposed. Unlinking a cover from a marking has
    # no sanctioned flow yet; deferred to the same later PR as cover deletion.
    http_method_names = ["get", "post", "put", "patch", "head", "options", "trace"]

    def get_queryset(self):
        qs = (
            CoverMarking.objects.all()
            .select_related("cover", "cover__color", "marking", "reviewer")
            .prefetch_related("marking__post_office__post_office_regions__region")
        )
        # Hide links whose cover is in the recycle bin. select_related("cover")
        # joins the Cover table directly, bypassing Cover's default manager (which
        # hides removed rows), so a soft-removed cover would otherwise still show
        # as associated on the marking detail / contribution review pages.
        # EXCEPTION: when the caller asks for one specific cover's links
        # (?cover=...), do not apply this filter -- the cover detail page must
        # still show what a removed cover was associated with so an editor can
        # restore it in context.
        if not self.request.query_params.get("cover"):
            qs = qs.filter(cover__recycle_bin_entry__isnull=True)
        user = self.request.user
        marking_param = self.request.query_params.get("marking")

        if user.is_authenticated and user.is_superuser:
            if marking_param:
                try:
                    return qs.filter(marking_id=int(marking_param))
                except (TypeError, ValueError):
                    return qs.none()
            return qs

        if marking_param:
            try:
                mid = int(marking_param)
            except (TypeError, ValueError):
                return qs.none()
            marking = (
                Marking.objects.filter(pk=mid)
                .select_related("post_office")
                .prefetch_related("post_office__post_office_regions__region")
                .first()
            )
            if not marking:
                return qs.none()
            qs = qs.filter(marking_id=mid)
            if not user.is_authenticated:
                return qs.filter(review_status=CoverMarking.REVIEW_APPROVED)
            if _user_is_responsible_for_marking(user, marking):
                return qs
            return qs.filter(
                Q(review_status=CoverMarking.REVIEW_APPROVED) | Q(created_by_id=user.id)
            )

        if not user.is_authenticated:
            return qs.none()
        if user.has_perm(REVIEW_CONTRIBUTION_PERM):
            region_ids = list(
                Region.objects.filter(collection__id__in=user_assigned_collection_ids(user)).values_list(
                    "pk", flat=True
                )
            )
            if not region_ids:
                return qs.filter(created_by=user)
            return qs.filter(
                Q(created_by=user)
                | Q(marking__post_office__post_office_regions__region_id__in=region_ids)
            ).distinct()
        return qs.filter(created_by=user)

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            modified_by=self.request.user,
            review_status=CoverMarking.REVIEW_PENDING,
        )

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response(
                {"detail": "This cover is already linked to this marking."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="remove", permission_classes=[IsAuthenticated])
    def remove(self, request, pk=None):
        """
        Sanctioned unlink of a cover-marking association. A CoverMarking is a
        pure junction with no standalone history table, so unlink is an audited
        hard delete with a tombstone SubmissionTransaction (same shape as draft
        deletion), not a soft sidecar. Permitted for the editor responsible for
        the junction's marking region, or a superuser.
        """
        cm = (
            CoverMarking.objects.filter(pk=pk)
            .select_related("cover", "marking__post_office")
            .first()
        )
        if not cm:
            return Response({"detail": "Cover-marking not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_marking(request.user, cm.marking):
            return Response(
                {"detail": "You are not allowed to unlink this cover-marking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        with transaction.atomic():
            log_submission_transaction(
                action=SubmissionTransaction.ACTION_RECORD_DELETE,
                actor=request.user,
                cover=cm.cover,
                marking=cm.marking,
                source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
                before_payload={
                    "cover_id": cm.cover_id,
                    "marking_id": cm.marking_id,
                    "is_backstamp": cm.is_backstamp,
                    "placement": cm.placement,
                    "review_status": cm.review_status,
                },
                after_payload={},
                extra_payload={"deleted_cover_marking_id": cm.pk},
            )
            cm.delete()
        return Response(
            {"detail": "Cover-marking unlinked.", "coverMarkingId": int(pk)},
            status=status.HTTP_200_OK,
        )

    def _editor_may_review(self, user, cover_marking):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return _user_is_responsible_for_marking(user, cover_marking.marking)

    def _transition_review(
        self,
        cover_marking,
        *,
        new_status,
        actor,
        review_notes,
        log_action,
    ):
        before = {
            "review_status": cover_marking.review_status,
            "review_notes": cover_marking.review_notes,
        }
        cover_marking.review_status = new_status
        cover_marking.reviewer = actor
        cover_marking.review_notes = review_notes or ""
        cover_marking.reviewed_at = timezone.now()
        cover_marking.modified_by = actor
        cover_marking.save(
            update_fields=[
                "review_status",
                "reviewer",
                "review_notes",
                "reviewed_at",
                "modified_by",
                "modified_date",
            ]
        )
        after = {
            "review_status": cover_marking.review_status,
            "review_notes": cover_marking.review_notes,
        }
        log_submission_transaction(
            action=log_action,
            actor=actor,
            contribution=None,
            marking=cover_marking.marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload=before,
            after_payload=after,
            extra_payload={
                "cover_marking_id": cover_marking.pk,
                "cover_id": cover_marking.cover_id,
                "review_notes": review_notes or "",
            },
        )

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        cm = self.get_object()
        if not self._editor_may_review(request.user, cm):
            return Response(
                {"detail": "You do not have permission to approve this cover link."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if cm.review_status != CoverMarking.REVIEW_PENDING:
            return Response(
                {"detail": f"Only pending cover links can be approved (status: {cm.review_status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = serializer.validated_data.get("review_notes", "")
        self._transition_review(
            cm,
            new_status=CoverMarking.REVIEW_APPROVED,
            actor=request.user,
            review_notes=review_notes,
            log_action=SubmissionTransaction.ACTION_APPROVE,
        )
        return Response({"detail": "Cover link approved.", "review_status": cm.review_status})

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        cm = self.get_object()
        if not self._editor_may_review(request.user, cm):
            return Response(
                {"detail": "You do not have permission to reject this cover link."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if cm.review_status != CoverMarking.REVIEW_PENDING:
            return Response(
                {"detail": f"Only pending cover links can be rejected (status: {cm.review_status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = serializer.validated_data.get("review_notes", "")
        self._transition_review(
            cm,
            new_status=CoverMarking.REVIEW_REJECTED,
            actor=request.user,
            review_notes=review_notes,
            log_action=SubmissionTransaction.ACTION_REJECT,
        )
        return Response({"detail": "Cover link rejected.", "review_status": cm.review_status})

    @action(detail=True, methods=["post"], url_path="request-revision")
    def request_revision(self, request, pk=None):
        cm = self.get_object()
        if not self._editor_may_review(request.user, cm):
            return Response(
                {"detail": "You do not have permission to return this cover for revision."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if cm.review_status != CoverMarking.REVIEW_PENDING:
            return Response(
                {"detail": f"Only pending cover links can be returned for revision (status: {cm.review_status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = (serializer.validated_data.get("review_notes") or "").strip()
        if not review_notes:
            return Response(
                {"detail": "review_notes is required when requesting revision."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self._transition_review(
            cm,
            new_status=CoverMarking.REVIEW_NEEDS_REVISION,
            actor=request.user,
            review_notes=review_notes,
            log_action=SubmissionTransaction.ACTION_EDIT_SUBMISSION,
        )
        return Response({"detail": "Revision requested.", "review_status": cm.review_status})

    @action(detail=True, methods=["post"], url_path="resubmit")
    def resubmit(self, request, pk=None):
        cm = self.get_object()
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if cm.created_by_id != request.user.id:
            return Response(
                {"detail": "Only the contributor who added this cover may resubmit it for review."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if cm.review_status != CoverMarking.REVIEW_NEEDS_REVISION:
            return Response(
                {"detail": "Resubmit is only available when the editor has requested changes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        before = {"review_status": cm.review_status}
        cm.review_status = CoverMarking.REVIEW_PENDING
        cm.reviewer = None
        cm.review_notes = ""
        cm.reviewed_at = None
        cm.modified_by = request.user
        cm.save(
            update_fields=["review_status", "reviewer", "review_notes", "reviewed_at", "modified_by", "modified_date"]
        )
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_EDIT_SUBMISSION,
            actor=request.user,
            contribution=None,
            marking=cm.marking,
            source=SubmissionTransaction.SOURCE_CONTRIBUTOR_PORTAL,
            before_payload=before,
            after_payload={"review_status": cm.review_status},
            extra_payload={"cover_marking_id": cm.pk, "cover_id": cm.cover_id},
        )
        return Response({"detail": "Cover link resubmitted for review.", "review_status": cm.review_status})


###################################################################################################
## Marking (unified TOWNMARK | RATEMARK | AUXMARK)
###################################################################################################
class MarkingViewSet(viewsets.ModelViewSet):
    """
    Unified marking ViewSet. Handles Townmark / Ratemark /
    AuxmarkViewSet. List supports `?type=TOWNMARK|RATEMARK|AUXMARK`
    and the legacy filters preserved on MarkingListFilter.
    """
    pagination_class = MarkingListPagination
    queryset = Marking.objects.all()
    permission_classes = [IsEditorOrAdminWrite, IsResponsibleForRegion]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = MarkingListFilter
    # No raw DELETE: removing a marking goes through the audited, reversible
    # POST /markings/<pk>/remove/ (recycle bin) action instead. Custom POST
    # actions (remove, restore) are unaffected by this restriction.
    http_method_names = ["get", "post", "put", "patch", "head", "options", "trace"]
    search_fields = [
        "code",
        "catalog_txt",
        "inscription_txt",
        "desc",
        "post_office__post_office_regions__region__name",
        "post_office__name",
        "shape__name",
        "lettering__name",
        "color__name",
    ]
    ordering_fields = [
        # Location / identity
        "post_office__post_office_regions__region__name",
        "post_office__post_office_regions__region__abbrev",
        "post_office__name",
        "code",
        "type",
        # Physical/editorial fields
        "shape__name",
        "lettering__name",
        "color__name",
        "width",
        "height",
        # Date range annotations
        "earliest_seen",
        "latest_seen",
        # Stable fallback
        "id",
    ]
    ordering = [
        "post_office__post_office_regions__region__name",
        "post_office__name",
        "earliest_seen",
    ]

    def get_queryset(self):
        return _marking_list_queryset()

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            # The default manager hides removed markings. Allow ONLY the editor
            # responsible for this marking's region (or a superuser) to load a
            # removed marking on the detail page so they can restore it. Detail
            # (retrieve) reads only; every other caller keeps getting 404.
            if self.action != "retrieve":
                raise
            marking = (
                Marking.all_objects
                .select_related("post_office", "shape", "lettering", "color")
                .prefetch_related("post_office__post_office_regions__region")
                .with_date_range()
                .filter(pk=self.kwargs[self.lookup_field])
                .first()
            )
            if marking and _user_is_responsible_for_marking(self.request.user, marking):
                self.check_object_permissions(self.request, marking)
                return marking
            raise

    def get_serializer_class(self):
        if self.action == "list":
            return MarkingListSerializer
        return MarkingSerializer

    def perform_create(self, serializer):
        marking = serializer.save(created_by=self.request.user, modified_by=self.request.user)
        after_snapshot = build_marking_snapshot(marking)
        txn = log_submission_transaction(
            action=SubmissionTransaction.ACTION_RECORD_CREATE,
            actor=self.request.user,
            contribution=getattr(marking, "contribution", None),
            marking=marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload={},
            after_payload=after_snapshot,
        )
        create_marking_version(marking, txn, self.request.user)

    def perform_update(self, serializer):
        before_snapshot = build_marking_snapshot(serializer.instance)
        marking = serializer.save(modified_by=self.request.user)
        after_snapshot = build_marking_snapshot(marking)
        txn = log_submission_transaction(
            action=SubmissionTransaction.ACTION_RECORD_UPDATE,
            actor=self.request.user,
            contribution=getattr(marking, "contribution", None),
            marking=marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload=before_snapshot,
            after_payload=after_snapshot,
        )
        create_marking_version(marking, txn, self.request.user)

    @action(detail=True, methods=["post"], url_path="remove", permission_classes=[IsAuthenticated])
    def remove(self, request, pk=None):
        """
        Soft-REMOVE this marking into the recycle bin. The Marking row is not
        mutated or deleted; a MarkingRecycleBin sidecar row is created, which
        causes the default manager to hide it. Reversible via restore.
        Permitted for the editor responsible for the marking's region, or a
        superuser. Optional JSON body: {"reason": "..."}.
        """
        marking = (
            Marking.all_objects.filter(pk=pk).select_related("post_office").first()
        )
        if not marking:
            return Response({"detail": "Marking not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_marking(request.user, marking):
            return Response(
                {"detail": "You are not allowed to remove this marking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if MarkingRecycleBin.objects.filter(marking=marking).exists():
            return Response(
                {"detail": "Marking is already in the recycle bin."},
                status=status.HTTP_409_CONFLICT,
            )
        reason = ""
        if isinstance(request.data, dict):
            reason = (request.data.get("reason") or "").strip()
        with transaction.atomic():
            log_marking_removed(marking, request.user, reason)
            MarkingRecycleBin.objects.create(
                marking=marking, removed_by=request.user, reason=reason
            )
        return Response(
            {"detail": "Marking removed.", "markingId": marking.pk},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="restore", permission_classes=[IsAuthenticated])
    def restore(self, request, pk=None):
        """
        Restore this marking from the recycle bin by deleting its
        MarkingRecycleBin sidecar row. Permitted for the editor responsible for
        the marking's region, or a superuser.
        """
        marking = (
            Marking.all_objects.filter(pk=pk).select_related("post_office").first()
        )
        if not marking:
            return Response({"detail": "Marking not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_marking(request.user, marking):
            return Response(
                {"detail": "You are not allowed to restore this marking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        entry = MarkingRecycleBin.objects.filter(marking=marking).first()
        if not entry:
            return Response(
                {"detail": "Marking is not in the recycle bin."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            entry.delete()
            log_marking_restored(marking, request.user)
        return Response(
            {"detail": "Marking restored.", "markingId": marking.pk},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="recycle-bin", permission_classes=[IsAuthenticated])
    def recycle_bin(self, request):
        """
        List markings currently in the recycle bin (removed), scoped to the
        regions the editor is responsible for; superusers see all. Uses
        Marking.all_objects since the default manager hides removed rows.
        Returns the same list shape as the main marking list so the dashboard
        can render the rows with its existing card. Per-row removal metadata
        (who/when/why) lives in the marking changelog.
        """
        user = request.user
        if not (user.is_superuser or user.has_perm(REVIEW_CONTRIBUTION_PERM)):
            return Response(
                {"detail": "You are not allowed to view the recycle bin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = (
            Marking.all_objects.filter(recycle_bin_entry__isnull=False)
            .select_related("post_office", "shape", "lettering", "color")
            .prefetch_related("post_office__post_office_regions__region")
            .with_date_range()
            .order_by("-recycle_bin_entry__removed_at")
        )
        if not user.is_superuser:
            assigned_regions = _get_user_assigned_regions(user)
            if assigned_regions.exists():
                qs = qs.filter(
                    post_office__post_office_regions__region__in=assigned_regions
                ).distinct()
            else:
                qs = qs.none()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = MarkingListSerializer(page, many=True, context=self.get_serializer_context())
            return self.get_paginated_response(serializer.data)
        serializer = MarkingListSerializer(qs, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="my-assigned", permission_classes=[IsAuthenticated])
    def my_assigned(self, request):
        user = request.user
        assigned_regions = _get_user_assigned_regions(user)
        if not assigned_regions.exists():
            empty = self.get_queryset().none()
            page = self.paginate_queryset(empty)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            return Response(self.get_serializer(empty, many=True).data)
        qs = self.get_queryset().filter(
            post_office__post_office_regions__region__in=assigned_regions
        ).distinct().order_by("-created_date")
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"], url_path="my-submissions", permission_classes=[IsAuthenticated])
    def my_submissions(self, request):
        user = request.user
        qs = self.get_queryset().filter(contribution__contributor=user).order_by("-created_date")
        if not user.is_superuser:
            assigned_regions = _get_user_assigned_regions(user)
            if assigned_regions.exists():
                qs = qs.filter(
                    post_office__post_office_regions__region__in=assigned_regions
                ).distinct()
            else:
                qs = qs.none()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="changelog", permission_classes=[IsAuthenticated])
    def changelog(self, request, pk=None):
        # Use all_objects so a removed (recycle-binned) marking's history stays
        # viewable; the default manager would 404 it. Responsibility is checked
        # explicitly below.
        marking = Marking.all_objects.filter(pk=pk).first()
        if not marking:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _user_is_responsible_for_marking(request.user, marking):
            return Response(
                {"detail": "You are not allowed to view changelog for this record."},
                status=status.HTTP_403_FORBIDDEN,
            )

        def _summary(snap):
            snap = snap if isinstance(snap, dict) else {}
            return {
                "catalog_txt": snap.get("catalog_txt") or "",
                "code": snap.get("code") or "",
                "town": snap.get("town") or "",
                "state": snap.get("state") or "",
                "type": snap.get("type") or "",
                "inscription_txt": snap.get("inscription_txt") or "",
                "desc": snap.get("desc") or "",
                "is_manuscript": bool(snap.get("is_manuscript")),
                "impression": snap.get("impression") or "",
                "is_irreg": snap.get("is_irreg"),
                "shape_id": snap.get("shape_id"),
                "lettering_id": snap.get("lettering_id"),
                "color_id": snap.get("color_id"),
                "date_fmt": snap.get("date_fmt") or "",
                "rate_val": snap.get("rate_val"),
                "width": snap.get("width"),
                "height": snap.get("height"),
            }

        txns = list(
            SubmissionTransaction.objects.filter(
                Q(marking=marking) | Q(contribution__marking=marking)
            )
            .select_related("actor", "contribution")
            .order_by("-created_at", "-id")
            .distinct()
        )
        versions = list(
            MarkingVersion.objects.filter(marking=marking)
            .select_related("created_by", "transaction")
            .order_by("-version_no")
        )
        version_no_by_txn_id = {
            v.transaction_id: v.version_no for v in versions if v.transaction_id is not None
        }
        txn_by_id = {txn.id: txn for txn in txns}
        action_labels = dict(SubmissionTransaction.ACTION_CHOICES)

        events = []
        for txn in txns:
            actor_name = None
            actor_email = None
            if txn.actor:
                actor_email = (getattr(txn.actor, "email", "") or "").strip() or None
                actor_name = (
                    txn.actor.get_username()
                    or actor_email
                    or str(txn.actor.pk)
                )
            events.append(
                {
                    "event_id": txn.id,
                    "transaction_uuid": str(txn.transaction_uuid),
                    "timestamp": txn.created_at,
                    "action": txn.action,
                    "action_label": action_labels.get(txn.action, txn.action.replace("_", " ").title()),
                    "actor": actor_name,
                    # actor_email is what the editor-facing Record History panel
                    # displays per row. We expose it explicitly (in addition to
                    # the username-fallback "actor" string) because the audit
                    # trail is contractually email-based on the UI side.
                    "actor_email": actor_email,
                    "source": txn.source,
                    "contribution_id": txn.contribution_id,
                    "version_no": version_no_by_txn_id.get(txn.id),
                    "diff": txn.diff_payload or [],
                    "summary": f"{action_labels.get(txn.action, txn.action)} by {actor_email or actor_name or 'system'}",
                }
            )

        approved_actions = {
            SubmissionTransaction.ACTION_APPROVE,
            SubmissionTransaction.ACTION_CATALOG_DIRECT_EDIT,
            SubmissionTransaction.ACTION_RESTORE_VERSION,
        }
        version_rows = []
        approved_version_rows = []
        for version in versions:
            created_by_name = None
            if version.created_by:
                created_by_name = (
                    version.created_by.get_username()
                    or getattr(version.created_by, "email", "")
                    or str(version.created_by.pk)
                )
            txn = txn_by_id.get(version.transaction_id) if version.transaction_id is not None else None
            txn_action = txn.action if txn else None
            row = {
                "version_no": version.version_no,
                "created_at": version.created_at,
                "created_by": created_by_name,
                "transaction_id": version.transaction_id,
                "action": txn_action,
                "action_label": (
                    action_labels.get(txn_action, str(txn_action).replace("_", " ").title())
                    if txn_action
                    else None
                ),
                "snapshot": _summary(version.snapshot),
            }
            version_rows.append(row)
            if txn_action in approved_actions:
                approved_version_rows.append(row)

        return Response(
            {
                "marking_id": marking.pk,
                "events": events,
                "versions": version_rows,
                "approved_versions": approved_version_rows,
            }
        )

    @action(detail=True, methods=["post"], url_path="restore-version", permission_classes=[IsAuthenticated])
    def restore_version(self, request, pk=None):
        marking = self.get_object()
        if not _user_is_responsible_for_marking(request.user, marking):
            return Response(
                {"detail": "You are not allowed to restore this record."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_version_no = (request.data or {}).get("version_no")
        try:
            version_no = int(raw_version_no)
        except (TypeError, ValueError):
            return Response(
                {"detail": "version_no must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        restore_from = MarkingVersion.objects.filter(
            marking=marking, version_no=version_no
        ).first()
        if not restore_from:
            return Response(
                {"detail": f"Version {version_no} not found for this record."},
                status=status.HTTP_404_NOT_FOUND,
            )
        before_snapshot = build_marking_snapshot(marking)
        with transaction.atomic():
            restore_marking_from_snapshot(marking, restore_from.snapshot or {}, request.user)
            marking.refresh_from_db()
            after_snapshot = build_marking_snapshot(marking)
            txn = log_submission_transaction(
                action=SubmissionTransaction.ACTION_RESTORE_VERSION,
                actor=request.user,
                contribution=getattr(marking, "contribution", None),
                marking=marking,
                source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
                before_payload=before_snapshot,
                after_payload=after_snapshot,
                extra_payload={"restored_from_version_no": restore_from.version_no},
            )
            new_version = create_marking_version(marking, txn, request.user)
        return Response(
            {
                "detail": f"Record restored from version {restore_from.version_no}.",
                "restored_from_version_no": restore_from.version_no,
                "new_version_no": new_version.version_no,
            },
            status=status.HTTP_200_OK,
        )


###################################################################################################
## Custom non-router endpoints
###################################################################################################
@extend_schema(
    responses=inline_serializer(
        name="MarkingDateRangeResponse",
        fields={
            "earliest_year": serializers.IntegerField(allow_null=True),
            "latest_year": serializers.IntegerField(allow_null=True),
        },
    )
)
class MarkingDateRangeView(APIView):
    """Earliest and latest dates_seen.date years across the approved catalog.

    Aggregates DateSeen rows that belong to approved catalog content only:
      * subject_type='MARKING': subject_id must reference an existing Marking.
        Draft contributions do not have a Marking row yet (Contribution.marking
        is null until approval), so MARKING-scoped draft dates cannot exist.
      * subject_type='COVER':  subject_id must reference a Cover that is linked
        to at least one Marking via an APPROVED CoverMarking. Cover-scoped
        DateSeen rows created during draft / pending / rejected reviews are
        therefore excluded from the public catalog range.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        approved_cover_ids = CoverMarking.objects.filter(
            review_status=CoverMarking.REVIEW_APPROVED,
        ).values("cover_id")
        approved_marking_ids = Marking.objects.values("pk")

        qs = DateSeen.objects.filter(
            Q(subject_type=DateSeen.SUBJECT_MARKING, subject_id__in=approved_marking_ids)
            | Q(subject_type=DateSeen.SUBJECT_COVER, subject_id__in=approved_cover_ids)
        )

        agg = qs.aggregate(
            earliest_year=Min(ExtractYear("date")),
            latest_year=Max(ExtractYear("date")),
        )
        earliest = int(agg["earliest_year"]) if agg["earliest_year"] is not None else None
        latest = int(agg["latest_year"]) if agg["latest_year"] is not None else None
        return Response({"earliest_year": earliest, "latest_year": latest})


###################################################################################################
## Contribution viewset
###################################################################################################
def _get_editor_contribution_queryset(user):
    """Editors see contributions for Collections they are assigned to."""
    assigned_ids = user_assigned_collection_ids(user)
    base = Contribution.objects.select_related(
        "contributor", "reviewer", "marking", "collection", "collection__region"
    )
    if not assigned_ids:
        return base.none()
    return base.filter(collection_id__in=assigned_ids).distinct()


@method_decorator(csrf_exempt, name="dispatch")
class ContributionViewSet(
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    GET list / detail at /contributions/ and /contributions/<pk>/.
    POST /contributions/ delegates to ContributionSubmitView so authenticated
    contributors can submit new entries here. Approve / reject actions live on
    detail routes.
    DELETE /contributions/<pk>/ hard-deletes a DRAFT owned by the requester
    (true DELETE); see IsDraftOwner. Non-draft contributions cannot be
    hard-deleted -- removing a promoted marking goes through the recycle bin.
    """
    permission_classes = [IsAuthenticated, CanReviewContribution]
    serializer_class = ContributionDetailSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]
    ordering = ["-created_date"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        # POST /contributions/ is the public submission endpoint and only
        # requires authentication; the editor review permission is enforced on
        # GET (list/detail) and on the approve/reject detail actions.
        if self.action == "create":
            return [IsAuthenticated()]
        # DELETE is the true-delete-for-drafts path: gated by IsDraftOwner,
        # not the editor-review permission.
        if self.action == "destroy":
            return [IsAuthenticated(), IsDraftOwner()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        # Reachable only for a draft owned by the requester (IsDraftOwner).
        # A draft has no Marking yet, so this hard-delete has no downstream
        # catalog impact. Record a tombstone transaction before deleting; the
        # contribution FK is left null because the row is about to vanish
        # (SubmissionTransaction.contribution is SET_NULL anyway).
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_DRAFT_DELETED,
            actor=self.request.user,
            contribution=None,
            marking=None,
            source=SubmissionTransaction.SOURCE_CONTRIBUTOR_PORTAL,
            before_payload={
                "contribution_id": instance.pk,
                "status": instance.status,
                "collection_id": instance.collection_id,
                "submitted_data": instance.submitted_data,
            },
            after_payload={},
            extra_payload={"deleted_contribution_id": instance.pk},
        )
        instance.delete()

    def create(self, request, *args, **kwargs):
        # Delegate to ContributionSubmitView.post so the submission logic stays
        # in one place. The DRF Request from this viewset already exposes data,
        # FILES, and user as ContributionSubmitView expects.
        submit_view = ContributionSubmitView()
        submit_view.request = request
        submit_view.kwargs = getattr(self, "kwargs", {})
        submit_view.format_kwarg = getattr(self, "format_kwarg", None)
        return submit_view.post(request)

    def get_queryset(self):
        # Visibility rules:
        #   list (default)          -> "My Contributions": only rows where the
        #                              logged-in user is the contributor. This
        #                              applies to everyone including editors
        #                              and superusers, so the dashboard's
        #                              My Contributions tab never leaks other
        #                              users' work.
        #   list ?mode=editor       -> editor review queue: contributions on
        #                              collections the user is assigned to.
        #                              Requires the review permission;
        #                              superusers see everything.
        #   retrieve (detail) and
        #   action endpoints        -> union of "mine" and "editor view" so
        #                              opening a single contribution from
        #                              either tab works without the caller
        #                              having to know which lens it came
        #                              from. Approve/reject/request-revision
        #                              additionally enforce the review
        #                              permission via the decorator on each
        #                              action.
        user = self.request.user
        base_qs = Contribution.objects.select_related(
            "contributor", "reviewer", "marking", "collection", "collection__region"
        )
        if self.action != "list":
            if user.is_superuser:
                return base_qs
            mine = Q(contributor=user)
            if user.has_perm(REVIEW_CONTRIBUTION_PERM):
                assigned_ids = user_assigned_collection_ids(user)
                if assigned_ids:
                    return base_qs.filter(mine | Q(collection_id__in=assigned_ids)).distinct()
            return base_qs.filter(mine).distinct()
        mode = (self.request.query_params.get("mode") or "").strip().lower()
        if mode == "editor":
            if user.is_superuser:
                qs = base_qs
            elif user.has_perm(REVIEW_CONTRIBUTION_PERM):
                qs = _get_editor_contribution_queryset(user)
            else:
                return base_qs.none()
            state = (self.request.query_params.get("state") or "").strip()
            if state:
                qs = qs.filter(
                    Q(collection__region__name__iexact=state)
                    | Q(collection__region__abbrev__iexact=state)
                    | Q(submitted_data__state__iexact=state)
                )
            return qs
        return base_qs.filter(contributor=user).distinct()

    def get_serializer_class(self):
        if self.action == "list":
            return ContributionListSerializer
        return ContributionDetailSerializer

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        contrib = self.get_object()
        if contrib.status != Contribution.STATUS_PENDING:
            return Response(
                {"detail": f"Contribution is not pending (status: {contrib.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = serializer.validated_data.get("review_notes", "")
        try:
            with transaction.atomic():
                # Capture the BEFORE snapshot for edits (the entity already
                # exists and apply mutates it in place). build_*_snapshot read
                # live DB state, so this must run BEFORE apply. CREATE leaves
                # these empty, so the audit diff for a create stays before={}.
                sd = contrib.submitted_data or {}
                edit_marking_id = _parse_int(sd.get("edit_marking_id"))
                edit_cover_id = _parse_int(sd.get("edit_cover_id"))
                before_marking_snapshot = {}
                before_cover_snapshot = {}
                if edit_cover_id:
                    try:
                        before_cover_snapshot = build_cover_snapshot(
                            Cover.all_objects.get(pk=edit_cover_id)
                        )
                    except Cover.DoesNotExist:
                        pass
                elif edit_marking_id:
                    try:
                        before_marking_snapshot = build_marking_snapshot(
                            Marking.all_objects.get(pk=edit_marking_id)
                        )
                    except Marking.DoesNotExist:
                        pass

                result = contrib.apply_to_catalog()

                # apply_to_catalog returns a Marking for marking submissions and
                # a dict {"kind": "cover", ...} for cover submissions. Branch on
                # the return type so each path does its own post-processing.
                if isinstance(result, dict) and result.get("kind") == "cover":
                    cover = result["cover"]
                    cover_marking = result["cover_marking"]
                    parent_marking = result["parent_marking"]

                    # apply set review_status=APPROVED and reviewed_at; backfill
                    # the approving editor's identity and notes on the link here
                    # (apply has no access to request.user).
                    cover_marking.reviewer = request.user
                    cover_marking.review_notes = review_notes
                    cover_marking.modified_by = request.user
                    # Stamp reviewed_at here for BOTH create and edit: the edit
                    # apply path deliberately leaves the link's review fields
                    # alone, so the approve view owns reviewed_at in both cases.
                    cover_marking.reviewed_at = timezone.now()
                    cover_marking.save(
                        update_fields=["reviewer", "review_notes", "modified_by", "reviewed_at"]
                    )

                    contrib.status = Contribution.STATUS_APPROVED
                    contrib.reviewer = request.user
                    contrib.review_notes = review_notes
                    contrib.modified_by = request.user
                    update_fields = [
                        "status",
                        "reviewer",
                        "review_notes",
                        "submitted_data",
                        "modified_date",
                        "modified_by",
                    ]
                    if not Contribution.objects.filter(marking=parent_marking).exclude(pk=contrib.pk).exists():
                        contrib.marking = parent_marking
                        update_fields.append("marking")
                    # Stamp traceability so the frontend mapper treats this
                    # contribution as materialized (no longer a pending draft).
                    sd = dict(contrib.submitted_data or {})
                    sd["cover_id"] = cover.pk
                    sd["cover_marking_id"] = cover_marking.pk
                    sd["materialized_cover_marking_id"] = cover_marking.pk
                    contrib.submitted_data = sd
                    contrib.save(update_fields=update_fields)
                    after_snapshot = build_cover_snapshot(cover)
                    txn = log_submission_transaction(
                        action=SubmissionTransaction.ACTION_APPROVE,
                        actor=request.user,
                        contribution=contrib,
                        marking=parent_marking,
                        cover=cover,
                        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
                        before_payload=before_cover_snapshot,
                        after_payload=after_snapshot,
                        extra_payload={
                            "review_notes": review_notes,
                            "cover_marking_id": cover_marking.pk,
                        },
                    )
                    create_cover_version(cover, txn, request.user)
                    approved_response = {"detail": "Contribution approved.", "coverId": cover.pk}
                else:
                    marking = result
                    if not marking:
                        return Response(
                            {"detail": "Could not apply contribution. Check submitted_data."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )
                    contrib.status = Contribution.STATUS_APPROVED
                    contrib.reviewer = request.user
                    contrib.review_notes = review_notes
                    contrib.modified_by = request.user
                    update_fields = ["status", "reviewer", "review_notes", "modified_date", "modified_by"]
                    if not Contribution.objects.filter(marking=marking).exclude(pk=contrib.pk).exists():
                        contrib.marking = marking
                        update_fields.append("marking")
                    contrib.save(update_fields=update_fields)
                    after_snapshot = build_marking_snapshot(marking)
                    txn = log_submission_transaction(
                        action=SubmissionTransaction.ACTION_APPROVE,
                        actor=request.user,
                        contribution=contrib,
                        marking=marking,
                        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
                        before_payload=before_marking_snapshot,
                        after_payload=after_snapshot,
                        extra_payload={"review_notes": review_notes},
                    )
                    create_marking_version(marking, txn, request.user)
                    approved_response = {
                        "detail": "Contribution approved.",
                        "markingId": marking.pk,
                    }
        except ContributionApplyError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(approved_response, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        contrib = self.get_object()
        if contrib.status != Contribution.STATUS_PENDING:
            return Response(
                {"detail": f"Contribution is not pending (status: {contrib.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = serializer.validated_data.get("review_notes", "")
        before_submission = dict(contrib.submitted_data or {})
        contrib.status = Contribution.STATUS_REJECTED
        contrib.reviewer = request.user
        contrib.review_notes = review_notes
        contrib.modified_by = request.user
        contrib.save(update_fields=["status", "reviewer", "review_notes", "modified_date", "modified_by"])
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_REJECT,
            actor=request.user,
            contribution=contrib,
            marking=contrib.marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload=before_submission,
            after_payload=before_submission,
            extra_payload={"review_notes": review_notes},
        )
        return Response({"detail": "Contribution rejected."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="request-revision")
    def request_revision(self, request, pk=None):
        contrib = self.get_object()
        if contrib.status != Contribution.STATUS_PENDING:
            return Response(
                {"detail": f"Contribution is not pending (status: {contrib.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ContributionApproveRejectSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        review_notes = (serializer.validated_data.get("review_notes") or "").strip()
        if not review_notes:
            return Response(
                {"detail": "review_notes is required when requesting revision."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        before_submission = dict(contrib.submitted_data or {})
        contrib.status = Contribution.STATUS_NEEDS_REVISION
        contrib.reviewer = request.user
        contrib.review_notes = review_notes
        contrib.modified_by = request.user
        contrib.save(update_fields=["status", "reviewer", "review_notes", "modified_date", "modified_by"])
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_EDIT_SUBMISSION,
            actor=request.user,
            contribution=contrib,
            marking=contrib.marking,
            source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
            before_payload=before_submission,
            after_payload=before_submission,
            extra_payload={"review_notes": review_notes},
        )
        return Response(
            {"detail": "Contribution returned for revision.", "status": contrib.status},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    request=inline_serializer(
        name="ContributionSubmitRequest",
        fields={
            "state": serializers.CharField(help_text="State name or abbreviation; routes to a Collection"),
            "type": serializers.ChoiceField(
                choices=["TOWNMARK", "RATEMARK", "AUXMARK"],
                required=False,
            ),
            "desc": serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        201: inline_serializer(
            name="ContributionSubmitResponse",
            fields={
                "contribution_id": serializers.IntegerField(),
                "status": serializers.CharField(),
            },
        ),
        400: OpenApiResponse(description="Validation error"),
    },
)
def _region_routing_value_from_post_office(post_office) -> str:
    """Collection routing key: region abbrev when set, else full region name."""
    if post_office is None:
        return ""
    region = post_office.region
    if region is None:
        return ""
    abbrev = (region.abbrev or "").strip()
    name = (region.name or "").strip()
    return abbrev or name


def _resolve_contribution_state_value(data) -> str:
    """
    Resolve the state/region string used to pick a Collection.

    Cover drafts may omit `state` in the form; derive it from the parent
    marking's post office when parent_marking_id / marking_id is present.
    """
    explicit = (data.get("state") or "").strip()
    if explicit:
        return explicit
    for key in ("parent_marking_id", "marking_id", "marking"):
        raw = data.get(key)
        if raw in (None, ""):
            continue
        try:
            marking_id = int(raw)
        except (TypeError, ValueError):
            continue
        marking = (
            Marking.all_objects.filter(pk=marking_id)
            .select_related("post_office")
            .prefetch_related("post_office__post_office_regions__region")
            .first()
        )
        if not marking:
            continue
        routed = _region_routing_value_from_post_office(marking.post_office)
        if routed:
            return routed
    return ""


def _is_save_as_draft_submission(data) -> bool:
    save_as_draft_raw = str(
        data.get("save_as_draft") or data.get("saveAsDraft") or data.get("status") or ""
    ).strip().lower()
    return save_as_draft_raw in {"draft", "true", "1", "yes", "on"}


def _is_cover_submission_data(data) -> bool:
    """Detect cover drafts/submissions (submission_kind or cover-only payload shape)."""
    kind = str(data.get("submission_kind") or data.get("submissionKind") or "").strip().lower()
    if kind == "cover":
        return True
    if kind in {"marking", "townmark", "ratemark", "auxmark"}:
        return False
    type_value = str(data.get("type") or "").strip().upper()
    has_cover_type = type_value in {"FC", "FL"}
    has_marking_type = type_value in {"TOWNMARK", "RATEMARK", "AUXMARK"}
    has_town = bool(str(data.get("town") or "").strip())
    parent_raw = data.get("parent_marking_id") or data.get("marking_id")
    has_parent = parent_raw not in (None, "")
    has_cover_date = bool(str(data.get("cover_date") or data.get("coverDate") or "").strip())
    if has_parent and (has_cover_type or has_cover_date) and not has_town and not has_marking_type:
        return True
    return False


def _submitted_data_is_cover(sd) -> bool:
    if not isinstance(sd, dict):
        return False
    return _is_cover_submission_data(sd)


def _resolve_collection_for_submission(
    *,
    state_value: str,
    is_draft: bool,
    is_cover_submission: bool,
):
    """
    Return (region, collection, effective_state_value).

    Cover *drafts* may be stored even when the parent marking has no routable
    region: we attach them to any active Collection to satisfy the NOT NULL FK.
    Editors re-route on final submit when the contributor completes the form.
    """
    region = None
    collection = None
    effective_state = state_value

    if state_value:
        region = Region.objects.filter(
            Q(name__iexact=state_value) | Q(abbrev__iexact=state_value)
        ).first()
        if region:
            collection = Collection.objects.filter(region=region, is_active=True).first()

    if collection is None and is_draft and is_cover_submission:
        collection = (
            Collection.objects.filter(is_active=True)
            .select_related("region")
            .order_by("pk")
            .first()
        )
        if collection and collection.region_id and not effective_state:
            r = collection.region
            effective_state = (r.abbrev or "").strip() or (r.name or "").strip()

    return region, collection, effective_state


@method_decorator(csrf_exempt, name="dispatch")
class ContributionSubmitView(APIView):
    """
    Public submission endpoint for new contributions.

    Accepts the unified payload shape: marking_* keys,
    plus `type` (TOWNMARK | RATEMARK | AUXMARK) and `desc`. The payload is
    persisted to Contribution.submitted_data and routed to a Collection by
    state. Final application to the catalog (creating / updating Marking,
    Image, CoverMarking, DateSeen, CoverValuation rows) happens at approval
    time.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else dict(request.data)
        is_cover_submission = _is_cover_submission_data(data)
        is_draft = _is_save_as_draft_submission(data)
        edit_pk = None
        edit_raw = data.get("edit_contribution_id") or data.get("editContributionId")
        if edit_raw not in (None, ""):
            try:
                edit_pk = int(edit_raw)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Invalid edit_contribution_id."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        abandon_draft = str(
            data.get("abandon_draft") or data.get("abandonDraft") or ""
        ).lower() in {"1", "true", "yes", "on"}
        if abandon_draft:
            if edit_pk is None:
                return Response(
                    {"detail": "edit_contribution_id is required to abandon a draft."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            contrib = Contribution.objects.filter(
                pk=edit_pk,
                contributor=request.user,
                status=Contribution.STATUS_DRAFT,
            ).first()
            if not contrib:
                return Response(
                    {"detail": "Draft not found or not editable."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            sd = contrib.submitted_data or {}
            is_cover_draft = _submitted_data_is_cover(sd)
            is_marking_edit_draft = bool(sd.get("edit_marking_id"))
            if not (is_cover_draft or is_marking_edit_draft):
                return Response(
                    {"detail": "This draft type cannot be abandoned via this endpoint."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            contrib.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        routing_state = _resolve_contribution_state_value(data)
        region, collection, state_value = _resolve_collection_for_submission(
            state_value=routing_state,
            is_draft=is_draft,
            is_cover_submission=is_cover_submission,
        )
        routing_deferred = is_draft and is_cover_submission and not routing_state

        if collection is None:
            if not routing_state:
                return Response(
                    {"detail": "state is required to route the contribution to a Collection."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if region is None:
                return Response(
                    {"detail": f"No Region matches state={routing_state!r}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"detail": f"No active Collection covers region {region.name!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        type_value = (data.get("type") or "").strip().upper()
        if not is_cover_submission:
            if type_value and type_value not in {"TOWNMARK", "RATEMARK", "AUXMARK"}:
                return Response(
                    {"detail": "type must be TOWNMARK, RATEMARK, or AUXMARK."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif type_value and type_value not in {"FC", "FL"}:
            # Cover contributions may send cover type as `type` (FC | FL).
            return Response(
                {"detail": "For cover submissions, type must be FC or FL when provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Save uploaded image files under MEDIA_ROOT/<region_abbrev>/.
        # Marking flow uses `marking_image`; cover draft flow uses `cover_image`.
        # stash the resulting metadata on Contribution.submitted_data so the
        # editor portal can display previews. Approval-time materialization
        # into Image rows happens in the contribution-apply pipeline.
        if region is not None:
            region_abbrev = (region.abbrev or "").strip().lower() or "unknown"
        elif collection is not None and collection.region_id:
            region_abbrev = (collection.region.abbrev or "").strip().lower() or "unknown"
        else:
            region_abbrev = "draft"
        uploaded_files = []
        try:
            if is_cover_submission:
                uploaded_files = request.FILES.getlist("cover_image")
            else:
                uploaded_files = request.FILES.getlist("marking_image")
        except AttributeError:
            uploaded_files = []
        image_metas = []
        for uploaded in uploaded_files:
            meta = _save_contribution_image(uploaded, region_abbrev)
            if meta:
                image_metas.append(meta)

        # Contributor-chosen final image order: a list of tokens, one per
        # displayed image, where "__new__" is a freshly-uploaded file (consumed
        # FIFO from image_metas) and any other token is an existing image's
        # URL/storage_filename tail. Applied at submit time so the saved metas
        # list -- and thus display_order at approval -- matches the UI order.
        image_order = _parse_removed_image_keys(data.get("image_order"))

        # Strip multi-value form keys to plain values for the JSONField payload.
        # Skip raw file objects: they are not JSON-serializable and we have already
        # captured them in image_metas above.
        submitted_data = {}
        skip_keys = {
            "marking_image",
            "cover_image",
            "edit_contribution_id",
            "editContributionId",
            "removed_existing_image_keys",
            # Image-ordering control consumed by the *_image_metas builders;
            # not a display field for the review UI.
            "image_order",
            # Submit-mode controls -- consumed by _is_save_as_draft_submission
            # above; should not be persisted into the JSON submitted_data
            # payload that the review UI renders as field rows.
            "save_as_draft",
            "saveAsDraft",
            "status",
        }
        for key in data:
            if key in skip_keys:
                continue
            value = data.get(key)
            if hasattr(value, "read") and hasattr(value, "name"):
                continue
            try:
                submitted_data[key] = value
            except Exception:
                submitted_data[key] = str(value)

        if is_cover_submission:
            submitted_data["submission_kind"] = "cover"
            submitted_data["entity_type"] = "cover"
        else:
            submitted_data["submission_kind"] = submitted_data.get("submission_kind") or "marking"
            submitted_data["entity_type"] = "marking"
        if state_value:
            submitted_data["state"] = state_value
        if routing_deferred:
            submitted_data["routing_deferred"] = True

        # Collapse duplicate marking-edit drafts. When a contributor saves a
        # draft against an existing marking (edit_marking_id) without already
        # targeting a specific draft (no edit_contribution_id), look for an
        # open draft of theirs against the same marking and route the save
        # through the update branch below. Closes the race where two tabs (or
        # a fast click before the frontend dedupe GET resolves) would each
        # create a parallel draft row. Form-posted values arrive as strings;
        # the JSON branch posts ints, so query both shapes.
        if edit_pk is None and is_draft:
            edit_marking_id_raw = submitted_data.get("edit_marking_id")
            if edit_marking_id_raw not in (None, ""):
                try:
                    epi_int = int(edit_marking_id_raw)
                except (TypeError, ValueError):
                    epi_int = None
                epi_str = str(edit_marking_id_raw)
                value_filter = Q(submitted_data__edit_marking_id=epi_str)
                if epi_int is not None:
                    value_filter |= Q(submitted_data__edit_marking_id=epi_int)
                existing_draft = (
                    Contribution.objects.filter(
                        contributor=request.user,
                        status=Contribution.STATUS_DRAFT,
                    )
                    .filter(value_filter)
                    .order_by("-modified_date")
                    .first()
                )
                if existing_draft is not None:
                    edit_pk = existing_draft.pk

        if edit_pk is not None:
            if is_draft:
                # Allow save-as-draft against either an actual draft or a
                # needs_revision row (contributor saving partial edits before
                # they are ready to resubmit). The needs_revision row keeps
                # its status so it stays in the editor's history and the
                # original review_notes remain visible.
                target_statuses = [
                    Contribution.STATUS_DRAFT,
                    Contribution.STATUS_NEEDS_REVISION,
                ]
                transition_to_pending = False
            else:
                # Non-draft submit with edit_pk: contributor either resubmitting
                # after the editor returned the contribution for revision OR
                # promoting one of their own drafts to pending review for the
                # first time. Both transition the row to pending.
                target_statuses = [
                    Contribution.STATUS_DRAFT,
                    Contribution.STATUS_NEEDS_REVISION,
                ]
                transition_to_pending = True

            contrib = Contribution.objects.filter(
                pk=edit_pk,
                contributor=request.user,
                status__in=target_statuses,
            ).first()
            if not contrib:
                detail = (
                    "Contribution not found, not owned by you, or not in a submittable status (draft / needs_revision)."
                    if transition_to_pending
                    else "Draft or returned contribution not found or not editable."
                )
                return Response({"detail": detail}, status=status.HTTP_404_NOT_FOUND)
            existing_sd = dict(contrib.submitted_data or {})

            # Honor contributor-side image removals and merge new uploads
            # against the draft's prior submitted_data. Frontend
            # (Contribute.tsx -> removeExistingImageAt) sends a JSON list of the
            # displayed URLs the user removed from the edit form; the helper
            # drops matching entries and merges any newly uploaded files.
            image_updates = _apply_existing_image_reconciliation(
                existing_sd, data, image_metas, is_cover_submission, image_order
            )
            submitted_data.update(image_updates)
            existing_sd.update(submitted_data)
            contrib.submitted_data = existing_sd
            contrib.collection = collection
            contrib.modified_by = request.user
            update_fields = ["submitted_data", "collection", "modified_date", "modified_by"]
            if transition_to_pending:
                contrib.status = Contribution.STATUS_PENDING
                contrib.reviewer = None
                contrib.review_notes = ""
                update_fields += ["status", "reviewer", "review_notes"]
            contrib.save(update_fields=update_fields)
            log_submission_transaction(
                action=SubmissionTransaction.ACTION_EDIT_SUBMISSION,
                actor=request.user,
                contribution=contrib,
                marking=None,
                source=SubmissionTransaction.SOURCE_CONTRIBUTOR_PORTAL,
                before_payload={},
                after_payload=existing_sd,
                extra_payload={"resubmitted": transition_to_pending},
            )
            return Response(
                ContributionDetailSerializer(contrib, context={"request": request}).data,
                status=status.HTTP_200_OK,
            )

        # Fresh submit (no edit_contribution_id). When an edit marker is present
        # -- a PENDING edit submitted without first creating a draft -- rebuild
        # the FULL desired image set server-side from the catalog entry's
        # existing Image rows so approval-time apply reconciles correctly
        # (existing-image removals are otherwise dropped on this branch).
        # Otherwise this is a plain create: store the uploads as-is.
        edit_marking_marker = _parse_int(submitted_data.get("edit_marking_id"))
        edit_cover_marker = _parse_int(submitted_data.get("edit_cover_id"))
        if is_cover_submission and edit_cover_marker is not None:
            _apply_fresh_edit_image_metas(
                submitted_data,
                data,
                image_metas,
                subject_type=Image.SUBJECT_COVER,
                subject_id=edit_cover_marker,
                meta_key="cover_image_metas",
                image_order=image_order,
            )
        elif (not is_cover_submission) and edit_marking_marker is not None:
            _apply_fresh_edit_image_metas(
                submitted_data,
                data,
                image_metas,
                subject_type=Image.SUBJECT_MARKING,
                subject_id=edit_marking_marker,
                meta_key="marking_image_metas",
                image_order=image_order,
            )
        elif image_metas:
            submitted_data["marking_image_metas"] = image_metas
            if is_cover_submission:
                submitted_data["cover_image_metas"] = image_metas
            submitted_data["image_metas"] = image_metas
            submitted_data["image_meta"] = image_metas[0]

        contrib = Contribution.objects.create(
            contributor=request.user,
            collection=collection,
            submitted_data=submitted_data,
            status=Contribution.STATUS_DRAFT if is_draft else Contribution.STATUS_PENDING,
            created_by=request.user,
            modified_by=request.user,
        )
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_SUBMIT,
            actor=request.user,
            contribution=contrib,
            marking=None,
            source=SubmissionTransaction.SOURCE_CONTRIBUTOR_PORTAL,
            before_payload={},
            after_payload=submitted_data,
        )
        return Response(
            ContributionDetailSerializer(contrib, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


def _save_contribution_image(uploaded_file, region_abbrev):
    """
    Save an uploaded marking image under MEDIA_ROOT/<region_abbrev>/
    and return a metadata dict suitable for Contribution.submitted_data.

    Returns dict with storage_filename, original_filename, file_checksum,
    mime_type, image_width, image_height, file_size_bytes; or None if the
    file is missing, oversize, or not a recognized image format.

    storage_filename is returned as '<region_abbrev>/<uuid>.<ext>'. Public
    URLs are built by ImageSerializer.get_image_url, which serves from
    MEDIA_URL directly (e.g. /media/<region_abbrev>/<uuid>.<ext>).
    """
    from common.images import extract_image_metadata

    if not uploaded_file or not getattr(uploaded_file, "read", None):
        return None
    content_type = getattr(uploaded_file, "content_type", "") or ""
    max_size_bytes = 100 * 1024 * 1024  # 100 MB
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    content = uploaded_file.read()
    if not content or len(content) > max_size_bytes:
        return None
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    metadata = extract_image_metadata(content, content_type)
    if metadata is None:
        return None
    if "png" in content_type:
        ext = "png"
    elif "tiff" in content_type:
        ext = "tiff"
    else:
        ext = "jpg"
    abbrev = (region_abbrev or "").strip().lower() or "unknown"
    storage_name = f"{abbrev}/{uuid.uuid4().hex}.{ext}"
    sub_dir = os.path.join(settings.MEDIA_ROOT, abbrev)
    os.makedirs(sub_dir, exist_ok=True)
    file_path = os.path.join(settings.MEDIA_ROOT, storage_name)
    with open(file_path, "wb") as f:
        f.write(content)
    return {
        "storage_filename": storage_name,
        "original_filename": (getattr(uploaded_file, "name", "image") or "image")[:255],
        **metadata,
    }


def _parse_removed_image_keys(raw):
    """Parse removed_existing_image_keys (a JSON string or a list) into a list
    of URL strings. Returns [] for anything else."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [str(k) for k in parsed if k] if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return [str(k) for k in raw if k]
    return []


def _storage_filename_removed(storage_filename, removed_set):
    """True when storage_filename matches one of the removed URL keys. Mirrors
    the _meta_was_removed matcher: normalize both (strip leading slash) and test
    URL-tail against the storage_filename."""
    sf = str(storage_filename or "").lstrip("/")
    if not sf:
        return False
    for k in removed_set:
        kn = str(k).lstrip("/")
        if kn == sf or kn.endswith(sf):
            return True
    return False


def _parse_existing_image_tags(raw):
    """Parse existing_image_tags ({url: "tracing"|"photograph"}) from a JSON
    string or dict into a {str: str} map. Returns {} otherwise."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _existing_image_tag_value(storage_filename, existing_tags):
    """Return the tag for an image whose URL key in existing_tags matches the
    storage_filename (URL-tail match, legacy 'markings/' prefix stripped), or
    None when no key matches."""
    sfn = str(storage_filename or "").lstrip("/")
    if sfn.startswith("markings/"):
        sfn = sfn[len("markings/"):]
    for url, tag in existing_tags.items():
        urln = str(url or "").lstrip("/")
        if urln.startswith("markings/"):
            urln = urln[len("markings/"):]
        if urln == sfn or urln.endswith(sfn):
            return tag
    return None


def _normalize_order_key(value):
    """Normalize a storage_filename or image URL for tail-matching: strip a
    leading slash and the legacy 'markings/' prefix. Mirrors the normalization
    used by _existing_image_tag_value / _storage_filename_removed."""
    s = str(value or "").lstrip("/")
    if s.startswith("markings/"):
        s = s[len("markings/"):]
    return s


def _reorder_metas_by_image_order(combined, new_metas, image_order):
    """Return `combined` reordered to match the contributor-chosen `image_order`.

    image_order is a list of string tokens, one per displayed image, in the
    desired order. Each token is either the literal "__new__" (a newly-uploaded
    file, consumed FIFO from new_metas) or an existing image's key (its media
    URL / storage_filename tail, matched against each kept meta's
    storage_filename with the same tail-match used elsewhere).

    `combined` is the full meta list (kept metas + new_metas) in its current
    order; `new_metas` is this submit's newly-uploaded metas (a subset of
    `combined`, matched by identity). When image_order is missing, empty, or not
    a list, `combined` is returned unchanged. Any meta not consumed by a token is
    appended at the end (preserving `combined` order) so nothing is ever dropped.
    """
    if not isinstance(image_order, list) or not image_order:
        return combined
    new_ids = {id(m) for m in new_metas}
    kept = [m for m in combined if id(m) not in new_ids and isinstance(m, dict)]
    new_queue = [m for m in combined if id(m) in new_ids]

    used = set()
    result = []
    for token in image_order:
        tok = str(token)
        if tok == "__new__":
            if new_queue:
                m = new_queue.pop(0)
                result.append(m)
                used.add(id(m))
            continue
        tkey = _normalize_order_key(tok)
        if not tkey:
            continue
        for m in kept:
            if id(m) in used:
                continue
            mkey = _normalize_order_key(m.get("storage_filename"))
            if mkey and (mkey == tkey or tkey.endswith(mkey) or mkey.endswith(tkey)):
                result.append(m)
                used.add(id(m))
                break

    # Safety: append anything the tokens did not account for, in original order.
    for m in combined:
        if id(m) not in used:
            result.append(m)
            used.add(id(m))
    return result


def _apply_existing_image_reconciliation(
    existing_sd, data, image_metas, is_cover, image_order=None
):
    """
    Draft-resume image reconciliation. Mutates existing_sd in place to honor
    contributor-side removals (removed_existing_image_keys) against the draft's
    prior submitted_data (marking_images URL list, the *_metas lists, and the
    image_meta thumbnail pointer), then computes the merged meta set when there
    are new uploads. Returns a dict of submitted_data updates (the *_metas /
    image_metas / image_meta keys) for the caller to apply; an empty dict when
    there are no new uploads.
    """
    removed_keys = _parse_removed_image_keys(data.get("removed_existing_image_keys"))
    if removed_keys:
        removed_set = set(removed_keys)
        # marking_images is a list of URL strings; drop direct hits.
        existing_marking_images = existing_sd.get("marking_images")
        if isinstance(existing_marking_images, list):
            existing_sd["marking_images"] = [
                u for u in existing_marking_images if u not in removed_set
            ]
        for meta_key in ("marking_image_metas", "cover_image_metas", "image_metas"):
            metas_list = existing_sd.get(meta_key)
            if isinstance(metas_list, list):
                existing_sd[meta_key] = [
                    m
                    for m in metas_list
                    if not (
                        isinstance(m, dict)
                        and _storage_filename_removed(
                            m.get("storage_filename"), removed_set
                        )
                    )
                ]
        # image_meta is the catalog-default thumbnail pointer; if it was just
        # removed, replace it with the next surviving meta (or None if none).
        primary = existing_sd.get("image_meta")
        if isinstance(primary, dict) and _storage_filename_removed(
            primary.get("storage_filename"), removed_set
        ):
            replacement = None
            for fallback_key in ("marking_image_metas", "image_metas", "cover_image_metas"):
                fallback_list = existing_sd.get(fallback_key)
                if isinstance(fallback_list, list) and fallback_list:
                    replacement = fallback_list[0]
                    break
            existing_sd["image_meta"] = replacement

    updates = {}
    # When the contributor only reordered prior images (no new uploads),
    # image_metas is empty but image_order still needs to be applied to the
    # prior list so the new order persists on the draft.
    if image_metas or image_order:
        meta_key = "cover_image_metas" if is_cover else "marking_image_metas"
        prior = existing_sd.get(meta_key) or existing_sd.get("image_metas") or []
        if not isinstance(prior, list):
            prior = []
        new_metas = list(image_metas or [])
        merged_metas = list(prior) + new_metas
        merged_metas = _reorder_metas_by_image_order(
            merged_metas, new_metas, image_order
        )
        updates[meta_key] = merged_metas
        updates["image_metas"] = merged_metas
        updates["image_meta"] = (
            merged_metas[0] if merged_metas else existing_sd.get("image_meta")
        )
    return updates


def _apply_fresh_edit_image_metas(
    submitted_data, data, image_metas, *, subject_type, subject_id, meta_key,
    image_order=None,
):
    """
    Build the FULL desired image set for a PENDING edit submitted fresh (no
    draft, so edit_pk is None). The database is the source of truth for kept
    images: query the edit target's existing Image rows, drop the ones the
    contributor removed (removed_existing_image_keys), convert each kept row
    into a submitted_data meta record (storage_filename + dimensions + a
    'tracing' flag derived from existing_image_tags, else the DB is_tracing),
    put the kept records first, then append the newly uploaded files' records.
    When image_order is supplied, reorder the combined set to the
    contributor-chosen sequence (kept images and new uploads may interleave, and
    any image may become the default at index 0). Store the combined set under
    meta_key + image_metas + image_meta so approval-time apply (_sync_images)
    reconciles correctly.
    """
    removed_set = set(_parse_removed_image_keys(data.get("removed_existing_image_keys")))
    existing_tags = _parse_existing_image_tags(data.get("existing_image_tags"))

    kept_records = []
    rows = Image.objects.filter(
        subject_type=subject_type, subject_id=subject_id
    ).order_by("display_order", "image_id")
    for row in rows:
        if _storage_filename_removed(row.storage_filename, removed_set):
            continue
        tag = _existing_image_tag_value(row.storage_filename, existing_tags)
        tracing = (tag == "tracing") if tag is not None else bool(row.is_tracing)
        kept_records.append(
            {
                "storage_filename": row.storage_filename,
                "original_filename": row.original_filename or "",
                "file_checksum": row.file_checksum or "",
                "mime_type": row.mime_type or "",
                "image_width": row.image_width or 0,
                "image_height": row.image_height or 0,
                "file_size_bytes": row.file_size_bytes or 0,
                "image_description": row.image_description or "",
                "tracing": tracing,
            }
        )

    new_metas = list(image_metas or [])
    combined = kept_records + new_metas
    combined = _reorder_metas_by_image_order(combined, new_metas, image_order)
    submitted_data[meta_key] = combined
    submitted_data["image_metas"] = combined
    submitted_data["image_meta"] = combined[0] if combined else None


###################################################################################################
## Collection (institutional unit, F7)
###################################################################################################
class CollectionViewSet(viewsets.ModelViewSet):
    """
    Reads: any authenticated user.
    Writes (incl. assign / unassign editor): Administrators only.
    """
    queryset = Collection.objects.select_related("region").all()
    serializer_class = CollectionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["region", "is_active"]
    search_fields = ["name", "description", "region__name", "region__abbrev"]
    ordering_fields = ["name", "created_date"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "editors"):
            return [IsAuthenticated()]
        return [IsAdminUser()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    @action(detail=True, methods=["get"], url_path="editors")
    def editors(self, request, pk=None):
        collection = self.get_object()
        rows = collection.editor_assignments.select_related("user").order_by("user__username")
        return Response([
            {
                "id": ca.pk,
                "user_id": ca.user_id,
                "username": ca.user.username,
                "email": getattr(ca.user, "email", "") or "",
            }
            for ca in rows
        ])

    @action(detail=True, methods=["post"], url_path="assign-editor")
    def assign_editor(self, request, pk=None):
        collection = self.get_object()
        user_id = request.data.get("user_id")
        try:
            user_id = int(user_id) if user_id is not None else None
        except (TypeError, ValueError):
            user_id = None
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        target = User.objects.filter(pk=user_id).first()
        if not target:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        ca, created = CollectionAssignment.objects.get_or_create(
            user=target,
            collection=collection,
            defaults={"created_by": request.user, "modified_by": request.user},
        )
        return Response(
            {
                "id": ca.pk,
                "user_id": target.pk,
                "username": target.username,
                "collection_id": collection.pk,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["delete"], url_path="unassign-editor/(?P<user_id>[^/.]+)")
    def unassign_editor(self, request, pk=None, user_id=None):
        collection = self.get_object()
        try:
            uid = int(user_id) if user_id is not None else None
        except (TypeError, ValueError):
            uid = None
        if not uid:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = CollectionAssignment.objects.filter(
            user_id=uid, collection=collection
        ).delete()
        if not deleted:
            return Response({"detail": "Assignment not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


###################################################################################################
