"""Build munger-safe catalog text from v1 split columns.

Run related tests from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_v1_pipeline.py'

Expected exit code: 0.
"""

from __future__ import annotations

import re

from munger.fields.colors import parse_color_field
from munger.fields.rates import parse_rate_token, split_rate_tokens
from munger.fields.sizes import parse_size_field
from v1_to_v2_catalog_format import normalize_listing


TEXT_SENTINELS = {"", "-", "--", "N/A", "NA", "NONE", "NULL"}
DATE_SENTINEL_YEARS = {"1700", "1900"}
MONTH_NAME_BY_NUMBER = {
    "1": "Jan.",
    "2": "Feb.",
    "3": "Mar.",
    "4": "Apr.",
    "5": "May",
    "6": "June",
    "7": "July",
    "8": "Aug.",
    "9": "Sept.",
    "10": "Oct.",
    "11": "Nov.",
    "12": "Dec.",
}
SHAPE_CODE_BY_LABEL = {
    "ARC OR SEMI-CIRCLE": "ARC",
    "BOX": "BOX",
    "CDS": "C",
    "CIRCLE": "C",
    "DOUBLE CIRCLE": "DC",
    "DOUBLE LINE CIRCLE": "DLC",
    "DOUBLE LINE DOUBLE CIRCLE": "DLDC",
    "DOUBLE LINE DOUBLE OVAL": "DLDO",
    "DOUBLE LINE OVAL": "DLO",
    "DOUBLE OVAL": "DO",
    "NO OUTER RIM": "NOR",
    "OCTAGON": "OCTAGON",
    "OVAL": "O",
    "SL - STRAIGHT LINE": "SL",
    "STRAIGHT LINE": "SL",
}
RATE_KEYWORD_RE = re.compile(
    r"\b(?:PAID|FREE|STEAM|DUE|FRANK)\b|\bP\.?M\.?",
    re.IGNORECASE,
)
RATE_BRACKET_SIGNAL_RE = re.compile(
    r"[\[\{\|]\s*(?:MS\.?|C|O|BOX|ARC|OCTAGON|SL|RECTANGLE|OVAL|CIRCLE)\s*[\]\}\|]",
    re.IGNORECASE,
)
BARE_RATE_RE = re.compile(
    r"^(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLCDM]+)(?:\s*,\s*(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLCDM]+))*$",
    re.IGNORECASE,
)
FRACTION_CENTS_RE = re.compile(r"\b(\d+)\s+(\d+)/(\d+)\s*cents?\b", re.IGNORECASE)
MS_BRACKET_RE = re.compile(r"[\[\{\|]\s*ms\.?\s*[\]\}\|]", re.IGNORECASE)


