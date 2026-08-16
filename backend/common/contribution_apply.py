"""
Apply an approved Contribution to the catalog.

The approve view in backend/common/api/v2/views.py wraps a call to
apply_contribution_to_catalog(contrib) in transaction.atomic(); this
module must therefore NOT open its own transaction. On any error it
raises -- ContributionApplyError for malformed submission data (becomes
a 500 with a field-specific message).

Scope:
  * Marking submissions (type in TOWNMARK/RATEMARK/AUXMARK): create one
    Marking + Image rows unless no image was explicitly affirmed + zero-or-more Citation rows.
    apply_contribution_to_catalog returns the created Marking.
  * Cover submissions (submission_kind == "cover" or type in FC/FL):
    create one Cover + one CoverMarking (linked to the parent marking,
    review_status=APPROVED) + zero-or-one DateSeen + Image
    rows + zero-or-more Citation rows + zero-or-one CoverValuation. The
    cover branch returns a dict, NOT a Marking -- see
    apply_cover_contribution_to_catalog for the exact shape. The approve
    view (common/api/v2/views.py ContributionViewSet.approve) branches on
    the return type.
  * Edit flow (update-in-place, no duplicate row):
      - Marking edit: payload carries edit_marking_id. The existing Marking
        is re-resolved and overwritten; its Images and Citations are
        reconciled against the FULL desired set in submitted_data.
        apply_contribution_to_catalog returns that existing Marking.
      - Cover edit: payload carries edit_cover_id (and usually
        edit_cover_marking_id). The existing Cover is overwritten in place
        (its code is kept), the existing CoverMarking link's positional fields
        are updated, and the cover's DateSeen / Images / Citations / Valuation
        are reconciled. apply_cover_contribution_to_catalog returns the same
        dict shape as the create path, with the existing entities.

Create vs edit share child reconcilers (_sync_images / _sync_citations /
_sync_cover_date_seen / _sync_cover_valuation): on a freshly created subject
the existing set is empty, so a _sync_* call behaves exactly like the old
create-only path.

The function reads contrib.submitted_data (a JSON blob written by the
v2 ContributionSubmitView) under the caller's transaction.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date

from common.catalog_codes import code_value_from_payload
from common.models import (
    Citation,
    Color,
    Cover,
    CoverMarking,
    CoverValuation,
    DateSeen,
    Image,
    Lettering,
    Marking,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    Shape,
)


_BOOL_TRUE = {"true", "yes", "1"}
_BOOL_FALSE = {"false", "no", "0"}

_MARKING_TYPES = ("TOWNMARK", "RATEMARK", "AUXMARK")
_COVER_TYPES = ("FC", "FL")

# Every spelling _resolve_lettering understands, so "did the payload mention
# lettering at all?" and "what does it resolve to?" stay in step.
_LETTERING_KEYS = (
    "lettering_style_id",
    "lettering",
    "lettering_id",
    "lettering_style",
    "letteringStyle",
)

# Multipart submissions arrive as "reference_work_ids[]" (the browser's array
# spelling); JSON ones as reference_work_ids / referenceWorkIds. All three are
# the same field -- catalog_codes._first_reference_work_id already reads all of
# them, and _sync_citations must agree or it deletes rows it cannot see.
_CITATION_ID_KEYS = (
    "reference_work_ids",
    "referenceWorkIds",
    "reference_work_ids[]",
)


class ContributionApplyError(ValueError):
    """Raised when contribution submitted_data is malformed.

    The approve view surfaces this as a 500 with the error message
    visible to the editor, so messages should name the specific field
    that is missing or invalid.
    """


def apply_contribution_to_catalog(contrib):
    """
    Apply an approved Contribution to the catalog by creating a Marking
    (+ Images + Citations) from contrib.submitted_data. Caller must wrap
    in a transaction; this function does not.
    """
    payload = contrib.submitted_data or {}
    actor = contrib.contributor

    submission_kind = payload.get("submission_kind") or payload.get("submissionKind")
    sub_type = payload.get("type")
    if submission_kind == "cover" or sub_type in _COVER_TYPES:
        return apply_cover_contribution_to_catalog(contrib)
    if sub_type not in _MARKING_TYPES:
        raise ContributionApplyError(
            "Marking type is required and must be one of "
            "TOWNMARK/RATEMARK/AUXMARK; got {!r}.".format(sub_type)
        )

    edit_marking_id = _parse_int(payload.get("edit_marking_id"))
    if edit_marking_id is not None:
        return _apply_marking_edit(contrib, payload, actor, edit_marking_id)

    state = _required_str(payload, "state")
    town = _required_str(payload, "town")
    inscription = _required_str(payload, "inscription_txt")

    is_manuscript = _coerce_required_bool(payload, "is_manuscript")

    post_office = _resolve_post_office(state, town, actor)

    if is_manuscript:
        shape = None
        lettering = None
        is_irreg = None
    else:
        shape = _resolve_fk(Shape, payload, "shape_id", "shape")
        lettering = _resolve_lettering(payload)
        is_irreg = _coerce_required_bool(payload, "is_irreg")

    color = _resolve_fk(Color, payload, "color_id", "color")

    width = _parse_decimal(payload.get("width_mm") or payload.get("widthMm"))
    height = _parse_decimal(payload.get("height_mm") or payload.get("heightMm"))

    desc_raw = (payload.get("desc") or payload.get("description") or "")
    if isinstance(desc_raw, str):
        desc_raw = desc_raw.strip()
    else:
        desc_raw = str(desc_raw).strip()
    display_submitter_name = _coerce_optional_bool(payload, "display_submitter_name", False)

    date_fmt = payload.get("date_fmt") or payload.get("dateFmt") or None
    if isinstance(date_fmt, str):
        date_fmt = date_fmt.strip() or None

    impression = payload.get("impression") or None
    if isinstance(impression, str):
        impression = impression.strip() or None

    marking_kwargs = dict(
        code=code_value_from_payload(payload) or None,
        type=sub_type,
        inscription_txt=inscription,
        desc=desc_raw or None,
        is_manuscript=is_manuscript,
        shape=shape,
        lettering=lettering,
        is_irreg=is_irreg,
        width=width,
        height=height,
        date_fmt=date_fmt,
        impression=impression,
        rate_val=_parse_decimal(payload.get("rate_val")),
        post_office=post_office,
        display_submitter_name=bool(display_submitter_name),
        created_by=actor,
        modified_by=actor,
    )
    if color is not None:
        marking_kwargs["color"] = color

    marking = Marking(**marking_kwargs)
    # full_clean() is belt-and-braces; the explicit checks above produce
    # the friendly messages, but this catches anything we missed (e.g.
    # check constraints we did not enumerate).
    marking.full_clean()
    marking.save()

    _sync_images(
        Image.SUBJECT_MARKING,
        marking.pk,
        payload,
        actor,
        image_view="FULL",
        metas_keys=("marking_image_metas", "image_metas"),
        tags_key="marking_image_tags",
    )
    _sync_citations("MARKING", marking.pk, payload, actor)
    _sync_marking_dates_seen(marking.pk, payload, actor)

    return marking


def _apply_marking_edit(contrib, payload: dict, actor, marking_id: int) -> Marking:
    """
    Apply an approved marking-EDIT contribution in place: merge the submitted
    scalars into the existing row (no new Marking), then reconcile its Images
    and Citations. Returns the updated Marking -- the same contract as the
    create path.

    Scalars follow RFC 7396 merge semantics, matching _apply_cover_edit: a key
    the payload never mentions leaves the stored value alone, and an explicitly
    empty value clears it. The submit form states emptiness explicitly (""),
    so "the contributor cleared this box" still reads as a clear.

    This is load-bearing, not stylistic. Rebuilding the row from the payload
    instead meant any producer that spoke to a subset of columns silently
    NULLed the rest -- and full_clean() passed, so the approval returned 200
    with a clean audit trail. The VPHC ingest speaks to none of impression,
    date_fmt, rate_val or desc; approving its 310 edits under the old behavior
    nulled impression on all 288 matched markings.

    Marking.all_objects is used so a pending edit still applies even if the
    entry was recycle-binned after the edit was submitted; the removal sidecar
    (MarkingRecycleBin) is left untouched, so the marking stays binned.

    Caller owns transaction.atomic(); this does not open one.
    """
    try:
        marking = Marking.all_objects.get(pk=marking_id)
    except Marking.DoesNotExist:
        raise ContributionApplyError(
            "Unknown marking id for edit: {}".format(marking_id)
        )

    # Resolve with the SAME helpers the create path uses so create and edit
    # validate identically; the difference is only which fields get assigned.
    state = _required_str(payload, "state")
    town = _required_str(payload, "town")
    inscription = _required_str(payload, "inscription_txt")
    is_manuscript = _coerce_required_bool(payload, "is_manuscript")
    post_office = _resolve_post_office(state, town, actor)

    # Required by the submission contract, so always restated.
    catalog_code = code_value_from_payload(payload)
    if catalog_code:
        marking.code = catalog_code
    marking.inscription_txt = inscription
    marking.is_manuscript = is_manuscript
    marking.post_office = post_office

    if is_manuscript:
        # Derived, not submitted: a manuscript marking has no shape, lettering
        # or irregularity, so these clear whether or not the payload said so.
        marking.shape = None
        marking.lettering = None
        marking.is_irreg = None
    else:
        if _payload_mentions_fk(payload, "shape_id", "shape"):
            marking.shape = _resolve_fk(Shape, payload, "shape_id", "shape")
        if _payload_mentions(payload, *_LETTERING_KEYS):
            marking.lettering = _resolve_lettering(payload)
        if "is_irreg" in payload:
            marking.is_irreg = _coerce_optional_bool(
                payload, "is_irreg", marking.is_irreg
            )

    # type was validated against _MARKING_TYPES by the dispatch before this ran.
    if "type" in payload:
        marking.type = payload.get("type")
    if _payload_mentions_fk(payload, "color_id", "color"):
        marking.color = _resolve_fk(Color, payload, "color_id", "color")
    if _payload_mentions(payload, "width_mm", "widthMm"):
        marking.width = _parse_decimal(
            payload.get("width_mm") or payload.get("widthMm")
        )
    if _payload_mentions(payload, "height_mm", "heightMm"):
        marking.height = _parse_decimal(
            payload.get("height_mm") or payload.get("heightMm")
        )
    if _payload_mentions(payload, "desc", "description"):
        desc_raw = payload.get("desc") or payload.get("description") or ""
        desc_raw = (
            desc_raw.strip() if isinstance(desc_raw, str) else str(desc_raw).strip()
        )
        marking.desc = desc_raw or None
    if _payload_mentions(payload, "date_fmt", "dateFmt"):
        date_fmt = payload.get("date_fmt") or payload.get("dateFmt") or None
        if isinstance(date_fmt, str):
            date_fmt = date_fmt.strip() or None
        marking.date_fmt = date_fmt
    if "impression" in payload:
        impression = payload.get("impression") or None
        if isinstance(impression, str):
            impression = impression.strip() or None
        marking.impression = impression
    if "rate_val" in payload:
        marking.rate_val = _parse_decimal(payload.get("rate_val"))
    if "display_submitter_name" in payload:
        marking.display_submitter_name = bool(
            _coerce_optional_bool(
                payload, "display_submitter_name", marking.display_submitter_name
            )
        )
    marking.modified_by = actor
    marking.full_clean()
    marking.save()

    _sync_images(
        Image.SUBJECT_MARKING,
        marking.pk,
        payload,
        actor,
        image_view="FULL",
        metas_keys=("marking_image_metas", "image_metas"),
        tags_key="marking_image_tags",
    )
    _sync_citations("MARKING", marking.pk, payload, actor)
    _sync_marking_dates_seen(marking.pk, payload, actor)

    return marking


def apply_cover_contribution_to_catalog(contrib) -> dict:
    """
    Materialize an approved cover Contribution into the catalog: create one
    Cover, one CoverMarking (linked to the parent Marking, review_status
    APPROVED), zero-or-one DateSeen, one-or-more Image rows, zero-or-more
    Citation rows, and zero-or-one CoverValuation -- all from
    contrib.submitted_data. The caller (ContributionViewSet.approve) owns the
    transaction.atomic(); this function MUST NOT open its own.

    Returns a dict so the approve view can tell covers from markings:
        {
            "kind": "cover",
            "cover": <Cover>,
            "cover_marking": <CoverMarking>,    # review_status == APPROVED
            "parent_marking": <Marking>,
        }
    The CoverMarking.reviewer is left null here (the approving editor is not
    available in this function); the approve view backfills reviewer /
    review_notes after this returns.
    """
    payload = contrib.submitted_data or {}
    actor = contrib.contributor

    # Parent marking: the form sends both parent_marking_id and marking_id.
    parent_id = None
    for k in ("parent_marking_id", "marking_id", "marking"):
        raw = payload.get(k)
        if raw in (None, ""):
            continue
        try:
            parent_id = int(raw)
            break
        except (TypeError, ValueError):
            continue
    if parent_id is None:
        raise ContributionApplyError(
            "parent_marking_id (or marking_id) is required for a cover submission."
        )
    # all_objects so a recycle-binned parent still resolves (mirrors
    # _resolve_parent_marking_post_office in common/api/v2/views.py).
    try:
        parent_marking = Marking.all_objects.get(pk=parent_id)
    except Marking.DoesNotExist:
        raise ContributionApplyError("Unknown parent marking id: {}".format(parent_id))

    edit_cover_id = _parse_int(payload.get("edit_cover_id") or payload.get("editCoverId"))
    if edit_cover_id is not None:
        return _apply_cover_edit(
            contrib,
            payload,
            actor,
            parent_marking,
            edit_cover_id,
            _parse_int(
                payload.get("edit_cover_marking_id")
                or payload.get("editCoverMarkingId")
            ),
        )

    cover_type = payload.get("type")
    if isinstance(cover_type, str):
        cover_type = cover_type.strip().upper() or None
    if cover_type is not None and cover_type not in _COVER_TYPES:
        raise ContributionApplyError(
            "Cover type must be FC or FL; got {!r}.".format(cover_type)
        )

    color = _resolve_fk(Color, payload, "color_id", "color")
    has_adhesive = _coerce_optional_bool(payload, "has_adhesive", False)
    is_institutional = _coerce_optional_bool(payload, "is_institutional", None)
    display_submitter_name = _coerce_optional_bool(payload, "display_submitter_name", False)
    raw_description = payload.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    width = _parse_decimal(payload.get("width_mm") or payload.get("widthMm"))
    height = _parse_decimal(payload.get("height_mm") or payload.get("heightMm"))

    cover = Cover(
        code=code_value_from_payload(payload) or None,
        type=cover_type,
        color=color,
        has_adhesive=bool(has_adhesive),
        is_institutional=is_institutional,
        display_submitter_name=bool(display_submitter_name),
        description=description,
        width=width,
        height=height,
        created_by=actor,
        modified_by=actor,
    )
    # Code is either supplied by the editor review path or assigned inside
    # Cover.save() as a legacy direct-create fallback.
    cover.full_clean(exclude=["code"])
    cover.save()

    is_backstamp = _coerce_optional_bool(payload, "is_backstamp", False)
    placement = payload.get("placement")
    if isinstance(placement, str):
        placement = placement.strip() or None
    else:
        placement = None
    comment_raw = payload.get("contributor_comment") or payload.get("comment_for_editor") or ""
    comment = comment_raw.strip() if isinstance(comment_raw, str) else str(comment_raw).strip()

    cover_marking, _created = CoverMarking.objects.get_or_create(
        cover=cover,
        marking=parent_marking,
        defaults=dict(
            is_backstamp=bool(is_backstamp),
            placement=placement,
            contributor_comment=comment or None,
            review_status=CoverMarking.REVIEW_APPROVED,
            reviewed_at=timezone.now(),
            created_by=actor,
            modified_by=actor,
        ),
    )

    _sync_cover_date_seen(cover.pk, payload, actor)
    _sync_images(
        Image.SUBJECT_COVER,
        cover.pk,
        payload,
        actor,
        image_view="FRONT",
        metas_keys=("cover_image_metas", "image_metas"),
        tags_key="cover_image_tags",
    )
    _sync_citations("COVER", cover.pk, payload, actor)
    _sync_cover_valuation(cover.pk, payload, actor)

    return {
        "kind": "cover",
        "cover": cover,
        "cover_marking": cover_marking,
        "parent_marking": parent_marking,
    }


def _apply_cover_edit(
    contrib,
    payload: dict,
    actor,
    parent_marking,
    cover_id: int,
    cover_marking_id,
) -> dict:
    """
    Apply an approved cover-EDIT contribution in place: overwrite the existing
    Cover's scalar fields (keeping its assigned code), update the existing
    CoverMarking link's positional fields, and reconcile the cover's DateSeen /
    Images / Citations / Valuation. Returns the SAME dict shape as the cover
    create path, with the existing entities.

    Cover.all_objects is used so a pending edit still applies even if the cover
    was recycle-binned after submission; the removal sidecar (CoverRecycleBin)
    is left untouched.

    The CoverMarking review_status / reviewer / reviewed_at are deliberately
    left alone here -- the approve view backfills reviewer / review_notes /
    reviewed_at (B2c). Caller owns transaction.atomic(); this does not open one.

    Scalar fields are updated only when the submission actually carries them, so
    a partial cover-edit form (which today omits color / has_adhesive / width /
    height / placement) does not wipe editor-entered values. Absent == keep
    existing.
    """
    try:
        cover = Cover.all_objects.get(pk=cover_id)
    except Cover.DoesNotExist:
        raise ContributionApplyError(
            "Unknown cover id for edit: {}".format(cover_id)
        )

    raw_type = payload.get("type")
    if isinstance(raw_type, str):
        raw_type = raw_type.strip().upper() or None
    if raw_type is not None:
        if raw_type not in _COVER_TYPES:
            raise ContributionApplyError(
                "Cover type must be FC or FL; got {!r}.".format(raw_type)
            )
        cover.type = raw_type

    color = _resolve_fk(Color, payload, "color_id", "color")
    if color is not None:
        cover.color = color
    if "has_adhesive" in payload:
        cover.has_adhesive = bool(
            _coerce_optional_bool(payload, "has_adhesive", cover.has_adhesive)
        )
    if "is_institutional" in payload:
        cover.is_institutional = _coerce_optional_bool(
            payload, "is_institutional", cover.is_institutional
        )
    if "display_submitter_name" in payload:
        cover.display_submitter_name = bool(
            _coerce_optional_bool(
                payload, "display_submitter_name", cover.display_submitter_name
            )
        )
    if "description" in payload:
        raw_description = payload.get("description")
        cover.description = (
            raw_description.strip() if isinstance(raw_description, str) else ""
        )
    width = _parse_decimal(payload.get("width_mm") or payload.get("widthMm"))
    if width is not None:
        cover.width = width
    height = _parse_decimal(payload.get("height_mm") or payload.get("heightMm"))
    if height is not None:
        cover.height = height
    catalog_code = code_value_from_payload(payload)
    if catalog_code:
        cover.code = catalog_code

    cover.modified_by = actor
    # code is kept (already assigned on the existing row); exclude from validation.
    cover.full_clean(exclude=["code"])
    cover.save()

    # Resolve the existing link. Prefer the explicit cover_marking_id, fall back
    # to the (cover, parent_marking) pair. NOT get_or_create: a missing link is
    # a real error for an edit, and creating one risks a second link.
    cover_marking = None
    if cover_marking_id is not None:
        cover_marking = CoverMarking.objects.filter(pk=cover_marking_id).first()
    if cover_marking is None:
        cover_marking = CoverMarking.objects.filter(
            cover=cover, marking=parent_marking
        ).first()
    if cover_marking is None:
        raise ContributionApplyError(
            "No CoverMarking link found for cover {} and marking {}.".format(
                cover.pk, parent_marking.pk
            )
        )

    cover_marking.is_backstamp = bool(
        _coerce_optional_bool(payload, "is_backstamp", cover_marking.is_backstamp)
    )
    if "placement" in payload:
        raw_placement = payload.get("placement")
        cover_marking.placement = (
            (raw_placement.strip() or None)
            if isinstance(raw_placement, str)
            else None
        )
    if "contributor_comment" in payload or "comment_for_editor" in payload:
        comment_raw = (
            payload.get("contributor_comment")
            or payload.get("comment_for_editor")
            or ""
        )
        comment = (
            comment_raw.strip()
            if isinstance(comment_raw, str)
            else str(comment_raw).strip()
        )
        cover_marking.contributor_comment = comment or None
    cover_marking.modified_by = actor
    # Do NOT touch review_status / reviewer / reviewed_at / created_by here; the
    # approve view backfills reviewer / review_notes / reviewed_at.
    cover_marking.save(
        update_fields=[
            "is_backstamp",
            "placement",
            "contributor_comment",
            "modified_by",
        ]
    )

    _sync_cover_date_seen(cover.pk, payload, actor)
    _sync_images(
        Image.SUBJECT_COVER,
        cover.pk,
        payload,
        actor,
        image_view="FRONT",
        metas_keys=("cover_image_metas", "image_metas"),
        tags_key="cover_image_tags",
    )
    _sync_citations("COVER", cover.pk, payload, actor)
    _sync_cover_valuation(cover.pk, payload, actor)

    return {
        "kind": "cover",
        "cover": cover,
        "cover_marking": cover_marking,
        "parent_marking": parent_marking,
    }


def _required_str(payload: dict, key: str) -> str:
    raw = payload.get(key)
    if raw is None:
        raise ContributionApplyError("{} is required.".format(key))
    s = str(raw).strip()
    if not s:
        raise ContributionApplyError("{} is required.".format(key))
    return s


def _coerce_required_bool(payload: dict, key: str) -> bool:
    """
    Parse a required boolean from payload[key]. Accepts True/False, 1/0,
    or the strings "true"/"false"/"yes"/"no" (case-insensitive). Any
    other value -- including missing key -- raises.
    """
    if key not in payload:
        raise ContributionApplyError(
            "{} is required and must be true or false.".format(key)
        )
    raw = payload[key]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        if raw == 1:
            return True
        if raw == 0:
            return False
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
    raise ContributionApplyError(
        "{} is required and must be true or false.".format(key)
    )


def _coerce_optional_bool(payload: dict, key: str, default):
    """
    Parse an optional boolean from payload[key]. Returns `default` when the key
    is missing, empty, or unparseable (lenient -- these cover fields are
    optional). Accepts True/False, 1/0, or the strings
    "true"/"false"/"yes"/"no" (case-insensitive).
    """
    if key not in payload:
        return default
    raw = payload[key]
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        if raw == 1:
            return True
        if raw == 0:
            return False
        return default
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
    return default


def _payload_affirms_no_images(payload: dict, subject_type) -> bool:
    if subject_type == Image.SUBJECT_COVER:
        keys = ("no_cover_image", "noCoverImage", "cover_no_image", "coverNoImage")
    else:
        keys = (
            "no_marking_image",
            "noMarkingImage",
            "marking_no_image",
            "markingNoImage",
        )
    for key in (*keys, "no_image", "noImage"):
        if _coerce_optional_bool(payload, key, False):
            return True
    return False


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return None
    return None


def _parse_int(value: Any) -> int | None:
    """Tolerant int parser. Returns None for None/""/unparseable values so a
    missing edit marker simply means "not an edit" rather than an error."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_fk(model, payload: dict, id_key: str, name_key: str, *fallback_id_keys: str):
    """
    Try id_key (and any fallback_id_keys) by primary key; on missing id
    that does not resolve, raise. Then try name_key by case-insensitive
    name match. Returns None if nothing resolves.
    """
    id_keys: tuple[str, ...] = (id_key,) + fallback_id_keys
    for k in id_keys:
        raw = payload.get(k)
        if raw is None or raw == "":
            continue
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            continue
        try:
            return model.objects.get(pk=pk)
        except model.DoesNotExist:
            raise ContributionApplyError(
                "Unknown {} id: {}".format(model.__name__.lower(), pk)
            )
    raw_name = payload.get(name_key)
    if raw_name is None:
        return None
    name = str(raw_name).strip()
    if not name:
        return None
    return model.objects.filter(name__iexact=name).first()


