from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import BigIntegerField, Max, Model, TextField, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, NullIf, Substr, Trim

from common.models import Citation, Contribution, Cover, Marking, ReferenceWork, Region


DEFAULT_REFERENCE_CODE = "APMC"
# Every key a minted suggestion writes, so that stripping one strips all of them.
# The three sidecars used to be omitted here, which meant each strip site removed
# `catalog_code` and left `catalog_code_reference_code` behind -- so a viewer
# without catalog-code permission still saw the reference code the strip existed
# to hide, and a cleanup that removed the code left orphans. Found while tracing
# issue #118.
CATALOG_CODE_KEYS = {
    "code",
    "catalog_code",
    "catalogCode",
    "suggested_catalog_code",
    "suggestedCatalogCode",
    "catalog_code_source",
    "catalogCodeSource",
    "catalog_code_reference_code",
    "catalogCodeReferenceCode",
    "catalog_code_region_abbrev",
    "catalogCodeRegionAbbrev",
}
# A catalog serial is the whole suffix after the prefix: four or more digits and
# nothing else. Written as an explicit character class rather than `\d` because
# this is handed to the database's regex engine, where `\d` may also match
# non-ASCII digits.
_SERIAL_PATTERN = r"^[0-9]{4,}$"


class CatalogCodeError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogCodeSuggestion:
    catalog_code: str
    prefix: str
    reference_code: str
    region_abbrev: str
    subject_type: str
    source: str


def normalize_catalog_code(value: Any) -> str:
    return str(value or "").strip()


def strip_catalog_code_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in CATALOG_CODE_KEYS}


def code_value_from_payload(payload: dict[str, Any]) -> str:
    for key in ("catalog_code", "catalogCode", "code"):
        value = normalize_catalog_code(payload.get(key))
        if value:
            return value
    return ""


def validate_unique_catalog_code(
    *,
    subject_type: str,
    code: str,
    exclude_id: int | None = None,
) -> str:
    normalized = normalize_catalog_code(code)
    if not normalized:
        raise CatalogCodeError("Catalog code is required.")
    model = _model_for_subject(subject_type)
    qs = model.all_objects.filter(code=normalized)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise CatalogCodeError("Catalog code already exists: {}".format(normalized))
    return normalized


def suggest_for_contribution(
    contrib: Contribution,
    *,
    force: bool = False,
) -> CatalogCodeSuggestion:
    payload = dict(contrib.submitted_data or {})
    existing = code_value_from_payload(payload)
    if existing and not force:
        return _suggestion_from_existing(contrib, existing)

    suggestion = _build_contribution_suggestion(contrib, payload)
    payload["catalog_code"] = suggestion.catalog_code
    payload["catalog_code_source"] = suggestion.source
    payload["catalog_code_reference_code"] = suggestion.reference_code
    payload["catalog_code_region_abbrev"] = suggestion.region_abbrev
    contrib.submitted_data = payload
    contrib.save(update_fields=["submitted_data", "modified_date"])
    return suggestion


def final_code_for_contribution(
    contrib: Contribution,
    *,
    requested_code: str | None,
) -> CatalogCodeSuggestion:
    payload = dict(contrib.submitted_data or {})
    override = normalize_catalog_code(requested_code)
    if override:
        suggestion = _suggestion_from_existing(contrib, override)
    else:
        stored = code_value_from_payload(payload)
        suggestion = (
            _suggestion_from_existing(contrib, stored)
            if stored
            else _build_contribution_suggestion(contrib, payload)
        )
    exclude_id = _existing_subject_id(contrib, suggestion.subject_type, payload)
    final_code = validate_unique_catalog_code(
        subject_type=suggestion.subject_type,
        code=suggestion.catalog_code,
        exclude_id=exclude_id,
    )
    if final_code != suggestion.catalog_code:
        suggestion = CatalogCodeSuggestion(
            catalog_code=final_code,
            prefix=suggestion.prefix,
            reference_code=suggestion.reference_code,
            region_abbrev=suggestion.region_abbrev,
            subject_type=suggestion.subject_type,
            source=suggestion.source,
        )
    payload["catalog_code"] = suggestion.catalog_code
    payload["catalog_code_source"] = suggestion.source
    payload["catalog_code_reference_code"] = suggestion.reference_code
    payload["catalog_code_region_abbrev"] = suggestion.region_abbrev
    contrib.submitted_data = payload
    contrib.save(update_fields=["submitted_data", "modified_date"])
    return suggestion


