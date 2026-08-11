import re

import pandas as pd

from .dates import parse_date_field, MONTHS_PAT, DATE_FIELD_RE
from .sizes import (
    SIZE_IMPRESSION_PREFIX_RE,
    is_comma_diameter_list,
    parse_size_field,
    SHAPE_CODE_SET,
    SHAPE_CODE_PAT,
    SIZE_SUFFIX_PAT,
    SIZE_FIELD_RE,
)
from .rates import RATE_KEYWORD_RE, parse_rate_field
from .colors import parse_color_field
from ..classify import _csv_manuscript_truthy


RATE_FIELD_RE = re.compile(
    r'(?:'
    r'\bPAID\b|\bFREE\b|\bSTEAM\b|\bDUE\b'
    r'|\bP\.?M\.?'
    r'|\bfrank\b'
    r'|\bnegative\b|\bstencil\b'
    # Bracketed rate hints: [ms], [C], and OCR close variants like [C[.
    r'|[\[\{\|][^\[\{\|\]\}]*[\[\{\|\]\}]'
    r'|\bwith\s+\d'        # "with 24" = with adhesive
    r')',
    re.IGNORECASE
)

KNOWN_COLORS = {
    'black', 'red', 'blue', 'green', 'brown', 'orange', 'purple',
    'magenta', 'yellow', 'olive', 'violet', 'carmine', 'vermilion',
    'pink', 'gray', 'grey', 'buff', 'salmon', 'rose', 'maroon',
    'crimson', 'indigo', 'lilac', 'scarlet', 'amber', 'brownish',
    'purplish',
}
COLOR_CONNECTOR_WORDS = {'and', 'to'}
COLOR_MODIFIER_WORDS = {'bright', 'dark', 'deep', 'light', 'pale'}

def is_color_token(tok):
    """Check a single token, including compound color phrases."""
    words = [w.lower() for w in re.split(r'[\s\-]+', tok.strip()) if w]
    if not words:
        return False
    has_color_word = False
    for word in words:
        if word in COLOR_CONNECTOR_WORDS or word in COLOR_MODIFIER_WORDS:
            continue
        if word in KNOWN_COLORS:
            has_color_word = True
            continue
        return False
    return has_color_word

def is_color_field(field):
    """True if all comma-separated tokens in the field are known colors."""
    tokens = [t.strip() for t in field.split(',') if t.strip()]
    return bool(tokens) and all(is_color_token(t) for t in tokens)

BARE_NUMBER_RE = re.compile(r'^\d{1,3}(?:\.\d+)?$')

# Leading shape-code + dimension signature ("SL-42x5...", "DC 25...").
# A field that OPENS like this is a size field even when a later annotation
# bracket would otherwise trip RATE_FIELD_RE's catch-all bracket alternative.
SIZE_LEADING_SHAPE_RE = re.compile(
    r'^(?:' + SHAPE_CODE_PAT + r')[\s\-]?\d', re.IGNORECASE
)

# Smallest plausible circular-datestamp diameter (mm). A bare number below this
# in the size slot of an unknown-date listing is a rate (e.g. a 2c drop rate),
# not a sub-centimetre circle. Town CDS diameters in ASCC run ~15-60mm.
MIN_BARE_DIAMETER_MM = 13.0


def _leading_dash_is_unknown_date(paren_fields, types):
    """True when a leading dash is the date slot, not a size placeholder."""
    if not paren_fields or paren_fields[0].strip() not in ('-', '--'):
        return False

    # Decision: only full date/size/rate/color-style rows promote a leading
    # dash to unknown date. Short rows such as ["--", "28"] keep staging's
    # size-placeholder behavior so the following diameter is not a rate.
    tail_types = types[2:]
    return len(paren_fields) >= 4 and 'rate' in tail_types and 'color' in tail_types

def classify_paren_field(field_text):
    """Classify a single paren field by intrinsic content signals.
    Returns one of: date, ms, size, rate, color, other, empty."""
    f = field_text.strip()
    if not f:
        return 'empty'

    # 1. Manuscript (exact)
    if f == 'Ms':
        return 'ms'

    # 2. Date expression
    if DATE_FIELD_RE.search(f):
        return 'date'

    # 2b. Impression-prefixed size, not rate: "stencil C-31" describes a
    # stencil townmark with a 31mm circle, while "stencil 5" remains a rate.
    impression_m = SIZE_IMPRESSION_PREFIX_RE.match(f)
    if impression_m:
        remainder = f[impression_m.end():].strip()
        if (not RATE_KEYWORD_RE.search(remainder)
                and (remainder.upper() in SHAPE_CODE_SET
                     or SIZE_FIELD_RE.search(remainder))):
            return 'size'

    # 3. Rate/auxmark (checked before size -- brackets disambiguate).
    # Exception: a field that opens with a shape-code+dimension signature is
    # a size no matter what a trailing annotation bracket contains.
    # "SL-42x5,MDD[separate hdstp]" was misread as a 42c ratemark because
    # the bracket alternative in RATE_FIELD_RE matched "[separate hdstp]"
    # (ANNAPOLIS, woco record ASCC6-MD-M1005). An explicit rate keyword
    # (PAID/FREE/STEAM/DUE) still outranks the size signature.
    if RATE_FIELD_RE.search(f):
        if not (SIZE_LEADING_SHAPE_RE.match(f)
                and not RATE_KEYWORD_RE.search(f)):
            return 'rate'

    # 4. Size/shape/dateformat composite
    if f.upper() in SHAPE_CODE_SET:
        return 'size'
    if is_comma_diameter_list(f):
        return 'size'
    if SIZE_FIELD_RE.search(f):
        return 'size'

    # 5. Color
    if is_color_field(f):
        return 'color'

    # 6. Bare small number -> size by ASCC convention
    if BARE_NUMBER_RE.match(f):
        return 'size'

    return 'other'