def _payload_mentions(payload: dict, *keys: str) -> bool:
    """
    Did the submission speak to this field at all? An edit merges rather than
    replaces (RFC 7396): a key the payload never mentions leaves the stored
    value alone; only an explicitly empty value clears it.
    """
    return any(key in payload for key in keys)


def _payload_mentions_fk(payload: dict, id_key: str, name_key: str, *fallback_id_keys: str) -> bool:
    return _payload_mentions(payload, id_key, name_key, *fallback_id_keys)


def _resolve_lettering(payload: dict) -> Lettering | None:
    """
    Lettering accepts nested-object shapes from older form payloads
    (lettering_style.lettering_style_id, letteringStyle.letteringStyleId)
    in addition to the flat id_key / name_key path used by _resolve_fk.
    """
    nested_id = _read_nested_id(
        payload.get("lettering_style"), "lettering_style_id", "letteringStyleId"
    )
    if nested_id is None:
        nested_id = _read_nested_id(
            payload.get("letteringStyle"), "lettering_style_id", "letteringStyleId"
        )
    if nested_id is not None:
        try:
            return Lettering.objects.get(pk=nested_id)
        except Lettering.DoesNotExist:
            raise ContributionApplyError(
                "Unknown lettering id: {}".format(nested_id)
            )
    return _resolve_fk(
        Lettering,
        payload,
        "lettering_style_id",
        "lettering",
        "lettering_id",
    )