def suggest_direct_catalog_code(payload: dict[str, Any]) -> CatalogCodeSuggestion:
    subject_type = _subject_type_from_payload(payload)
    if subject_type == "COVER":
        parent_marking = _parent_marking_from_payload(payload)
        region = _region_for_marking(parent_marking)
        reference_code = _reference_code_for_cover(parent_marking)
        exclude_id = _parse_positive_int(payload.get("exclude_id") or payload.get("cover_id"))
        return _build_suggestion(
            subject_type="COVER",
            prefix_letter="C",
            reference_code=reference_code,
            region=region,
            exclude_id=exclude_id,
            source="direct_cover",
        )

    region = _region_from_payload(payload)
    reference_code = _reference_code_from_selected_payload(payload)
    exclude_id = _parse_positive_int(payload.get("exclude_id") or payload.get("marking_id"))
    return _build_suggestion(
        subject_type="MARKING",
        prefix_letter="M",
        reference_code=reference_code,
        region=region,
        exclude_id=exclude_id,
        source="direct_marking",
    )


def _build_contribution_suggestion(
    contrib: Contribution,
    payload: dict[str, Any],
) -> CatalogCodeSuggestion:
    if _payload_is_cover(payload):
        parent_marking = _parent_marking_from_payload(payload)
        return _build_suggestion(
            subject_type="COVER",
            prefix_letter="C",
            reference_code=_reference_code_for_cover(parent_marking),
            region=_region_for_marking(parent_marking),
            exclude_id=_existing_subject_id(contrib, "COVER", payload),
            source="contribution_cover",
            exclude_contribution_id=contrib.pk,
        )

    region = _region_from_payload(payload)
    return _build_suggestion(
        subject_type="MARKING",
        prefix_letter="M",
        reference_code=_reference_code_from_selected_payload(payload),
        region=region,
        exclude_id=_existing_subject_id(contrib, "MARKING", payload),
        source="contribution_marking",
        exclude_contribution_id=contrib.pk,
    )


def _build_suggestion(
    *,
    subject_type: str,
    prefix_letter: str,
    reference_code: str,
    region: Region,
    exclude_id: int | None,
    source: str,
    exclude_contribution_id: int | None = None,
) -> CatalogCodeSuggestion:
    reference = normalize_catalog_code(reference_code) or DEFAULT_REFERENCE_CODE
    region_abbrev = normalize_catalog_code(region.abbrev).upper()
    if not region_abbrev:
        raise CatalogCodeError(
            "Region {} has no abbrev for catalog code generation.".format(region.pk)
        )
    prefix = "{}-{}-{}".format(reference, region_abbrev, prefix_letter)
    serial = _next_serial(
        subject_type=subject_type,
        prefix=prefix,
        exclude_id=exclude_id,
        exclude_contribution_id=exclude_contribution_id,
    )
    return CatalogCodeSuggestion(
        catalog_code="{}{:04d}".format(prefix, serial),
        prefix=prefix,
        reference_code=reference,
        region_abbrev=region_abbrev,
        subject_type=subject_type,
        source=source,
    )


def _next_serial(
    *,
    subject_type: str,
    prefix: str,
    exclude_id: int | None,
    exclude_contribution_id: int | None = None,
) -> int:
    model = _model_for_subject(subject_type)
    qs = model.all_objects.filter(code__startswith=prefix)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    max_seen = _max_serial(qs, code_expr="code", prefix=prefix)

    # Also check codes already suggested for other pending/draft contributions
    # that have not yet been approved into the Marking/Cover table.
    pending_qs = Contribution.objects.filter(
        status__in=(
            Contribution.STATUS_DRAFT,
            Contribution.STATUS_PENDING,
            Contribution.STATUS_NEEDS_REVISION,
        ),
    )
    if exclude_contribution_id is not None:
        pending_qs = pending_qs.exclude(pk=exclude_contribution_id)
    pending_qs = pending_qs.annotate(_payload_code=_payload_code_expr())
    pending_qs = pending_qs.filter(_payload_code__startswith=prefix)
    max_seen = max(max_seen, _max_serial(pending_qs, code_expr="_payload_code", prefix=prefix))

    return max_seen + 1


