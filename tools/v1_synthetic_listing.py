"""Build munger-safe catalog text from v1 split columns.

Run related tests from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_v1_pipeline.py'

Expected exit code: 0.
"""

from __future__ import annotations

import re

from munger.fields import KNOWN_COLORS, is_color_field
from munger.fields.colors import parse_color_field
from munger.fields.rates import parse_rate_token, split_rate_tokens
from munger.fields.sizes import parse_size_field
from v1_to_v2_catalog_format import normalize_listing

# v1 columns that may hold rate text. The v1 data entry split catalog lines
# positionally, so a listing with no rate field can strand its second color
# here (IA ATHENS: "(1831-32;31;Blue;Red)" -> txtRatesText='Blue' while
# txtColors='Red'). Every consumer of these columns must treat a value that
# parses entirely as colors as a color source, never as a rate or rate note.
RATE_TEXT_COLUMNS = ("txtRates", "txtRatesText", "txtTownmarkRateText", "txtTownmarkRateValue")


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
    r"\b(?:PAID|FREE|STEAM|DUE|FRANK|NEGATIVE|STENCIL)\b|\bP\.?M\.?",
    re.IGNORECASE,
)
RATE_BRACKET_SIGNAL_RE = re.compile(
    r"[\[\{\|]\s*"
    r"(?:MS\.?|NEG(?:ATIVE)?|STENCIL|C|O|BOX|ARC|OCTAGON|SL|RECTANGLE|OVAL|CIRCLE)"
    r"\s*[\]\}\|]",
    re.IGNORECASE,
)
BARE_RATE_RE = re.compile(
    r"^(?:(?:NEGATIVE|STENCIL)\s+)?"
    r"(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLDM]+)"
    r"(?:\s*,\s*(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLDM]+))*$",
    re.IGNORECASE,
)
FRACTION_CENTS_RE = re.compile(r"\b(\d+)\s+(\d+)/(\d+)\s*cents?\b", re.IGNORECASE)
MS_BRACKET_RE = re.compile(r"[\[\{\|]\s*ms\.?\s*[\]\}\|]", re.IGNORECASE)
COLOR_CONNECTOR_WORDS = {"and", "to"}
COLOR_MODIFIER_WORDS = {"bright", "dark", "deep", "light", "pale"}
V1_EXTRA_COLOR_WORDS = {"brownish", "purplish"}
DATE_FORMAT_COMPACTS = {
    "MD",
    "MDD",
    "YD",
    "YMD",
    "YMDD",
    "MONTHDAY",
    "MONTHDAYDAY",
    "YEARDAY",
    "YEARMONTHDAY",
    "YEARMONTHDAYDAY",
}
DATE_FORMAT_COMPACTS.update(
    "{0}{1}".format(code, suffix)
    for code in ("MD", "MDD", "YD", "YMD", "YMDD")
    for suffix in ("ABOVE", "BELOW")
)


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


def _is_v1_color_word(word: str) -> bool:
    return (
        word in KNOWN_COLORS
        or word in V1_EXTRA_COLOR_WORDS
        or word in COLOR_MODIFIER_WORDS
        or word in COLOR_CONNECTOR_WORDS
    )


def _is_v1_color_token(token: str) -> bool:
    words = [w.lower() for w in re.split(r"[\s/\-]+", token) if w]
    if not words:
        return False
    has_color_word = False
    for word in words:
        if word in COLOR_CONNECTOR_WORDS or word in COLOR_MODIFIER_WORDS:
            continue
        if word in KNOWN_COLORS or word in V1_EXTRA_COLOR_WORDS:
            has_color_word = True
            continue
        return False
    return has_color_word and all(_is_v1_color_word(word) for word in words)


def _is_v1_color_field(text: str) -> bool:
    if not text:
        return False
    if is_color_field(text):
        return True
    if re.search(r"\d", text) or RATE_KEYWORD_RE.search(text):
        return False
    tokens = [token.strip() for token in text.split(",") if token.strip()]
    return bool(tokens) and all(_is_v1_color_token(token) for token in tokens)


def _is_date_format_text(value: object) -> bool:
    compact = re.sub(r"[^A-Z0-9]+", "", clean(value).upper())
    return compact in DATE_FORMAT_COMPACTS


def synthetic_head(row: dict[str, str]) -> str:
    """Return the listing head for a blank-text v1 row."""
    for column in ("txtTownPostmark", "txtPostmark", "txtTown"):
        text = useful_text(row.get(column))
        if text:
            return text
    return ""


def color_tokens(row: dict[str, str]) -> list[str]:
    """Return color names from the v1 color source columns.

    Colors stranded in rate columns by the v1 positional split (see
    RATE_TEXT_COLUMNS) are appended after the color-column names, deduped
    case-insensitively.
    """
    names: list[str] = []
    for column in ("txtColors", "txtTownmarkColor"):
        text = useful_text(row.get(column))
        if not text or not _is_v1_color_field(text):
            continue
        names = [
            name
            for name in parse_color_field(text)
            if useful_text(name) and useful_text(name).upper() != "N/A"
        ]
        if names:
            break
    seen = {name.upper() for name in names}
    for column in RATE_TEXT_COLUMNS:
        value = _normalize_rate_text(row.get(column))
        if not value or not _is_v1_color_field(value):
            continue
        for name in parse_color_field(value):
            if useful_text(name) and name.upper() not in seen:
                names.append(name)
                seen.add(name.upper())
    return names


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
        _normalize_rate_text(row.get(column)) for column in RATE_TEXT_COLUMNS
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


def _rate_note_is_size_echo(value: str, row: dict[str, str]) -> bool:
    raw_size = useful_text(row.get("txtSizes"))
    if not raw_size:
        return False
    note_text = clean(value).strip("()[]{} ")
    if clean(raw_size).upper() != note_text.upper():
        return False
    parsed = parse_size_field(raw_size)
    return (
        not parsed.get("size_error")
        and parsed.get("size_dim1") is not None
        and not parsed.get("size_shape_code")
    )


def rate_note_tokens(row: dict[str, str]) -> list[str]:
    """Return rate-column values that should be preserved as notes.

    A value that parses entirely as colors is a stranded color, not a rate
    note; color_tokens picks it up instead (no "Rate note: Blue" desc).
    """
    notes = []
    seen = set()
    for column in RATE_TEXT_COLUMNS:
        value = _normalize_rate_text(row.get(column))
        if not value or _is_v1_color_field(value) or _is_date_format_text(value):
            continue
        if _rate_note_is_size_echo(value, row):
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