def _read_nested_id(value: Any, snake: str, camel: str) -> int | None:
    if not isinstance(value, dict):
        return None
    v = value.get(snake)
    if v is None:
        v = value.get(camel)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _resolve_post_office(state_name: str, town_name: str, actor) -> PostOffice:
    """
    Resolve a PostOffice for (state_name, town_name), auto-creating the
    town if it does not yet exist in that state. Regions are NOT
    auto-created; an unknown state is a hard error.
    """
    region = (
        Region.objects.filter(name__iexact=state_name).first()
        or Region.objects.filter(abbrev__iexact=state_name).first()
    )
    if region is None:
        raise ContributionApplyError("Unknown state: {}".format(state_name))

    normalized_name = " ".join(town_name.strip().split()).title()
    po = (
        PostOffice.objects
        .filter(name__iexact=normalized_name, post_office_regions__region=region)
        .first()
    )
    if po is None:
        po = PostOffice.objects.create(
            name=normalized_name,
            created_by=actor,
            modified_by=actor,
        )
    PostOfficeRegion.objects.get_or_create(
        post_office=po,
        region=region,
        defaults={"created_by": actor, "modified_by": actor},
    )
    return po


def _normalize_storage(value: Any) -> str:
    """
    Normalize a storage_filename or an image URL to a comparable tail. Strips a
    leading slash and a legacy 'markings/' prefix so that a public image URL
    (MEDIA_URL + storage_filename, built by ImageSerializer.get_image_url) and a
    raw storage_filename reduce to forms that can be tail-matched.
    """
    s = str(value or "").lstrip("/")
    if s.startswith("markings/"):
        s = s[len("markings/"):]
    return s