def _payload_code_expr():
    """SQL equivalent of code_value_from_payload: first non-blank of three keys.

    The key order and the blank handling both matter -- normalize_catalog_code
    strips whitespace before deciding a value is empty, so a key holding "   "
    must fall through to the next one exactly as it does in Python.
    """
    return Coalesce(
        *[
            NullIf(Trim(KeyTextTransform(key, "submitted_data")), Value(""))
            for key in ("catalog_code", "catalogCode", "code")
        ],
        Value(""),
        output_field=TextField(),
    )


def _max_serial(queryset, *, code_expr, prefix) -> int:
    """Largest numeric suffix in `queryset`, computed by the database.

    This used to be a Python loop over every prefixed row plus a json.loads of
    every pending contribution -- so a single approval cost one JSON parse per
    queued row, and approving a queue of N rows was O(N^2). At the VPHC ingest's
    2,443 pending rows that is ~6M parses to drain the queue. Both halves are
    now one aggregate each.

    Two things worth not re-deriving:

    * **Cast to an integer; do not compare the text.** Serials are zero-padded
      to four digits ("{:04d}"), so lexical order matches numeric order only
      below 9,999 -- "10000" sorts before "9999" as text. Sorting lexically
      would silently start re-issuing serials once a prefix crossed 10k.
    * _SERIAL_PATTERN requires the suffix to be *entirely* digits, at least four
      of them. A code with a trailing letter is skipped here exactly as the
      Python version skipped it.

    `__startswith` is case-insensitive under MariaDB's default collation while
    Python's str.startswith is not, so this can match a lowercase-prefixed code
    the old loop ignored. That direction is safe: an extra match can only raise
    the maximum, which skips a serial rather than colliding on one.
    """
    row = (
        queryset.annotate(_serial_text=Substr(code_expr, len(prefix) + 1))
        .filter(_serial_text__regex=_SERIAL_PATTERN)
        .aggregate(_max=Max(Cast("_serial_text", BigIntegerField())))
    )
    return row["_max"] or 0


def _suggestion_from_existing(
    contrib: Contribution,
    code: str,
) -> CatalogCodeSuggestion:
    payload = dict(contrib.submitted_data or {})
    subject_type = "COVER" if _payload_is_cover(payload) else "MARKING"
    normalized = normalize_catalog_code(code)
    parts = normalized.split("-")
    reference_code = parts[0] if len(parts) >= 3 else DEFAULT_REFERENCE_CODE
    region_abbrev = parts[1] if len(parts) >= 3 else ""
    prefix = "-".join(parts[:3]) if len(parts) >= 3 else normalized
    return CatalogCodeSuggestion(
        catalog_code=normalized,
        prefix=prefix,
        reference_code=reference_code or DEFAULT_REFERENCE_CODE,
        region_abbrev=region_abbrev,
        subject_type=subject_type,
        source="existing",
    )


def _model_for_subject(subject_type: str) -> type[Model]:
    normalized = str(subject_type or "").strip().upper()
    if normalized == "COVER":
        return Cover
    if normalized == "MARKING":
        return Marking
    raise CatalogCodeError("subject_type must be COVER or MARKING.")


def _subject_type_from_payload(payload: dict[str, Any]) -> str:
    raw = str(payload.get("subject_type") or payload.get("subjectType") or "").strip().upper()
    if raw in {"COVER", "MARKING"}:
        return raw
    raw = str(payload.get("kind") or payload.get("type") or "").strip().upper()
    if raw == "COVER":
        return "COVER"
    if raw == "MARKING":
        return "MARKING"
    raise CatalogCodeError("subject_type is required.")


def _payload_is_cover(payload: dict[str, Any]) -> bool:
    kind = str(payload.get("submission_kind") or payload.get("submissionKind") or "").strip().lower()
    if kind == "cover":
        return True
    if kind in {"marking", "townmark", "ratemark", "auxmark"}:
        return False
    type_value = str(payload.get("type") or "").strip().upper()
    return type_value in {"FC", "FL"} or bool(payload.get("parent_marking_id"))


