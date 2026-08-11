from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.db.models import Max
from django.utils import timezone

from common.models import (
    Citation,
    Cover,
    CoverVersion,
    DateSeen,
    Image,
    Marking,
    MarkingVersion,
    SubmissionTransaction,
)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def build_marking_snapshot(marking: Marking | None) -> dict[str, Any]:
    if not marking:
        return {}

    post_office = getattr(marking, "post_office", None)
    # PostOffice.region is a property that resolves to the most-recent active
    # Region via the post_office_regions junction. May be None if no link exists.
    region = post_office.region if post_office else None
    images = (
        Image.objects.filter(subject_type=Image.SUBJECT_MARKING, subject_id=marking.pk)
        .order_by("display_order")
        .values(
            "original_filename",
            "storage_filename",
            "file_checksum",
            "mime_type",
            "image_width",
            "image_height",
            "file_size_bytes",
            "image_view",
            "image_description",
            "display_order",
        )
    )
    citations = Citation.objects.filter(
        subject_type="MARKING",
        subject_id=marking.pk,
    ).order_by("reference_work_id", "citation_detail").values(
        "reference_work_id",
        "citation_detail",
    )
    dates_seen = DateSeen.objects.filter(
        subject_type=DateSeen.SUBJECT_MARKING,
        subject_id=marking.pk,
    ).order_by("date", "date_year", "date_month", "date_day", "granularity").values(
        "date",
        "granularity",
        "date_year",
        "date_month",
        "date_day",
    )

    return _json_safe(
        {
            "marking_id": marking.pk,
            "code": marking.code,
            "type": marking.type,
            "catalog_txt": marking.catalog_txt,
            "inscription_txt": marking.inscription_txt,
            "desc": marking.desc,
            "post_office_id": marking.post_office_id,
            "town": post_office.name if post_office else "",
            "region_id": region.id if region else None,
            "state": region.name if region else "",
            "shape_id": marking.shape_id,
            "lettering_id": marking.lettering_id,
            "color_id": marking.color_id,
            "is_manuscript": marking.is_manuscript,
            "impression": marking.impression,
            "is_irreg": marking.is_irreg,
            "width": marking.width,
            "height": marking.height,
            "date_fmt": marking.date_fmt,
            "rate_val": marking.rate_val,
            "images": list(images),
            "citations": list(citations),
            "dates_seen": list(dates_seen),
            "captured_at": timezone.now(),
        }
    )


def compute_payload_diff(before_payload: Any, after_payload: Any) -> list[dict[str, Any]]:
    before_dict = before_payload if isinstance(before_payload, dict) else {}
    after_dict = after_payload if isinstance(after_payload, dict) else {}
    keys = sorted(set(before_dict.keys()) | set(after_dict.keys()))
    diff: list[dict[str, Any]] = []
    for key in keys:
        if key == "captured_at":
            continue
        before_val = before_dict.get(key)
        after_val = after_dict.get(key)
        if before_val != after_val:
            diff.append(
                {
                    "field": key,
                    "before": _json_safe(before_val),
                    "after": _json_safe(after_val),
                }
            )
    return diff


def log_submission_transaction(
    *,
    action: str,
    actor=None,
    contribution=None,
    marking=None,
    cover=None,
    source: str = SubmissionTransaction.SOURCE_SYSTEM,
    before_payload: Any = None,
    after_payload: Any = None,
    extra_payload: Any = None,
) -> SubmissionTransaction:
    before_safe = _json_safe(before_payload or {})
    after_safe = _json_safe(after_payload or {})
    extra_safe = _json_safe(extra_payload or {})
    return SubmissionTransaction.objects.create(
        transaction_uuid=uuid4(),
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        contribution=contribution,
        marking=marking,
        cover=cover,
        source=source,
        before_payload=before_safe,
        after_payload=after_safe,
        diff_payload=compute_payload_diff(before_safe, after_safe),
        extra_payload=extra_safe,
    )