def _parse_tag_list(raw: Any) -> list[str]:
    """Positional tracing tags for newly uploaded files: a JSON string or a
    list of strings like ["tracing", "photograph"]. Returns [] otherwise."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def _parse_tag_map(raw: Any) -> dict[str, str]:
    """existing_image_tags is a {url: "tracing"|"photograph"} map for kept
    images. Tolerate a JSON string or a dict; returns {} otherwise."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _existing_tag_for(sf: str, existing_tags: dict[str, str]) -> str | None:
    """
    Return the tag for a kept image whose URL key in existing_tags matches the
    normalized storage_filename sf, or None when no key matches. URL keys are
    public image URLs; match the normalized URL tail against sf (mirrors
    _meta_was_removed in common/api/v2/views.py).
    """
    for url, tag in existing_tags.items():
        urln = _normalize_storage(url)
        if urln == sf or urln.endswith(sf):
            return tag
    return None


def _meta_is_tracing(meta: dict) -> bool:
    """Read a tracing flag baked into a meta record (B3 stores a 'tracing'
    boolean on kept-image records; tolerate a 'tag' string too)."""
    if not isinstance(meta, dict):
        return False
    val = meta.get("tracing")
    if isinstance(val, bool):
        return val
    if val is not None:
        return str(val).strip().lower() in _BOOL_TRUE
    tag = meta.get("tag")
    if isinstance(tag, str):
        return tag.strip().lower() == "tracing"
    return False


