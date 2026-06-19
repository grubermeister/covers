"""
DRF permission classes backed by real Django RBAC.

Role mapping (see plan in
.claude/plans/currently-the-system-has-prancy-wilkinson.md):

    Guest          -> anonymous request
    Contributor    -> in `Contributors` group
    Editor         -> assigned to at least one Collection, or has
                      `common.review_contribution`
    Administrator  -> `is_superuser` (single-person admin per design)

`IsAdminUser` (DRF built-in) is used directly in views for Administrator-only
endpoints -- there is no separate Administrator group.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS

from common.models import Contribution, Region


REVIEW_CONTRIBUTION_PERM = "common.review_contribution"
APPROVE_IMAGE_PERM = "common.approve_image"


def user_assigned_collection_ids(user) -> set[int]:
    """Return the set of Collection IDs this user is assigned to as an Editor."""
    if not user or not user.is_authenticated:
        return set()
    return set(
        user.collection_assignments.values_list("collection_id", flat=True)
    )


def user_can_review_contributions(user) -> bool:
    """
    Return True when the user can enter review flows.

    CollectionAssignment is the scoped editor source of truth. The
    `common.review_contribution` permission is retained for legacy group-based
    editor accounts and broad catalog tooling.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.has_perm(REVIEW_CONTRIBUTION_PERM):
        return True
    return bool(user_assigned_collection_ids(user))


def _get_user_assigned_regions(user):
    if not user or not user.is_authenticated:
        return Region.objects.none()
    return Region.objects.filter(collection__editor_assignments__user=user).distinct()


def _user_is_responsible_for_marking(user, marking):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user_can_review_contributions(user):
        return False
    if not marking or not marking.post_office_id:
        return False
    region = marking.post_office.region
    if region is None:
        return False
    return _get_user_assigned_regions(user).filter(pk=region.pk).exists()


def _user_is_responsible_for_cover(user, cover):
    """
    A cover has no region of its own; its regions are derived from the markings
    linked to it (CoverMarking -> Marking -> post_office -> region). An editor
    is responsible for the cover if any of those regions is in their assigned
    regions. A cover with no linked markings has no region, so only a superuser
    can act on it.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user_can_review_contributions(user):
        return False
    assigned = _get_user_assigned_regions(user)
    if not assigned.exists():
        return False
    region_ids = set()
    for cm in cover.cover_markings.select_related("marking__post_office").all():
        post_office = cm.marking.post_office if cm.marking else None
        # PostOffice.region is a property resolving the most-recent active region.
        region = post_office.region if post_office else None
        if region is not None:
            region_ids.add(region.pk)
    if not region_ids:
        return False
    return assigned.filter(pk__in=region_ids).exists()


class IsEditor(BasePermission):
    """
    Granted to assigned editors, users with the review permission, or superusers.

    This is a pure role check; it does NOT scope to a specific Collection.
    Use `CanReviewContribution` when you need to also verify the user is
    assigned to the contribution's Collection.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return user_can_review_contributions(user)


class IsEditorOrAdminWrite(IsEditor):
    """
    Public reads pass; unsafe writes require an Editor or Administrator.

    Use this for direct catalog endpoints where contributors must go through
    the contribution review flow, but editors are allowed to enter live catalog
    vocabulary and records.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class CanReviewContribution(BasePermission):
    """
    Object-level: user can review (approve/reject/edit) THIS contribution.

    Allowed if:
    - superuser, or
    - is assigned to the contribution's Collection, with legacy support for
      users carrying `common.review_contribution`.

    For collection-listing actions (no object yet), allow any authenticated user
    in the Editors group; per-object filtering happens in get_queryset.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return True  # Allow contributors to list their own; per-object check below

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        # Read access: contributors can see their own; editors can see anything in their collections.
        if request.method in SAFE_METHODS:
            if getattr(obj, "contributor_id", None) == user.id:
                return True
            if not user_can_review_contributions(user):
                return False
            return obj.collection_id in user_assigned_collection_ids(user)

        # Write access (approve/reject/edit): must be assigned to this Collection.
        if not user_can_review_contributions(user):
            return False
        return obj.collection_id in user_assigned_collection_ids(user)


class IsDraftOwner(BasePermission):
    """
    Object-level: user may hard-DELETE this Contribution only if it is a draft
    that they own. True DELETE is permitted exclusively for drafts
    (status=draft); a non-draft contribution can never be hard-deleted through
    this path, not even by a superuser (use the marking REMOVE flow instead).
    For drafts, the owner (contributor or editor) may delete; superusers may
    delete any draft.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        # DELETE is for drafts only -- no exceptions, including superusers.
        if getattr(obj, "status", None) != Contribution.STATUS_DRAFT:
            return False
        if user.is_superuser:
            return True
        return getattr(obj, "contributor_id", None) == user.id


class IsOwnDeletableContribution(BasePermission):
    """
    Object-level: a user may hard-DELETE (withdraw) a Contribution they own as
    long as it has NOT been approved. Draft, pending, needs_revision and
    rejected contributions have no published Marking of their own yet -- the
    catalog row is only created/updated on approval -- so deleting them has no
    downstream catalog impact, the same rationale that made draft delete safe.
    An approved contribution can never be hard-deleted through this path (not
    even by a superuser); removing the resulting published marking goes through
    the marking REMOVE / recycle-bin flow instead. The owner (contributor) may
    delete their own; superusers may delete any non-approved contribution.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        # Approved contributions are off-limits to this true-delete path.
        if getattr(obj, "status", None) == Contribution.STATUS_APPROVED:
            return False
        if user.is_superuser:
            return True
        return getattr(obj, "contributor_id", None) == user.id


class CanManageReferenceWorks(BasePermission):
    """
    Reads: anyone authenticated.
    Writes: anyone with `add_referencework` / `change_referencework` (Editors group, superuser).
    Closes the F6 gap where reference works were writable by any contributor.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if user.is_superuser:
            return True
        if request.method == "POST":
            return user.has_perm("common.add_referencework")
        if request.method in ("PUT", "PATCH"):
            return user.has_perm("common.change_referencework")
        if request.method == "DELETE":
            # Spec says "add and edit" only -- delete is admin-only.
            return False
        return False