def classify_all_fields(paren_fields):
    """Classify each field in the list. Returns parallel list of type labels."""
    types = [classify_paren_field(f) for f in paren_fields]

    # Positional disambiguation: in semicolon parentheticals, the first field
    # can be an unknown date when the rest of the row has enough context to
    # prove it. Later dash fields still use the size parser so forms like
    # "--,YD" keep their existing unknown-dimension meaning.
    if _leading_dash_is_unknown_date(paren_fields, types):
        types[0] = 'date'

    # Positional disambiguation: ASCC entries have at most one size field.
    # If a second 'size' appears and it's a bare number (no shape code, no
    # dateformat, no dimension separator), reclassify it as 'rate'.
    size_seen = False
    saw_dash = False
    for i, (field, ftype) in enumerate(zip(paren_fields, types)):
        if ftype != 'size':
            continue
        fstrip = field.strip()
        # A bare-dash placeholder (`-`, `--`) is an *unknown* size, not a real
        # measurement, so it must not consume the single size slot.
        if fstrip in ('-', '--'):
            saw_dash = True
            continue
        is_bare = bool(BARE_NUMBER_RE.match(fstrip))
        if is_bare and saw_dash and not size_seen:
            # First real number after an unknown-size `--` placeholder. It keeps
            # the size slot when it's a plausible diameter (Issue #25B, Amelia
            # "(--;28;...)" -> circle size 28, previously mis-read as a rate),
            # but a rate-magnitude value stays a rate (PONTIAC
            # "Same(--;2;Red) Drop rate" -> 2c drop rate, not a 2mm circle).
            if float(fstrip) < MIN_BARE_DIAMETER_MM:
                types[i] = 'rate'
            else:
                size_seen = True
            continue
        # Legacy positional rule (unchanged for non-dash listings): the first
        # size fills the slot; a later bare number is a rate.
        if size_seen and is_bare:
            types[i] = 'rate'
        else:
            size_seen = True

    return types

TRUNCATED_DATE_RE = re.compile(r'^\d{3}-\d{0,2}$')

SIZE_WITH_DASH_RE = re.compile(
    r'^(?:' + SHAPE_CODE_PAT + r'|arc)[\s\-]*-{1,2}$', re.IGNORECASE
)

BARE_RATE_RE = re.compile(
    r'^(?:(?:large|fancy|shaded|Double|small|negative|stencil)\s+)?'
    r'(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLDM]+)'
    r'(?:\s*,\s*(?:\d+(?:-\d+(?:/\d+)?)?|[IVXLDM]+))*$'
)

IRREGULAR_SIZE_RE = re.compile(r'^irregular\s+\d', re.IGNORECASE)

MULTI_DIM_RE = re.compile(r'^\d{2,3}\s*,\s*\d{2,3}$')

def triage_other_field(text):
    """Attempt reclassification of an 'other' field.
    Returns (new_type, parsed_result) or ('other', None) if unresolvable."""
    t = text.strip()

    # Truncated date: "185-", "186-", "183-51"
    if TRUNCATED_DATE_RE.match(t):
        # Treat as approximate date range
        prefix = t.split('-')[0]
        suffix = t.split('-')[1] if '-' in t else ''
        if len(prefix) == 3:
            decade_base = int(prefix + '0')
            if suffix and suffix.isdigit():
                year_end = int(prefix + suffix) if len(suffix) == 1 else int('1' + suffix) if len(suffix) == 2 else decade_base + 9
            else:
                year_end = decade_base + 9
            return 'date', {
                'date_month': None, 'date_day': None,
                'date_year_start': decade_base,
                'date_year_end': year_end,
                'date_granularity': 'RANGE',
                'date_is_circa': False,
                'date_raw': t,
                'date_error': 'reclassified from other (truncated date)',
            }

    # Size with unknown dim: "DC--", "DLC--", "arc--"
    if SIZE_WITH_DASH_RE.match(t):
        # Extract shape code
        shape = re.match(r'^([A-Za-z]+)', t).group(1).upper()
        return 'size', {
            'size_shape_code': shape,
            'size_dim1': None, 'size_dim2': None,
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_desc_note': None,
            'size_raw': t, 'size_error': None,
        }

    # Irregular size: "irregular 34"
    if IRREGULAR_SIZE_RE.match(t):
        return 'size', parse_size_field(t)

    # Multi-dimension: "30,32"
    if MULTI_DIM_RE.match(t):
        dims = t.split(',')
        return 'size', {
            'size_shape_code': None,
            'size_dim1': float(dims[0].strip()),
            'size_dim2': float(dims[1].strip()),
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_desc_note': None,
            'size_raw': t, 'size_error': None,
        }

    # Bare rate amounts or roman+amount combos: "5,10", "12-1/2", "V,X", "Double 50"
    if BARE_RATE_RE.match(t):
        return 'rate', parse_rate_field(t)

    # Color with unknown terms (partial match)
    tokens = [tok.strip() for tok in t.split(',') if tok.strip()]
    known_count = sum(1 for tok in tokens if is_color_token(tok))
    if known_count > 0 and known_count >= len(tokens) - 1:
        # At least one unknown term but majority are colors -> reclassify
        return 'color', [t.upper() for t in tokens]

    return 'other', None

