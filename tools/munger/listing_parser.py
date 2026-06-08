"""Reusable listing parser for catalog comparison tools.

This module is intentionally a thin adapter over the existing munger parser
steps. It accepts one CSV row plus its Listing text and returns plain Python
keys that can be compared without importing the full munger pipeline.
"""
from dataclasses import dataclass
import re

from .classify import (
    RELATIONSHIP_PATTERN,
    TRAILING_VALUE_PATTERN,
    _csv_manuscript_truthy,
    detect_cross_reference,
    detect_fragment,
    detect_structural_anatomy,
)
from .fields import (
    KNOWN_COLORS,
    _split_ms_date_token,
    classify_all_fields,
    classify_paren_field,
    subparse_fields,
)
from .fields.colors import parse_color_field
from .fields.dates import parse_date_field
from .head import MS_DATE_AT_END, parse_head, parse_manuscript_row
from .segment import classify_entry_form, decompose_tail, segment_entry, split_paren_fields
from .text_utils import strip_dot_leaders


NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
SPACE_RE = re.compile(r"\s+")
STATE_SUFFIX_RE = re.compile(r"(?:/|\s)*(?:va|na|virginia)\.?$", re.IGNORECASE)
LEADING_MARKER_RE = re.compile(r"^\s*(?:\*+\s*)?(?:\(\s*1\s*\)\s*)?")
DATEISH_TOKEN_RE = re.compile(r"\s+(?:c\.?\s*)?\*?\d{3,4}\b|\s+--\b")
TWO_DOT_RE = re.compile(r"\.{2,}")
COLOR_SPLIT_RE = re.compile(r"[\s-]+")
MONTH_FRAGMENT_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)",
    re.IGNORECASE,
)
YEAR_TOKEN_RE = re.compile(r"^\s*\*?(?:c\.?)?\d{3,4}(?:-\d{1,4})?(?:'?[Ss])?\s*$")
SIZE_SUFFIXES = {"YMDD", "MDD", "YMD", "YD", "MD", "NOR"}
RATE_KEYWORD_RE = re.compile(r"\b(?:PAID|FREE|STEAM|DUE|P\.?M\.?|frank)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedListing:
    """Plain parse result used by edition diff.

    Example summary shape:
    name=alexandria; dates=1772-1772; sizes=; rates=; colors=BLACK
    """

    raw_text: str
    clean_text: str
    entry_form: str
    name_text: str
    name_key: str
    material_key: tuple
    marker_key: tuple
    unknown_key: tuple
    value_key: tuple
    parse_errors: tuple
    summary: str


def parse_listing_text(listing, row=None, carried_town=""):
    """Parse one catalog Listing row into comparison keys.

    The caller supplies `carried_town` from the edition diff loader. Expected
    input row shape:
    {"Listing": "...", "Manuscript": "TRUE"|"FALSE"|""}
    """
    row = dict(row or {})
    raw_text = str(listing or "")
    clean_text = strip_dot_leaders(raw_text)
    work = dict(row)
    work["Listing"] = raw_text
    work["clean_text"] = clean_text
    work["s1_relationship"] = bool(RELATIONSHIP_PATTERN.match(clean_text))
    work["s2_cross_ref"] = detect_cross_reference(clean_text)
    work["s3_fragment"] = detect_fragment(clean_text)
    work["s4_trailing_value"] = bool(TRAILING_VALUE_PATTERN.search(clean_text))
    anatomy = detect_structural_anatomy(clean_text)
    work["s5_anatomy"] = anatomy["any"]
    work["is_manuscript_section"] = _is_manuscript_section(work, clean_text)

    entry_form = classify_entry_form(work)
    work["entry_form"] = entry_form

    if entry_form == "manuscript":
        work.update(_series_dict(parse_manuscript_row(work)))
    else:
        work.update(_series_dict(segment_entry(work)))

    fields = [_normalize_paren_field_text(f)
              for f in _recover_comma_fields(split_paren_fields(work))]
    if work.get("ms_date_text"):
        fields = [_normalize_paren_field_text(f)
                  for f in _split_ms_date_token(work.get("ms_date_text"))]
    work["paren_fields"] = fields
    work["paren_field_types"] = classify_all_fields(fields)

    work.update(_series_dict(parse_head(work)))
    work.update(_series_dict(decompose_tail(work)))
    work.update(_series_dict(subparse_fields(work)))
    if entry_form == "manuscript":
        work["is_manuscript"] = True

    parsed_dates = list(work.get("parsed_dates") or [])
    parsed_sizes = list(work.get("parsed_sizes") or [])
    parsed_rates = list(work.get("parsed_rates") or [])
    parsed_colors = list(work.get("parsed_colors") or [])
    other_fields = list(work.get("other_fields") or [])
    other_fields, extra_colors = _pull_color_like_other_fields(other_fields)
    parsed_colors.extend(extra_colors)

    marker_annotations, content_annotations = _split_marker_annotations(
        work.get("head_annotations") or []
    )
    name_text = _entry_name_text(work, clean_text, carried_town)
    name_key = _name_key(name_text)

    material_key = (
        ("name", name_key),
        ("dates", _date_key(parsed_dates)),
        ("manuscript", bool(work.get("is_manuscript"))),
        ("sizes", _size_key(parsed_sizes)),
        ("rates", _rate_key(parsed_rates)),
        ("colors", _color_key(parsed_colors)),
        ("annotations", _note_tuple_key(content_annotations)),
        ("tail_note", _note_key(work.get("tail_annotation"))),
        ("free_text", _free_text_key(work)),
    )
    marker_key = (
        ("first", bool(work.get("head_first_of_town"))),
        ("rel", _marker_text_key(work.get("head_rel_type"))),
        ("markers", tuple(sorted(_text_key(x) for x in marker_annotations))),
    )
    unknown_key = _note_tuple_key(other_fields)
    value_key = tuple(_text_key(x) for x in (work.get("tail_valuation"),))
    errors = _parse_errors(work, parsed_dates, parsed_sizes)
    summary = _summary(name_key, parsed_dates, parsed_sizes, parsed_rates,
                       parsed_colors, content_annotations, other_fields)

    return ParsedListing(
        raw_text=raw_text,
        clean_text=clean_text,
        entry_form=entry_form,
        name_text=name_text,
        name_key=name_key,
        material_key=material_key,
        marker_key=marker_key,
        unknown_key=unknown_key,
        value_key=value_key,
        parse_errors=tuple(errors),
        summary=summary,
    )


def _series_dict(series):
    return series.to_dict()


def _recover_comma_fields(fields):
    recovered = []
    for field in fields:
        recovered.extend(_recover_one_comma_field(field))
    return [field for field in recovered if str(field).strip()]


def _recover_one_comma_field(field):
    text = str(field or "").strip()
    if "," not in text:
        return [text]

    tokens = [tok.strip() for tok in text.split(",") if tok.strip()]
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_date_fragment(tok) and i + 1 < len(tokens) and YEAR_TOKEN_RE.match(tokens[i + 1]):
            out.append(tok + "," + tokens[i + 1])
            i += 2
            continue
        if _is_ms_token(tok):
            out.append("Ms")
            i += 1
            continue
        if _is_size_start(tok):
            parts = [tok]
            i += 1
            while i < len(tokens) and _is_size_suffix(tokens[i]):
                parts.append(tokens[i])
                i += 1
            out.append(",".join(parts))
            continue
        if _is_color_field_token(tok):
            parts = [tok]
            i += 1
            while i < len(tokens) and _is_color_field_token(tokens[i]):
                parts.append(tokens[i])
                i += 1
            out.append(",".join(parts))
            continue
        if _is_rate_field_token(tok):
            parts = [tok]
            i += 1
            while i < len(tokens) and _is_rate_field_token(tokens[i]):
                parts.append(tokens[i])
                i += 1
            out.append(",".join(parts))
            continue
        out.append(tok)
        i += 1
    return out


def _normalize_paren_field_text(text):
    t = str(text or "").strip()
    t = re.sub(r"\bc\.\s*(?=\d{3,4})", "c", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(1[5-8]\d0)s\b", r"\1's", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.\s*(?=\d{4}\b)",
        r"\1. ",
        t,
        flags=re.IGNORECASE,
    )
    return t


def _is_date_fragment(text):
    return bool(MONTH_FRAGMENT_RE.search(text) or re.search(r"\bc\.?\d{3,4}\b", text))


def _is_ms_token(text):
    return str(text).strip().lower() == "ms"


def _is_size_start(text):
    return classify_paren_field(str(text)) == "size"


def _is_size_suffix(text):
    parts = str(text).strip().upper().split()
    return bool(parts) and parts[0] in SIZE_SUFFIXES


def _is_color_field_token(text):
    parts = _color_tokens(str(text))
    return bool(parts) and all(part.lower() in KNOWN_COLORS for part in parts)


def _is_rate_field_token(text):
    t = str(text).strip()
    if RATE_KEYWORD_RE.search(t):
        return True
    if re.search(r"\[[^\]]+\]", t):
        return True
    return bool(re.match(r"^(?:\d+(?:[-/]\d+(?:/\d+)?)?|[IVXLCDM]+)$", t, re.IGNORECASE))


def _is_manuscript_section(row, clean_text):
    if _csv_manuscript_truthy(row):
        return True
    if "(" in clean_text or ")" in clean_text:
        return False
    if not TRAILING_VALUE_PATTERN.search(clean_text):
        return False
    body = TRAILING_VALUE_PATTERN.sub("", clean_text).rstrip()
    return bool(MS_DATE_AT_END.search(body))


def _entry_name_text(work, clean_text, carried_town):
    rel = _marker_text_key(work.get("head_rel_type"))
    if carried_town and rel in ("same", "l", "e"):
        return carried_town
    name = work.get("head_name_body") or ""
    if not name:
        name = _fallback_name_text(clean_text, carried_town)
    name = STATE_SUFFIX_RE.sub("", str(name)).strip(" ./")
    return name or carried_town or ""


def _fallback_name_text(text, carried_town):
    cleaned = LEADING_MARKER_RE.sub("", text).strip()
    cleaned = TWO_DOT_RE.sub(" ... ", cleaned)
    name = re.split(r"\s+\.\.\.\s+|\s+-{2,}\s+", cleaned, maxsplit=1)[0].strip()
    m = DATEISH_TOKEN_RE.search(name)
    if m:
        name = name[:m.start()].strip()
    return name or carried_town or cleaned


def _name_key(text):
    text = STATE_SUFFIX_RE.sub("", str(text or "").lower()).strip()
    text = LEADING_MARKER_RE.sub("", text)
    return NON_ALNUM_RE.sub("", text)


def _text_key(text):
    if _is_blank(text):
        return ""
    t = str(text).lower()
    t = strip_dot_leaders(t)
    t = re.sub(r"\bc\.\s*(?=\d{3,4})", "c", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", "", t)
    t = t.replace("'", "")
    t = t.replace('"', "")
    t = t.replace("`", "")
    t = re.sub(r"\b([a-z]{1,3})\s*in\s+ms\b", r"\1ms", t)
    t = re.sub(r"\bin\s+ms\b", "ms", t)
    t = re.sub(r"\s*([(),;])\s*", r"\1", t)
    t = SPACE_RE.sub(" ", t).strip()
    return t


def _marker_text_key(text):
    return NON_ALNUM_RE.sub("", _text_key(text))


def _text_tuple_key(items):
    return tuple(sorted(_text_key(x) for x in items if _text_key(x)))


def _note_key(text):
    t = _text_key(text)
    t = re.sub(r"\boval\b", "o", t)
    return NON_ALNUM_RE.sub("", t)


def _note_tuple_key(items):
    return tuple(sorted(_note_key(x) for x in items if _note_key(x)))


def _split_marker_annotations(items):
    markers = []
    content = []
    for item in items:
        key = _text_key(item)
        if key in ("e", "l", "1", "*"):
            markers.append(item)
        else:
            content.append(item)
    return markers, content


def _date_key(parsed_dates):
    values = []
    for item in parsed_dates:
        values.append((
            item.get("date_year_start"),
            item.get("date_year_end"),
            item.get("date_month"),
            item.get("date_day"),
            item.get("date_granularity"),
            bool(item.get("date_is_circa")),
        ))
    return _sorted_tuple(values)


def _size_key(parsed_sizes):
    values = []
    for item in parsed_sizes:
        values.append((
            _text_key(item.get("size_shape_code")),
            item.get("size_dim1"),
            item.get("size_dim2"),
            _text_key(item.get("size_dateformat")),
            bool(item.get("size_is_irregular")),
            _text_key(item.get("size_qualifier")),
        ))
    return _sorted_tuple(values)


def _rate_key(parsed_rates):
    values = []
    for group in parsed_rates:
        tokens = group if isinstance(group, list) else [group]
        for item in tokens:
            values.append((
                _text_key(item.get("rate_keyword")),
                _text_key(item.get("rate_amount_raw")),
                _note_key(item.get("rate_bracket")),
                bool(item.get("rate_is_manuscript")),
                _note_key(item.get("rate_impression")),
            ))
    return _sorted_tuple(values)


def _sorted_tuple(values):
    return tuple(sorted(values, key=lambda item: repr(item)))


def _color_key(parsed_colors):
    colors = []
    for item in parsed_colors:
        colors.extend(_color_tokens(str(item)))
    return tuple(sorted(set(colors)))


def _color_tokens(text):
    out = []
    for comma_part in text.split(","):
        parts = [p for p in COLOR_SPLIT_RE.split(comma_part.strip()) if p]
        if parts and all(p.lower() in KNOWN_COLORS for p in parts):
            out.extend(p.upper() for p in parts)
        elif comma_part.strip():
            out.append(comma_part.strip().upper())
    return out


def _pull_color_like_other_fields(other_fields):
    kept = []
    colors = []
    for field in other_fields:
        parsed = _color_like_field(field)
        if parsed:
            colors.extend(parsed)
        else:
            kept.append(field)
    return kept, colors


def _color_like_field(field):
    colors = _color_tokens(str(field))
    if not colors:
        return []
    if all(c.lower() in KNOWN_COLORS for c in colors):
        return colors
    return parse_color_field(field) if "," in str(field) else []


def _free_text_key(work):
    if work.get("entry_form") in ("semicolon_paren", "simple_paren", "manuscript"):
        return ""
    return _note_key(work.get("head_name_body"))


def _parse_errors(work, parsed_dates, parsed_sizes):
    errors = []
    for key in ("seg_error", "tail_error"):
        val = work.get(key)
        if not _is_blank(val):
            errors.append("%s:%s" % (key, val))
    for item in parsed_dates:
        if item.get("date_error"):
            errors.append("date:%s" % item.get("date_error"))
    for item in parsed_sizes:
        if item.get("size_error"):
            errors.append("size:%s" % item.get("size_error"))
    return errors


def _is_blank(value):
    if value is None:
        return True
    return isinstance(value, float) and value != value


def _summary(name_key, parsed_dates, parsed_sizes, parsed_rates,
             parsed_colors, content_annotations, other_fields):
    dates = ",".join("%s-%s" % (d.get("date_year_start"), d.get("date_year_end"))
                     for d in parsed_dates)
    sizes = ",".join("%s:%s:%s" % (
        _text_key(s.get("size_shape_code")), s.get("size_dim1"), s.get("size_dim2")
    ) for s in parsed_sizes)
    rates = ",".join(sorted(str(x) for x in _rate_key(parsed_rates)))
    colors = ",".join(_color_key(parsed_colors))
    notes = ",".join(_text_tuple_key(content_annotations))
    unknown = ",".join(_text_tuple_key(other_fields))
    return (
        "name=%s; dates=%s; sizes=%s; rates=%s; colors=%s; notes=%s; unknown=%s"
        % (name_key, dates, sizes, rates, colors, notes, unknown)
    )