def _sync_images(
    subject_type,
    subject_id,
    payload: dict,
    actor,
    *,
    image_view: str,
    metas_keys: tuple[str, ...],
    tags_key: str,
) -> None:
    """
    Reconcile a subject's Image rows against the FULL desired set in
    submitted_data. On a freshly created subject the existing set is empty, so
    this behaves exactly like the old create-only path (every meta is a new
    upload). On an edit, kept images are retained + reordered + retagged, new
    uploads are created, and rows absent from the desired set are deleted.

    subject_type: Image.SUBJECT_MARKING or Image.SUBJECT_COVER.
    image_view:   "FULL" for markings, "FRONT" for covers (a marking's "FULL"
                  is rejected by the DB check constraint for COVER).
    metas_keys:   ordered keys to find the FULL desired meta list, first
                  non-empty wins, e.g. ("marking_image_metas", "image_metas").
    tags_key:     positional tracing tags for NEW uploads, e.g.
                  "marking_image_tags".

    Tag rule: kept rows take existing_image_tags keyed by URL-tail match (with a
    fallback to a 'tracing' flag baked into the meta); new uploads take the
    positional tags in tags_key, indexed over the new uploads only.

    At least one image is required unless the contributor explicitly affirmed
    that no image is available.
    """
    metas = None
    for k in metas_keys:
        candidate = payload.get(k)
        if isinstance(candidate, list) and len(candidate) > 0:
            metas = candidate
            break
    if not metas:
        if _payload_affirms_no_images(payload, subject_type):
            Image.objects.filter(
                subject_type=subject_type,
                subject_id=subject_id,
            ).delete()
            return
        raise ContributionApplyError(
            "Submission has no images; at least one image is required."
        )

    new_tags = _parse_tag_list(payload.get(tags_key))
    existing_tags = _parse_tag_map(payload.get("existing_image_tags"))

    current = {}
    for row in Image.objects.filter(subject_type=subject_type, subject_id=subject_id):
        current[_normalize_storage(row.storage_filename)] = row

    desired: list[str] = []
    new_index = 0
    for i, meta in enumerate(metas):
        if not isinstance(meta, dict):
            raise ContributionApplyError(
                "{}[{}] is not an object.".format(metas_keys[0], i)
            )
        raw_sf = meta.get("storage_filename")
        if not raw_sf:
            raise ContributionApplyError(
                "{}[{}].storage_filename is required.".format(metas_keys[0], i)
            )
        sf = _normalize_storage(raw_sf)
        desired.append(sf)
        row = current.get(sf)
        if row is not None:
            # Kept image: tag from existing_image_tags by URL-tail; fall back to
            # a flag baked into the meta record. Retain the row, update its
            # order and tag.
            tag = _existing_tag_for(sf, existing_tags)
            if tag is not None:
                is_tracing = tag == "tracing"
            else:
                is_tracing = _meta_is_tracing(meta)
            row.is_tracing = is_tracing
            row.display_order = i
            row.modified_by = actor
            row.save(
                update_fields=["is_tracing", "display_order", "modified_by"]
            )
        else:
            # New upload: positional tag over the new uploads only.
            is_tracing = (
                new_index < len(new_tags) and new_tags[new_index] == "tracing"
            )
            new_index += 1
            Image.objects.create(
                subject_type=subject_type,
                subject_id=subject_id,
                original_filename=str(meta.get("original_filename", "") or ""),
                storage_filename=str(raw_sf),
                file_checksum=str(meta.get("file_checksum", "") or ""),
                mime_type=str(meta.get("mime_type", "") or ""),
                image_width=int(meta.get("image_width") or 0),
                image_height=int(meta.get("image_height") or 0),
                file_size_bytes=int(meta.get("file_size_bytes") or 0),
                image_view=image_view,
                image_description=str(meta.get("image_description", "") or ""),
                is_tracing=is_tracing,
                display_order=i,
                uploaded_by=actor,
                created_by=actor,
                modified_by=actor,
            )

    # Removals: any current row not in the desired set is dropped.
    for sf, row in current.items():
        if sf not in desired:
            row.delete()


