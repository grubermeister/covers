import re
from datetime import date as _date_cls
import pandas as pd


ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'D': 500, 'M': 1000}

def roman_to_int(s):
    """Convert a roman numeral string to integer. Returns None on failure."""
    if not s or not all(c in ROMAN_VALUES for c in s.upper()):
        return None
    total = 0
    prev = 0
    for ch in reversed(s.upper()):
        val = ROMAN_VALUES[ch]
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total

FRACTION_RE = re.compile(
    r'^(\d+)?'            # optional whole part
    r'[\-\s]*'            # optional separator
    r'(\d+)/(\d+)$'      # fraction
)

def parse_rate_amount(raw):
    """Parse a rate amount string to a float value in cents.
    Returns (numeric_value, is_roman) or (None, False) on failure.
    """
    if raw is None:
        return None, False

    s = str(raw).strip()
    if not s:
        return None, False

    # Roman numeral check
    if re.match(r'^[IVXLDM]+$', s):
        val = roman_to_int(s)
        if val is not None:
            return float(val), True

    # Fractional: 12-1/2, 6-1/4, 1/2
    m = FRACTION_RE.match(s)
    if m:
        whole = int(m.group(1)) if m.group(1) else 0
        num = int(m.group(2))
        den = int(m.group(3))
        if den > 0:
            return float(whole) + num / den, False

    # Plain integer or decimal
    try:
        return float(s), False
    except ValueError:
        return None, False

BRACKET_SHAPE_MAP = {
    'c': 'C',
    'o': 'O',
    'dc': 'DC',
    'do': 'DO',
    'dlc': 'DLC',
    'dlo': 'DLO',
    'dldc': 'DLDC',
    'dldo': 'DLDO',
    'box': 'BOX',
    'arc': 'ARC',
    'octagon': 'Octagon',
    'sl': 'SL',
    'rectangle': 'BOX',
    'oval': 'O',
    'circle': 'C',
}

BRACKET_DIM_RE = re.compile(r'(\d+\.?\d*)(?:\s*x\s*(\d+\.?\d*))?')

def _tm_codes_by_listing(frame):
    if frame is None or len(frame) == 0:
        return {}
    if 'source_listing_idx' not in frame.columns or 'code' not in frame.columns:
        return {}
    return frame.groupby('source_listing_idx')['code'].apply(list).to_dict()