def clean(value: object) -> str:
    """Return a whitespace-normalized text cell."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def useful_text(value: object) -> str:
    """Return a normalized v1 value, or "" for sentinel placeholders."""
    text = clean(value)
    if text.upper() in TEXT_SENTINELS:
        return ""
    return text


def truthy(value: object) -> bool:
    """Return True for v1 boolean cells that mean yes."""
    return clean(value).lower() in {"1", "true", "yes", "y", "t"}


def synthetic_head(row: dict[str, str]) -> str:
    """Return the listing head for a blank-text v1 row."""
    for column in ("txtTownPostmark", "txtPostmark", "txtTown"):
        text = useful_text(row.get(column))
        if text:
            return text
    return ""


def color_tokens(row: dict[str, str]) -> list[str]:
    """Return color names from the v1 color source columns."""
    for column in ("txtColors", "txtTownmarkColor"):
        text = useful_text(row.get(column))
        if not text:
            continue
        names = [
            name
            for name in parse_color_field(text)
            if useful_text(name) and useful_text(name).upper() != "N/A"
        ]
        if names:
            return names
    return []


def normalized_shape_code(value: object) -> str:
    """Return an ASCC shape code from a v1 shape label."""
    text = useful_text(value).upper().replace("SEMI CIRCLE", "SEMI-CIRCLE")
    if not text:
        return ""
    if text in SHAPE_CODE_BY_LABEL:
        return SHAPE_CODE_BY_LABEL[text]
    first = re.split(r"[^A-Z0-9]+", text)[0]
    return SHAPE_CODE_BY_LABEL.get(first, first)


def decimal_text(value: object) -> str:
    """Return a compact decimal string, or "" when the cell is not numeric."""
    text = clean(value).replace(",", "")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    return "{0:.2f}".format(number).rstrip("0").rstrip(".")


def _shape_code_from_size_text(text: str) -> str:
    upper = text.upper()
    labels = sorted(SHAPE_CODE_BY_LABEL, key=len, reverse=True)
    for label in labels:
        if label in upper:
            return SHAPE_CODE_BY_LABEL[label]
    if re.search(r"\bCIRCLE\b", upper):
        return "C"
    return ""


def _dimension_from_size_text(text: str) -> tuple[str, str]:
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return "", ""
    width = decimal_text(numbers[0])
    height = decimal_text(numbers[1]) if len(numbers) > 1 else ""
    return width, height


def size_token(row: dict[str, str]) -> str:
    """Return the best munger-friendly size token for a blank-text row."""
    raw_size = useful_text(row.get("txtSizes"))
    if raw_size:
        parsed = parse_size_field(raw_size)
        if not parsed.get("size_error"):
            return raw_size
        shape_code = _shape_code_from_size_text(raw_size)
        width, height = _dimension_from_size_text(raw_size)
        if shape_code and width and height:
            return "{0}-{1}x{2}".format(shape_code, width, height)
        if shape_code and width:
            return "{0}-{1}".format(shape_code, width)
        if width:
            return width

    shape_code = normalized_shape_code(row.get("txtTownmarkShape"))
    width = decimal_text(row.get("nWidth") or row.get("txtWidth"))
    height = decimal_text(row.get("nHeight") or row.get("txtHeight"))
    if shape_code and width and height and height != width:
        return "{0}-{1}x{2}".format(shape_code, width, height)
    if shape_code and width:
        return "{0}-{1}".format(shape_code, width)
    if width:
        return width
    return shape_code


def _date_from_parts(row: dict[str, str], prefix: str) -> str:
    year_text = useful_text(row.get("txt{0}UseYear".format(prefix)))
    year_number = useful_text(row.get("n{0}UseYear".format(prefix)))
    year = year_text or year_number
    if not year or year in DATE_SENTINEL_YEARS:
        return ""
    year = re.sub(r"^circa\s+", "c", year, flags=re.IGNORECASE)
    month = useful_text(row.get("txt{0}UseMonth".format(prefix)))
    month = MONTH_NAME_BY_NUMBER.get(month, month)
    day = useful_text(row.get("n{0}UseDay".format(prefix)))
    if month and day:
        return "{0} {1}, {2}".format(month, day, year)
    if month:
        return "{0} {1}".format(month, year)
    return year


def date_tokens(row: dict[str, str]) -> list[str]:
    """Return meaningful date tokens for a blank-text v1 row."""
    dates_seen = useful_text(row.get("txtDatesSeen"))
    if dates_seen:
        years = set(re.findall(r"\b\d{4}\b", dates_seen))
        if years and years.issubset(DATE_SENTINEL_YEARS):
            dates_seen = ""
    if dates_seen:
        return [dates_seen]
    tokens = []
    for prefix in ("Earliest", "Latest"):
        token = _date_from_parts(row, prefix)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _normalize_rate_text(value: object) -> str:
    text = useful_text(value)
    if not text:
        return ""
    text = MS_BRACKET_RE.sub("[ms]", text)
    text = FRACTION_CENTS_RE.sub(r"\1-\2/\3", text)
    return clean(text)


def _is_rate_token(token: str) -> bool:
    if not token:
        return False
    if RATE_KEYWORD_RE.search(token) or RATE_BRACKET_SIGNAL_RE.search(token):
        return True
    if BARE_RATE_RE.match(token):
        parsed = parse_rate_token(token)
        return bool(parsed.get("rate_amount_raw"))
    return False


def rate_tokens(row: dict[str, str]) -> list[str]:
    """Return parseable rate or auxmark tokens from v1 rate columns."""
    raw_values = []
    rate_text = _normalize_rate_text(row.get("txtTownmarkRateText"))
    rate_value = _normalize_rate_text(row.get("txtTownmarkRateValue"))
    if rate_text and rate_value:
        raw_values.append("{0} {1}".format(rate_text, rate_value))
    raw_values.extend(
        _normalize_rate_text(row.get(column))
        for column in ("txtRates", "txtRatesText", "txtTownmarkRateText", "txtTownmarkRateValue")
    )

    tokens = []
    seen = set()
    for value in raw_values:
        if not value:
            continue
        for token in split_rate_tokens(value):
            token = _normalize_rate_text(token)
            key = token.upper()
            if key in seen or not _is_rate_token(token):
                continue
            tokens.append(token)
            seen.add(key)
    return tokens


def rate_note_tokens(row: dict[str, str]) -> list[str]:
    """Return rate-column values that should be preserved as notes."""
    notes = []
    seen = set()
    for column in ("txtRates", "txtRatesText", "txtTownmarkRateText", "txtTownmarkRateValue"):
        value = _normalize_rate_text(row.get(column))
        if not value:
            continue
        parseable = any(_is_rate_token(_normalize_rate_text(token)) for token in split_rate_tokens(value))
        key = value.upper()
        if parseable or key in seen:
            continue
        notes.append(value)
        seen.add(key)
    return notes


def synthetic_desc_lines(row: dict[str, str]) -> list[str]:
    """Return extra description lines implied by v1 split columns."""
    return ["Rate note: {0}".format(token) for token in rate_note_tokens(row)]


def has_synthetic_listing_evidence(row: dict[str, str]) -> bool:
    """Return True when a blank-text row can produce a catalog listing."""
    if normalize_listing(row.get("txtRawStateData")):
        return True
    return bool(synthetic_head(row))


def synthetic_listing(row: dict[str, str]) -> str:
    """Return synthesized catalog text for a blank txtRawStateData row.

    Example output shape:
      FALLS CHURCH(March 15, 1854;Ms;Paid 3 [ms];Black) --
    """
    if normalize_listing(row.get("txtRawStateData")):
        return ""
    head = synthetic_head(row)
    if not head:
        return ""
    fields = []
    fields.extend(date_tokens(row))
    if truthy(row.get("ynManuscript")) or truthy(row.get("ynManuscriptTownmarks")):
        fields.append("Ms")
    size = size_token(row)
    if size:
        fields.append(size)
    fields.extend(rate_tokens(row))
    colors = color_tokens(row)
    if colors:
        fields.append(",".join(colors))

    value = useful_text(row.get("txtValue")) or "--"
    if fields:
        return "{0}({1}) {2}".format(head, ";".join(fields), value)
    return "{0} {1}".format(head, value)
