import re


_POST_OFFICE_APOSTROPHE_RE = re.compile(r"[\u2019']")
_POST_OFFICE_QUOTE_RE = re.compile(r"[\u201c\u201d\u201e\u201f\"]")
_POST_OFFICE_AMP_RE = re.compile(r"\s*&\s*")
_POST_OFFICE_STRIP_PUNCT_RE = re.compile(r"[,/=()\[\]:;_`*?]")
_POST_OFFICE_DOUBLE_DASH_RE = re.compile(r"-{2,}")
_POST_OFFICE_MULTI_SPACE_RE = re.compile(r"\s+")
_POST_OFFICE_EDGE_TRIM_RE = re.compile(r"^[\s.\-]+|[\s.,\-]+$")
_POST_OFFICE_COMPACT_RE = re.compile(r"[^A-Z]+")
_POST_OFFICE_ATTACHED_YEAR_TAIL_RE = re.compile(
    r"(?<=[A-Z])C?\d{3,4}(?:'?[S])?(?:[,-]\d{1,4}(?:'?[S])?)*(?:\s+.*)?$"
)
_POST_OFFICE_DIGIT_TOKEN_TAIL_RE = re.compile(r"\s+\S*\d\S*.*$")
_UNKNOWN_POST_OFFICE_TOWN_KEYS = {
    "ADV",
    "ADVERTISED",
    "NOTOWN",
    "NOTOWNMARK",
    "NOTOWNMARKING",
    "REGISTERED",
}


def strip_dot_leaders(text):
    """Remove dot leaders and collapse resulting whitespace.

    Two leader forms occur: runs of 2+ dots (the common case), and a
    single dot flanked by whitespace on both sides (a short leader the
    extract emits when only a couple of dots were scanned, e.g.
    ``Cantwells Bridge 1807,1810,1823,1846 . 150/75.00``). A space-
    isolated single dot is always a leader -- abbreviation periods attach
    to a letter/digit (``C.D.``, ``St.``, ``N.W.``), never sit space-
    isolated -- so collapsing it is safe and leaves real abbreviations
    untouched. Without this, the trailing leader residue survives value-
    stripping and blocks the manuscript date peel, gluing the dates into
    the post-office name."""
    t = re.sub(r'\.{2,}', ' ', str(text))
    t = re.sub(r'(?<=\s)\.(?=\s)', ' ', t)
    return re.sub(r'  +', ' ', t).strip()


def strip_trailing_state_suffix(text):
    """Remove a trailing one-to-four-letter state abbreviation."""
    value = str(text or "").strip()
    for pattern in (
        r"\s+[A-Za-z]{1,4}\.?$",
        r"/\s*[A-Za-z]{1,4}\.?$",
        r"\.\s*[A-Za-z]{1,4}\.?$",
    ):
        stem = re.sub(pattern, "", value).strip()
        if stem != value:
            return stem
    return value


def normalize_post_office_town_text(raw_town):
    """Normalize ASCC town text into a post-office name, or None."""
    if raw_town is None:
        return None
    town = str(raw_town).upper()
    town = _POST_OFFICE_APOSTROPHE_RE.sub("", town)
    town = _POST_OFFICE_QUOTE_RE.sub("", town)
    town = _POST_OFFICE_AMP_RE.sub(" AND ", town)
    town = _POST_OFFICE_STRIP_PUNCT_RE.sub(" ", town)
    town = _POST_OFFICE_DOUBLE_DASH_RE.sub("-", town)
    town = _POST_OFFICE_MULTI_SPACE_RE.sub(" ", town)
    # v1 descriptive entries glue dates and prose to the town name.
    # Strip whitespace-delimited date tokens before attached date suffixes.
    town = _POST_OFFICE_DIGIT_TOKEN_TAIL_RE.sub("", town)
    town = _POST_OFFICE_ATTACHED_YEAR_TAIL_RE.sub("", town)
    town = _POST_OFFICE_EDGE_TRIM_RE.sub("", town)
    if _POST_OFFICE_COMPACT_RE.sub("", town) in _UNKNOWN_POST_OFFICE_TOWN_KEYS:
        return None
    return town or None