def _existing_subject_id(
    contrib: Contribution,
    subject_type: str,
    payload: dict[str, Any],
) -> int | None:
    if subject_type == "MARKING":
        return _parse_positive_int(
            payload.get("edit_marking_id")
            or payload.get("editMarkingId")
            or payload.get("marking_id")
            or contrib.marking_id
        )
    return _parse_positive_int(
        payload.get("edit_cover_id")
        or payload.get("editCoverId")
        or payload.get("cover_id")
        or payload.get("coverId")
    )


def _parent_marking_from_payload(payload: dict[str, Any]) -> Marking:
    marking_id = _parse_positive_int(
        payload.get("parent_marking_id")
        or payload.get("parentMarkingId")
        or payload.get("marking_id")
        or payload.get("markingId")
        or payload.get("marking")
    )
    if marking_id is None:
        raise CatalogCodeError("parent_marking_id is required for cover catalog codes.")
    try:
        return (
            Marking.all_objects
            .select_related("post_office")
            .prefetch_related("post_office__post_office_regions__region")
            .get(pk=marking_id)
        )
    except Marking.DoesNotExist:
        raise CatalogCodeError("Unknown parent marking id: {}".format(marking_id))


def _region_for_marking(marking: Marking) -> Region:
    post_office = marking.post_office if marking and marking.post_office_id else None
    region = post_office.region if post_office else None
    if region is None:
        raise CatalogCodeError(
            "Catalog code generation requires a region on the marking post office."
        )
    return region


def _region_from_payload(payload: dict[str, Any]) -> Region:
    region_id = _parse_positive_int(payload.get("region_id") or payload.get("regionId"))
    if region_id is not None:
        region = Region.objects.filter(pk=region_id).first()
        if region is not None:
            return region
    state = normalize_catalog_code(payload.get("state") or payload.get("region"))
    if state:
        # Same trap as _resolve_post_office: a bare abbrev matched counties too.
        # Benign here only by accident -- counties carry their state's abbrev,
        # so the minted prefix came out identical -- which is exactly the kind
        # of luck that stops holding the moment #103's county rows are cleaned
        # up. Resolve it correctly rather than rely on the coincidence.
        region = Region.primary_for_state_term(state)
        if region is not None:
            return region
    marking_id = _parse_positive_int(payload.get("edit_marking_id") or payload.get("marking_id"))
    if marking_id is not None:
        marking = (
            Marking.all_objects
            .select_related("post_office")
            .prefetch_related("post_office__post_office_regions__region")
            .filter(pk=marking_id)
            .first()
        )
        if marking is not None:
            return _region_for_marking(marking)
    raise CatalogCodeError("A region/state is required for catalog code generation.")


def _reference_code_for_cover(parent_marking: Marking) -> str:
    citation = (
        Citation.objects
        .filter(subject_type="MARKING", subject_id=parent_marking.pk)
        .select_related("reference_work")
        .order_by("-created_date", "-id")
        .first()
    )
    if citation is None:
        return DEFAULT_REFERENCE_CODE
    return _reference_code_from_work(citation.reference_work)


def _reference_code_from_selected_payload(payload: dict[str, Any]) -> str:
    ref_id = _first_reference_work_id(payload)
    if ref_id is None:
        return DEFAULT_REFERENCE_CODE
    work = ReferenceWork.objects.filter(pk=ref_id).first()
    if work is None:
        return DEFAULT_REFERENCE_CODE
    return _reference_code_from_work(work)


def _reference_code_from_work(work: ReferenceWork | None) -> str:
    if work is None:
        return DEFAULT_REFERENCE_CODE
    return normalize_catalog_code(work.code) or DEFAULT_REFERENCE_CODE


def _first_reference_work_id(payload: dict[str, Any]) -> int | None:
    raw = (
        payload.get("reference_work_ids")
        or payload.get("referenceWorkIds")
        or payload.get("reference_work_ids[]")
        or payload.get("reference_work_id")
        or payload.get("referenceWorkId")
    )
    if isinstance(raw, (list, tuple)):
        for item in raw:
            parsed = _parse_positive_int(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(raw, str) and raw.strip().startswith("["):
        import json

        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                value = _parse_positive_int(item)
                if value is not None:
                    return value
    return _parse_positive_int(raw)


def _parse_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