def _sync_citations(subject_type: str, subject_id, payload: dict, actor) -> None:
    """
    Replace all Citation rows for (subject_type, subject_id) with the set built
    from reference_work_ids + reference_work_details. Deletes the existing rows
    first, then recreates; an explicitly empty id list therefore clears
    citations. On a freshly created subject the delete is a no-op, so this
    matches the old create-only behavior. subject_type is the Citation subject
    string ("MARKING" | "COVER").

    A payload that never mentions the field is not a request to delete: an edit
    merges rather than replaces (RFC 7396), and an ingest that speaks only to
    the columns it knows about must not wipe a marking's existing bibliography.
    Clearing citations requires an explicitly empty list.
    """
    if not _payload_mentions(payload, *_CITATION_ID_KEYS):
        return

    ids_raw = None
    for key in _CITATION_ID_KEYS:
        if key in payload:
            ids_raw = payload.get(key)
            break

    # A multipart QueryDict is flattened to its last value at submit time, so
    # this arrives as a bare id rather than a list. Treat it as the one-element
    # list it is instead of discarding it.
    if isinstance(ids_raw, (str, int)) and not isinstance(ids_raw, bool):
        ids_raw = [ids_raw] if str(ids_raw).strip() else []

    if ids_raw and not isinstance(ids_raw, (list, tuple)):
        raise ContributionApplyError(
            "reference_work_ids must be a list of ids."
        )

    # Guard, extract and validate all happen above, so there is no path that
    # deletes without either repopulating or having been told to clear.
    Citation.objects.filter(
        subject_type=subject_type, subject_id=subject_id
    ).delete()

    if not ids_raw:
        return

    details_raw = payload.get("reference_work_details")
    if details_raw is None:
        details_raw = payload.get("referenceWorkDetails")
    details_list: list[dict] = []
    if isinstance(details_raw, str):
        try:
            parsed = json.loads(details_raw)
            if isinstance(parsed, list):
                details_list = [d for d in parsed if isinstance(d, dict)]
        except (ValueError, TypeError):
            details_list = []
    elif isinstance(details_raw, list):
        details_list = [d for d in details_raw if isinstance(d, dict)]

    detail_by_id: dict[int, dict] = {}
    for entry in details_list:
        try:
            rwid = int(entry.get("reference_work_id"))
        except (TypeError, ValueError):
            continue
        detail_by_id[rwid] = entry

    for rid_raw in ids_raw:
        try:
            rwid = int(rid_raw)
        except (TypeError, ValueError):
            raise ContributionApplyError(
                "Invalid reference work id: {!r}".format(rid_raw)
            )
        try:
            rw = ReferenceWork.objects.get(pk=rwid)
        except ReferenceWork.DoesNotExist:
            raise ContributionApplyError(
                "Unknown reference work id: {}".format(rwid)
            )
        detail = detail_by_id.get(rwid, {})
        page_number = str(detail.get("page_number") or "").strip()
        url = str(detail.get("url") or "").strip()
        if page_number and url:
            citation_detail = "p. {} - {}".format(page_number, url)
        elif page_number:
            citation_detail = "p. {}".format(page_number)
        elif url:
            citation_detail = url
        else:
            citation_detail = ""
        if len(citation_detail) > 500:
            citation_detail = citation_detail[:500]
        Citation.objects.create(
            reference_work=rw,
            subject_type=subject_type,
            subject_id=subject_id,
            citation_detail=citation_detail,
            created_by=actor,
            modified_by=actor,
        )


