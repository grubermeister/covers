"""Massachusetts-specific fixes for the v1 ASCC pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import re


BPM_REFERENCE_CODE = "BPM2"
TOWN_COL = "txtTown"
TOWN_POSTMARK_COL = "txtTownPostmark"
POSTMARK_COL = "txtPostmark"
RAW_TEXT_COL = "txtRawStateData"


@dataclass(frozen=True)
class BpmInlineRef:
    detail: str
    keyword: str


INLINE_BPM_RE = re.compile(
    r"\[\s*(?:BPM|BMP)\s+(?P<detail>[^\]]+?)\s*\]",
    re.IGNORECASE,
)
INLINE_BPM_WITH_KEYWORD_RE = re.compile(
    r"(?P<keyword>\b(?:FREE|PAID|STEAM|DUE)\b)"
    r"\s*\[\s*(?:BPM|BMP)\s+(?P<detail>[^\]]+?)\s*\]",
    re.IGNORECASE,
)
PAREN_RE = re.compile(r"\(([^)]*)\)")
SAME_HEAD_RE = re.compile(r"^\s*\+?(?:The\s+)?Same\??\b", re.IGNORECASE)
RELATION_MARKER_RE = re.compile(r"^[EL]$", re.IGNORECASE)
MONTH_NAME_RE = re.compile(
    r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|"
    r"Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|"
    r"Dec|December)\b",
    re.IGNORECASE,
)
BARE_BPM_BEFORE_BOSTON_RE = re.compile(
    r"^\s*(?P<detail>\d{1,3}[A-Z]?(?:-(?:\d+[A-Z]?|[A-Z]))?)"
    r"\s+(?=BOSTON\b)",
    re.IGNORECASE,
)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def is_boston_row(state: str, row: dict[str, str]) -> bool:
    return state.strip().upper() == "MA" and clean(row.get(TOWN_COL)).upper() == "BOSTON"


def _locator_piece_re() -> str:
    return r"\d+[A-Z]?(?:-(?:\d+[A-Z]?|[A-Z]))?(?:\s+[A-Z](?:-[A-Z])?)?"


LOCATOR_PIECE_RE = _locator_piece_re()
LOCATOR_LIST_RE = re.compile(
    r"^(?:also\s+)?"
    r"(?:" + LOCATOR_PIECE_RE + r")"
    r"(?:\s*(?:,|&)\s*(?:" + LOCATOR_PIECE_RE + r"))*"
    r"(?:\s*(?:&|and)\s*others)?$",
    re.IGNORECASE,
)
LOCATOR_WITH_OTHERS_RE = re.compile(
    r"^(?:" + LOCATOR_PIECE_RE + r")\s*(?:&|and)\s*others$",
    re.IGNORECASE,
)


def is_bpm_locator_content(value: object) -> bool:
    text = clean(value)
    if not text or ";" in text:
        return False
    lower = text.lower()
    if "star" in lower:
        return False
    if re.fullmatch(r"\d{4}", text):
        return False
    return bool(
        LOCATOR_LIST_RE.match(text)
        or LOCATOR_WITH_OTHERS_RE.match(text)
    )


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        key = clean(value).upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(clean(value))
    return out


def _is_date_annotation(value: object) -> bool:
    text = clean(value)
    if not text:
        return False
    if MONTH_NAME_RE.search(text):
        return True
    return bool(re.fullmatch(r"\d{4}(?:-\d{2,4})?", text))


def boston_head_description_lines(row: dict[str, str]) -> list[str]:
    lines = []
    for column in (RAW_TEXT_COL, POSTMARK_COL):
        value = str(row.get(column) or "")
        prefix = value[:_first_semicolon_paren_start(value)]
        for match in PAREN_RE.finditer(prefix):
            content = clean(match.group(1))
            if not content:
                continue
            if is_bpm_locator_content(content):
                continue
            if RELATION_MARKER_RE.match(content):
                continue
            if _is_date_annotation(content):
                continue
            lines.append(content)
    return _dedupe(lines)


def head_bpm_details(text: object) -> list[str]:
    value = str(text or "")
    details = []
    bare = BARE_BPM_BEFORE_BOSTON_RE.match(value)
    if bare:
        details.append(clean(bare.group("detail")))
    for match in PAREN_RE.finditer(value):
        content = match.group(1)
        if ";" in content:
            break
        if is_bpm_locator_content(content):
            details.append(clean(content))
    return _dedupe(details)


def inline_bpm_refs(text: object) -> list[BpmInlineRef]:
    value = str(text or "")
    refs = []
    for match in INLINE_BPM_WITH_KEYWORD_RE.finditer(value):
        refs.append(
            BpmInlineRef(
                detail=clean(match.group("detail")),
                keyword=clean(match.group("keyword")).upper(),
            )
        )
    if refs:
        return refs
    return [
        BpmInlineRef(detail=clean(match.group("detail")), keyword="")
        for match in INLINE_BPM_RE.finditer(value)
    ]


def boston_bpm_details(row: dict[str, str]) -> tuple[list[str], list[BpmInlineRef]]:
    head_values = []
    for column in (RAW_TEXT_COL, POSTMARK_COL):
        head_values.extend(head_bpm_details(row.get(column)))
    inline_values = []
    for column in (RAW_TEXT_COL, "txtRatesText", "txtRates"):
        inline_values.extend(inline_bpm_refs(row.get(column)))

    head = _dedupe(head_values)
    inline = []
    seen = set()
    for ref in inline_values:
        key = (ref.keyword, ref.detail.upper())
        if not ref.detail or key in seen:
            continue
        seen.add(key)
        inline.append(ref)
    return head, inline


def strip_inline_bpm_refs(text: str) -> str:
    return INLINE_BPM_RE.sub("", text)


def _first_semicolon_paren_start(text: str) -> int:
    for match in PAREN_RE.finditer(text):
        if ";" in match.group(1):
            return match.start()
    return len(text)


def _strip_bpm_parens_before_data(text: str) -> str:
    cutoff = _first_semicolon_paren_start(text)
    prefix = PAREN_RE.sub(
        lambda match: "" if is_bpm_locator_content(match.group(1)) else match.group(0),
        text[:cutoff],
    )
    return prefix + text[cutoff:]


def _starts_with_bpm_paren(text: str) -> bool:
    match = re.match(r"^\s*\(([^)]*)\)", text)
    return bool(match and is_bpm_locator_content(match.group(1)))


def _replace_head_before_data(text: str, replacement: str) -> str:
    if not replacement:
        return text
    cutoff = _first_semicolon_paren_start(text)
    if cutoff >= len(text):
        return text
    return replacement + text[cutoff:]


def _strip_bare_bpm_before_boston(text: str) -> str:
    return BARE_BPM_BEFORE_BOSTON_RE.sub("", text, count=1)


def _tidy_listing(text: str) -> str:
    text = re.sub(r"\s+([,;)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _is_boston_fragment(text: str) -> bool:
    if re.search(r"\d", text):
        return False
    letters = re.sub(r"[^A-Za-z]", "", text).upper()
    return 2 <= len(letters) <= 6 and "BOSTON".startswith(letters)


def sanitize_boston_inscription(text: object) -> str:
    value = clean(text)
    if not value:
        return ""
    value = strip_inline_bpm_refs(value)
    value = _strip_bare_bpm_before_boston(value)
    value = _strip_bpm_parens_before_data(value)
    value = clean(value).rstrip("(").strip()
    if _is_boston_fragment(value):
        return "BOSTON"
    if SAME_HEAD_RE.match(value):
        return "BOSTON"
    return value


def boston_catalog_head(row: dict[str, str]) -> str:
    townpost = sanitize_boston_inscription(row.get(TOWN_POSTMARK_COL))
    if townpost:
        return townpost
    postmark = sanitize_boston_inscription(row.get(POSTMARK_COL))
    if postmark:
        return postmark
    return "BOSTON"


def normalize_boston_listing_for_munger(
    state: str,
    row: dict[str, str],
    listing: str,
) -> str:
    if not is_boston_row(state, row) or not listing:
        return listing

    starts_with_bpm = _starts_with_bpm_paren(listing)
    text = strip_inline_bpm_refs(listing)
    text = _strip_bare_bpm_before_boston(text)
    text = _strip_bpm_parens_before_data(text)

    replacement = boston_catalog_head(row)
    if starts_with_bpm:
        text = _replace_head_before_data(text, replacement)
    if SAME_HEAD_RE.match(text):
        text = SAME_HEAD_RE.sub(replacement, text, count=1)
    return _tidy_listing(text)


def _is_single_detail(detail: str) -> bool:
    text = clean(detail)
    lower = text.lower()
    if "," in text or "&" in text or lower.startswith("also ") or " and others" in lower:
        return False
    if re.search(r"\d-\d", text):
        return False
    return True


def format_bpm_description(details: list[str]) -> str:
    details = _dedupe(details)
    if not details:
        return ""
    label = "illustration" if len(details) == 1 and _is_single_detail(details[0]) else "illustrations"
    return "BPM {0}: {1}".format(label, "; ".join(details))


def format_bpm_citation_detail(details: list[str]) -> str:
    details = _dedupe(details)
    if not details:
        return ""
    label = "illustration" if len(details) == 1 and _is_single_detail(details[0]) else "illustrations"
    return "{0} {1}".format(label, "; ".join(details))