def create_marking_version(marking: Marking, transaction: SubmissionTransaction | None, actor=None) -> MarkingVersion:
    latest = MarkingVersion.objects.filter(marking=marking).aggregate(max_no=Max("version_no"))["max_no"] or 0
    return MarkingVersion.objects.create(
        marking=marking,
        version_no=latest + 1,
        snapshot=build_marking_snapshot(marking),
        transaction=transaction,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


def log_marking_removed(marking: Marking, removed_by, reason: str = "") -> SubmissionTransaction:
    """
    Record that a marking was soft-removed into the recycle bin. Snapshots a
    MarkingVersion so the pre-removal state is durably captured, and writes a
    SubmissionTransaction with the actor and reason. Does NOT create the
    MarkingRecycleBin row -- the caller owns that so the whole operation is one
    atomic block.
    """
    before = build_marking_snapshot(marking)
    txn = log_submission_transaction(
        action=SubmissionTransaction.ACTION_MARKING_REMOVED,
        actor=removed_by,
        contribution=getattr(marking, "contribution", None),
        marking=marking,
        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
        before_payload=before,
        after_payload={},
        extra_payload={"reason": reason or "", "removed_marking_id": marking.pk},
    )
    create_marking_version(marking, txn, removed_by)
    return txn


def log_marking_restored(marking: Marking, restored_by) -> SubmissionTransaction:
    """Record that a marking was restored from the recycle bin."""
    after = build_marking_snapshot(marking)
    return log_submission_transaction(
        action=SubmissionTransaction.ACTION_MARKING_RESTORED,
        actor=restored_by,
        contribution=getattr(marking, "contribution", None),
        marking=marking,
        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
        before_payload={},
        after_payload=after,
        extra_payload={"restored_marking_id": marking.pk},
    )


def restore_marking_from_snapshot(marking: Marking, snapshot: dict[str, Any], actor) -> Marking:
    """
    Restore a Marking from a snapshot dict. Phase 1 stub: scalar-field restore
    only. Image reattachment is deferred to the Phase 2 contribution rewrite,
    which owns Image lifecycle for both COVER and MARKING subjects. DateSeen
    rows are now polymorphic and live alongside Image / Citation under the same
    (subject_type, subject_id) pattern; reattaching them is also deferred.
    """
    if not isinstance(snapshot, dict):
        return marking

    marking.code = snapshot.get("code")
    marking.type = snapshot.get("type") or marking.type
    marking.catalog_txt = snapshot.get("catalog_txt")
    marking.inscription_txt = snapshot.get("inscription_txt") or ""
    marking.desc = snapshot.get("desc")
    marking.post_office_id = snapshot.get("post_office_id")
    marking.shape_id = snapshot.get("shape_id")
    marking.lettering_id = snapshot.get("lettering_id")
    marking.color_id = snapshot.get("color_id")
    marking.is_manuscript = bool(snapshot.get("is_manuscript"))
    marking.impression = snapshot.get("impression")
    marking.is_irreg = snapshot.get("is_irreg")
    marking.width = snapshot.get("width")
    marking.height = snapshot.get("height")
    marking.date_fmt = snapshot.get("date_fmt")
    marking.rate_val = snapshot.get("rate_val")
    if actor and getattr(actor, "is_authenticated", False):
        marking.modified_by = actor
    marking.save()

    user_for_related = actor if actor and getattr(actor, "is_authenticated", False) else marking.modified_by

    Citation.objects.filter(subject_type="MARKING", subject_id=marking.pk).delete()
    for row in snapshot.get("citations", []) or []:
        ref_id = row.get("reference_work_id")
        if not ref_id:
            continue
        Citation.objects.create(
            reference_work_id=ref_id,
            subject_type="MARKING",
            subject_id=marking.pk,
            citation_detail=(row.get("citation_detail") or "").strip(),
            created_by=user_for_related,
            modified_by=user_for_related,
        )

    return marking


def build_cover_snapshot(cover: Cover | None) -> dict[str, Any]:
    """Snapshot of a cover's scalar fields plus its polymorphic children
    (images, dates_seen, citations), cover-marking links and valuations.
    Mirrors build_marking_snapshot."""
    if not cover:
        return {}

    images = (
        Image.objects.filter(subject_type=Image.SUBJECT_COVER, subject_id=cover.pk)
        .order_by("display_order")
        .values(
            "original_filename",
            "storage_filename",
            "file_checksum",
            "mime_type",
            "image_width",
            "image_height",
            "file_size_bytes",
            "image_view",
            "image_description",
            "display_order",
        )
    )
    dates_seen = (
        DateSeen.objects.filter(subject_type="COVER", subject_id=cover.pk)
        .order_by("date", "date_year", "date_month", "date_day", "granularity")
        .values("date", "granularity", "date_year", "date_month", "date_day")
    )
    citations = (
        Citation.objects.filter(subject_type="COVER", subject_id=cover.pk)
        .order_by("reference_work_id", "citation_detail")
        .values("reference_work_id", "citation_detail")
    )
    cover_markings = cover.cover_markings.values(
        "marking_id", "is_backstamp", "placement", "review_status"
    )
    valuations = cover.valuations.values("amt", "appraisal_date")

    return _json_safe(
        {
            "cover_id": cover.pk,
            "code": cover.code,
            "type": cover.type,
            "color_id": cover.color_id,
            "has_adhesive": cover.has_adhesive,
            "height": cover.height,
            "width": cover.width,
            "is_institutional": cover.is_institutional,
            "images": list(images),
            "dates_seen": list(dates_seen),
            "citations": list(citations),
            "cover_markings": list(cover_markings),
            "valuations": list(valuations),
            "captured_at": timezone.now(),
        }
    )


def create_cover_version(cover: Cover, transaction: SubmissionTransaction | None, actor=None) -> CoverVersion:
    latest = CoverVersion.objects.filter(cover=cover).aggregate(max_no=Max("version_no"))["max_no"] or 0
    return CoverVersion.objects.create(
        cover=cover,
        version_no=latest + 1,
        snapshot=build_cover_snapshot(cover),
        transaction=transaction,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


def log_cover_removed(cover: Cover, removed_by, reason: str = "") -> SubmissionTransaction:
    """
    Record that a cover was soft-removed into the recycle bin. Snapshots a
    CoverVersion so the pre-removal state is durably captured, and writes a
    SubmissionTransaction with the actor and reason. Does NOT create the
    CoverRecycleBin row -- the caller owns that so the whole operation is one
    atomic block. Mirrors log_marking_removed.
    """
    before = build_cover_snapshot(cover)
    txn = log_submission_transaction(
        action=SubmissionTransaction.ACTION_COVER_REMOVED,
        actor=removed_by,
        cover=cover,
        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
        before_payload=before,
        after_payload={},
        extra_payload={"reason": reason or "", "removed_cover_id": cover.pk},
    )
    create_cover_version(cover, txn, removed_by)
    return txn


def log_cover_restored(cover: Cover, restored_by) -> SubmissionTransaction:
    """Record that a cover was restored from the recycle bin."""
    after = build_cover_snapshot(cover)
    return log_submission_transaction(
        action=SubmissionTransaction.ACTION_COVER_RESTORED,
        actor=restored_by,
        cover=cover,
        source=SubmissionTransaction.SOURCE_EDITOR_PORTAL,
        before_payload={},
        after_payload=after,
        extra_payload={"restored_cover_id": cover.pk},
    )


def restore_cover_from_snapshot(cover: Cover, snapshot: dict[str, Any], actor) -> Cover:
    """
    Restore a Cover from a snapshot dict. Phase 1 stub: scalar-field restore
    plus citation reattachment only. Image reattachment is deferred to the
    Phase 2 contribution rewrite, which owns Image lifecycle for both COVER and
    MARKING subjects. DateSeen rows are polymorphic and live alongside Image /
    Citation under the same (subject_type, subject_id) pattern; reattaching them
    is also deferred. Mirrors restore_marking_from_snapshot.
    """
    if not isinstance(snapshot, dict):
        return cover

    cover.code = snapshot.get("code")
    cover.type = snapshot.get("type")
    cover.color_id = snapshot.get("color_id")
    cover.has_adhesive = bool(snapshot.get("has_adhesive"))
    cover.height = snapshot.get("height")
    cover.width = snapshot.get("width")
    cover.is_institutional = snapshot.get("is_institutional")
    if actor and getattr(actor, "is_authenticated", False):
        cover.modified_by = actor
    cover.save()

    user_for_related = actor if actor and getattr(actor, "is_authenticated", False) else cover.modified_by

    Citation.objects.filter(subject_type="COVER", subject_id=cover.pk).delete()
    for row in snapshot.get("citations", []) or []:
        ref_id = row.get("reference_work_id")
        if not ref_id:
            continue
        Citation.objects.create(
            reference_work_id=ref_id,
            subject_type="COVER",
            subject_id=cover.pk,
            citation_detail=(row.get("citation_detail") or "").strip(),
            created_by=user_for_related,
            modified_by=user_for_related,
        )

    return cover