def _any_manuscript_rate(parsed_rates):
    """True if any parsed rate token carries the [ms] manuscript bracket."""
    for group in parsed_rates:
        tokens = group if isinstance(group, list) else [group]
        for t in tokens:
            if isinstance(t, dict) and t.get('rate_is_manuscript'):
                return True
    return False


def _has_real_size_device(parsed_sizes):
    """True if any parsed size is a real struck device (a dimension or an
    explicit shape code), not just an unknown `--` placeholder."""
    return any(
        s.get('size_dim1') is not None or s.get('size_shape_code')
        for s in parsed_sizes
    )


def subparse_fields(row):
    """Apply the appropriate sub-parser to each paren field based on its type.
    Returns parallel lists: parsed_dates, parsed_sizes, parsed_rates, parsed_colors,
    plus is_manuscript flag and other_fields list.

    is_manuscript is derived from paren `(ms)` fields, then *unioned* with the
    optional per-row `Manuscript` CSV column (truthy values promote; the column
    cannot demote a paren-detected manuscript).
    """
    fields = row['paren_fields']
    types = row['paren_field_types']

    parsed_dates = []
    parsed_sizes = []
    parsed_rates = []
    parsed_colors = []
    is_manuscript = False
    other_fields = []
    reclassified = []

    for i, (field, ftype) in enumerate(zip(fields, types)):
        if ftype == 'ms':
            is_manuscript = True
        elif ftype == 'date':
            parsed_dates.append(parse_date_field(field))
        elif ftype == 'size':
            parsed_sizes.append(parse_size_field(field))
        elif ftype == 'rate':
            parsed_rates.append(parse_rate_field(field))
        elif ftype == 'color':
            parsed_colors.extend(parse_color_field(field))
        elif ftype == 'other':
            new_type, parsed = triage_other_field(field)
            if new_type != 'other':
                reclassified.append({
                    'position': i, 'original_type': 'other',
                    'new_type': new_type, 'field': field,
                })
                if new_type == 'date':
                    parsed_dates.append(parsed)
                elif new_type == 'size':
                    parsed_sizes.append(parsed)
                elif new_type == 'rate':
                    if isinstance(parsed, list):
                        parsed_rates.append(parsed)
                    else:
                        parsed_rates.append([parsed])
                elif new_type == 'color':
                    parsed_colors.extend(parsed)
            else:
                other_fields.append(field)

    # A manuscript-bracketed annotation ([ms]) with no real handstamp device
    # (no dimensioned size, no shape code) means the marking itself is
    # manuscript -- BARRY "(c.1861-63;--;Congressional frank[ms];Black)" is a
    # handwritten congressional frank, not a struck townmark. A [ms] alongside a
    # real device (HICKORY CORNERS "...;C-35,NOR;Pd. 3[ms];...") is only a
    # manuscript *rate* under a struck CDS, so it must NOT promote (Issue #34).
    if not is_manuscript and _any_manuscript_rate(parsed_rates) \
            and not _has_real_size_device(parsed_sizes):
        is_manuscript = True

    # Union the optional CSV `Manuscript` column (if present + truthy).
    if _csv_manuscript_truthy(row):
        is_manuscript = True

    return pd.Series({
        'parsed_dates': parsed_dates,
        'parsed_sizes': parsed_sizes,
        'parsed_rates': parsed_rates,
        'parsed_colors': parsed_colors,
        'is_manuscript': is_manuscript,
        'other_fields': other_fields,
        'reclassified_fields': reclassified,
    })

def _split_ms_date_token(token):
    """Split a captured ms_date_text into individual sub-tokens that
    parse_date_field understands. `1811,1849-55` -> [`1811`, `1849-55`]."""
    if token is None or (isinstance(token, float)) or token == '--':
        return []
    pieces = [t.strip() for t in str(token).split(',') if t.strip()]
    out = []
    for piece in pieces:
        if (
            out
            and re.search(MONTHS_PAT, out[-1], flags=re.IGNORECASE)
            and not re.search(r"\d{4}", out[-1])
            and re.match(r"^(?:c\.?\s*)?\d{4}$", piece, flags=re.IGNORECASE)
        ):
            out[-1] = out[-1] + ", " + piece
            continue
        out.append(piece)
    return out
