"""
Helpers for keeping only the latest non-draft Contribution per contributor and
catalog target.

Runbook:
  cwd: repo root
  command: .venv/bin/python backend/manage.py test common.tests.test_contribution_consolidation
  expected exit code: 0

Target shape example:
  {"kind": "marking", "id": 123}
  {"kind": "cover", "id": 456}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from common.models import Contribution, Cover, Marking, SubmissionTransaction


@dataclass(frozen=True)
class ContributionTarget:
    kind: str
    id: int


def _parse_positive_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_cover_payload(submitted_data: dict) -> bool:
    kind = str(
        submitted_data.get("submission_kind")
        or submitted_data.get("submissionKind")
        or ""
    ).strip().lower()
    if kind == "cover":
        return True
    if kind == "marking":
        return False
    type_value = str(submitted_data.get("type") or "").strip().upper()
    if type_value in {"TOWNMARK", "RATEMARK", "AUXMARK"}:
        return False
    parent = (
        submitted_data.get("parent_marking_id")
        or submitted_data.get("parentMarkingId")
        or submitted_data.get("marking_id")
        or submitted_data.get("markingId")
    )
    has_parent = _parse_positive_int(parent) is not None
    has_cover_type = type_value in {"FC", "FL"}
    has_cover_date = bool(
        str(
            submitted_data.get("cover_date")
            or submitted_data.get("coverDate")
            or ""
        ).strip()
    )
    has_town = bool(str(submitted_data.get("town") or "").strip())
    return has_parent and (has_cover_type or has_cover_date) and not has_town


def _first_positive_int(submitted_data: dict, keys: Iterable[str]) -> int | None:
    for key in keys:
        value = _parse_positive_int(submitted_data.get(key))
        if value is not None:
            return value
    return None


def contribution_target(contribution: Contribution) -> ContributionTarget | None:
    submitted_data = (
        contribution.submitted_data
        if isinstance(contribution.submitted_data, dict)
        else {}
    )
    if _is_cover_payload(submitted_data):
        cover_id = _first_positive_int(
            submitted_data,
            (
                "cover_id",
                "coverId",
                "edit_cover_id",
                "editCoverId",
                "materialized_cover_id",
                "materializedCoverId",
            ),
        )
        if cover_id is None:
            return None
        return ContributionTarget("cover", cover_id)

    marking_id = (
        _parse_positive_int(contribution.marking_id)
        or _first_positive_int(
            submitted_data,
            (
                "edit_marking_id",
                "editMarkingId",
                "original_marking_id",
                "originalMarkingId",
                "marking_id",
                "markingId",
            ),
        )
    )
    if marking_id is None:
        return None
    return ContributionTarget("marking", marking_id)


def _target_object(target: ContributionTarget):
    if target.kind == "marking":
        return Marking.all_objects.filter(pk=target.id).first()
    if target.kind == "cover":
        return Cover.all_objects.filter(pk=target.id).first()
    return None


def _same_target(contribution: Contribution, target: ContributionTarget) -> bool:
    candidate = contribution_target(contribution)
    return candidate == target


def _superseded_payload(contribution: Contribution) -> dict:
    return {
        "contribution_id": contribution.pk,
        "status": contribution.status,
        "collection_id": contribution.collection_id,
        "submitted_data": contribution.submitted_data or {},
        "created_date": contribution.created_date,
        "modified_date": contribution.modified_date,
    }


def consolidate_superseded_contributions(
    *,
    current: Contribution,
    target: ContributionTarget | None = None,
    actor=None,
    source: str = SubmissionTransaction.SOURCE_SYSTEM,
) -> int:
    """
    Delete older non-draft Contribution rows for current.contributor and target.

    Drafts are intentionally retained because a draft is still editable working
    state, not dashboard history. The current row is never deleted.
    """
    if current.status == Contribution.STATUS_DRAFT:
        return 0
    resolved = target or contribution_target(current)
    if resolved is None:
        return 0

    target_obj = _target_object(resolved)
    candidates = (
        Contribution.objects.filter(contributor=current.contributor)
        .exclude(pk=current.pk)
        .exclude(status=Contribution.STATUS_DRAFT)
        .order_by("modified_date", "pk")
    )

    from common.audit import log_submission_transaction

    deleted = 0
    for candidate in candidates:
        if not _same_target(candidate, resolved):
            continue
        log_submission_transaction(
            action=SubmissionTransaction.ACTION_CONTRIBUTION_SUPERSEDED,
            actor=actor,
            contribution=None,
            marking=target_obj if resolved.kind == "marking" else None,
            cover=target_obj if resolved.kind == "cover" else None,
            source=source,
            before_payload=_superseded_payload(candidate),
            after_payload={},
            extra_payload={
                "superseded_contribution_id": candidate.pk,
                "superseded_by_contribution_id": current.pk,
                "target_kind": resolved.kind,
                "target_id": resolved.id,
            },
        )
        candidate.delete()
        deleted += 1
    return deleted


__all__ = [
    "ContributionTarget",
    "consolidate_superseded_contributions",
    "contribution_target",
]