def _sync_cover_date_seen(cover_id, payload: dict, actor) -> None:
    """
    Reconcile the cover's DateSeen to zero-or-one row.

    New submissions may send component keys:
    cover_date_year / cover_date_month / cover_date_day, or
    cover_date_unknown=true. Legacy cover_date + cover_granularity payloads
    continue to work.
    """
    DateSeen.objects.filter(
        subject_type=DateSeen.SUBJECT_COVER, subject_id=cover_id
    ).delete()
    if _coerce_optional_bool(payload, "cover_date_unknown", False):
        return

    has_component = any(
        payload.get(key) not in (None, "")
        for key in ("cover_date_year", "cover_date_month", "cover_date_day")
    )
    if has_component:
        row = DateSeen(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=cover_id,
            date_year=_parse_int(payload.get("cover_date_year")),
            date_month=_parse_int(payload.get("cover_date_month")),
            date_day=_parse_int(payload.get("cover_date_day")),
            created_by=actor,
            modified_by=actor,
        )
        try:
            row.normalize_date_parts()
        except DjangoValidationError as exc:
            raise ContributionApplyError("Invalid cover date components: {}".format(exc))
        row.save()
        return

    raw = payload.get("cover_date") or payload.get("coverDate")
    if raw in (None, ""):
        return
    parsed = parse_date(str(raw)[:10])
    if parsed is None:
        raise ContributionApplyError("Invalid cover_date: {!r}".format(raw))
    granularity = payload.get("cover_granularity") or payload.get("coverGranularity") or "DAY"
    if isinstance(granularity, str):
        granularity = granularity.strip().upper() or "DAY"
    if granularity not in ("DAY", "MONTH", "YEAR"):
        raise ContributionApplyError("Invalid cover_granularity: {!r}".format(granularity))
    DateSeen.objects.create(
        subject_type=DateSeen.SUBJECT_COVER,
        subject_id=cover_id,
        date=parsed,
        granularity=granularity,
        created_by=actor,
        modified_by=actor,
    )


# Form keys carrying a manually-entered marking ERD/LRD (+ granularity), read
# by _sync_marking_dates_seen below. Setting a marking's date is editor-only
# (issue #27): the contribution submit handler strips these from a non-editor's
# submission, exactly as it strips CATALOG_CODE_KEYS. Kept here, next to the
# code that consumes them, so the key list has one home.
MARKING_DATE_SUBMIT_KEYS = frozenset({
    "marking_erd",
    "marking_erd_granularity",
    "marking_erd_unknown",
    "marking_erd_date_year",
    "marking_erd_date_month",
    "marking_erd_date_day",
    "marking_lrd",
    "marking_lrd_granularity",
    "marking_lrd_unknown",
    "marking_lrd_date_year",
    "marking_lrd_date_month",
    "marking_lrd_date_day",
})


def strip_marking_date_keys(data: dict) -> dict:
    """Drop manually-entered marking date keys (editor-only; issue #27)."""
    return {k: v for k, v in data.items() if k not in MARKING_DATE_SUBMIT_KEYS}


def _sync_marking_dates_seen(marking_id, payload: dict, actor) -> None:
    """
    Record manually-entered ERD/LRD as MARKING-subject DateSeen rows from
    marking_erd / marking_lrd (+ *_granularity). Used when a marking is added
    or edited from another catalog and has no covers to derive dates from.

    No date fields means no-op, preserving imported history. Explicit
    *_unknown=true means the editor is replacing direct marking-level date
    evidence with unknown, so direct MARKING DateSeen rows are cleared first.
    Cover-derived dates live under subject_type=COVER and are out of scope here.

    Each non-blank concrete boundary is get_or_create'd at its (subject, date),
    so a same-date row is reused rather than duplicated. The marking date-range
    cache (common.date_range) takes min/max across all rows.
    """
    boundary_specs = (
        (
            "marking_erd",
            "marking_erd_granularity",
            "marking_erd_unknown",
            "marking_erd_date_year",
            "marking_erd_date_month",
            "marking_erd_date_day",
        ),
        (
            "marking_lrd",
            "marking_lrd_granularity",
            "marking_lrd_unknown",
            "marking_lrd_date_year",
            "marking_lrd_date_month",
            "marking_lrd_date_day",
        ),
    )

    if any(_coerce_optional_bool(payload, spec[2], False) for spec in boundary_specs):
        DateSeen.objects.filter(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=marking_id,
        ).delete()

    for date_key, gran_key, unknown_key, year_key, month_key, day_key in boundary_specs:
        if _coerce_optional_bool(payload, unknown_key, False):
            continue
        has_component = any(
            payload.get(key) not in (None, "")
            for key in (year_key, month_key, day_key)
        )
        if has_component:
            row = DateSeen(
                subject_type=DateSeen.SUBJECT_MARKING,
                subject_id=marking_id,
                date_year=_parse_int(payload.get(year_key)),
                date_month=_parse_int(payload.get(month_key)),
                date_day=_parse_int(payload.get(day_key)),
                created_by=actor,
                modified_by=actor,
            )
            try:
                row.normalize_date_parts()
            except DjangoValidationError as exc:
                raise ContributionApplyError("Invalid {} components: {}".format(date_key, exc))
            DateSeen.objects.get_or_create(
                subject_type=DateSeen.SUBJECT_MARKING,
                subject_id=marking_id,
                granularity=row.granularity,
                date_year=row.date_year,
                date_month=row.date_month,
                date_day=row.date_day,
                defaults={
                    "date": row.date,
                    "created_by": actor,
                    "modified_by": actor,
                },
            )
            continue

        raw = payload.get(date_key)
        if raw in (None, ""):
            continue
        parsed = parse_date(str(raw)[:10])
        if parsed is None:
            raise ContributionApplyError("Invalid {}: {!r}".format(date_key, raw))
        granularity = payload.get(gran_key) or "DAY"
        if isinstance(granularity, str):
            granularity = granularity.strip().upper() or "DAY"
        if granularity not in ("DAY", "MONTH", "YEAR"):
            raise ContributionApplyError(
                "Invalid {}: {!r}".format(gran_key, granularity)
            )
        row = DateSeen(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=marking_id,
            date=parsed,
            granularity=granularity,
            created_by=actor,
            modified_by=actor,
        )
        try:
            row.normalize_date_parts()
        except DjangoValidationError as exc:
            raise ContributionApplyError("Invalid {}: {}".format(date_key, exc))
        DateSeen.objects.get_or_create(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=marking_id,
            granularity=row.granularity,
            date_year=row.date_year,
            date_month=row.date_month,
            date_day=row.date_day,
            defaults={
                "date": row.date,
                "created_by": actor,
                "modified_by": actor,
            },
        )


def _sync_cover_valuation(cover_id, payload: dict, actor) -> None:
    """
    Reconcile the cover's CoverValuation from valuation_amt /
    valuation_appraisal_date.

    Valuation is NOT part of the current draft/edit form payload, so when
    neither field is present this is a no-op that leaves any existing valuation
    untouched -- a missing field must never wipe an editor-entered valuation.
    When either field IS present, the existing valuations are replaced with the
    submitted one (delete-then-create). On a freshly created cover the delete is
    a no-op, so this matches the old create-only behavior.
    """
    amt_raw = payload.get("valuation_amt") or payload.get("valuationAmt")
    date_raw = payload.get("valuation_appraisal_date") or payload.get("valuationAppraisalDate")
    if amt_raw in (None, "") and date_raw in (None, ""):
        return
    CoverValuation.objects.filter(cover_id=cover_id).delete()
    amt = _parse_decimal(amt_raw)
    appraisal_date = parse_date(str(date_raw)[:10]) if date_raw else None
    if amt is None and appraisal_date is None:
        return
    CoverValuation.objects.create(
        cover_id=cover_id,
        amt=amt,
        appraisal_date=appraisal_date,
        created_by=actor,
        modified_by=actor,
    )


__all__ = [
    "apply_contribution_to_catalog",
    "apply_cover_contribution_to_catalog",
    "ContributionApplyError",
]
