import re
from datetime import date as dt_date
import pandas as pd


SHAPE_SEEDS = [
    'SL - Straight Line', 'Box', 'O - Oval', 'C - Circle',
    'ARC - Arc or Semi-circle', 'Octagon',
    'DC - Double Circle', 'DO - Double Oval',
    'DLC - Double Line Circle', 'DLO - Double Line Oval',
    'DLDC - Double Line Double Circle', 'DLDO - Double Line Double Oval',
    'NOR - No Outer Rim',
    'Pictorial', 'Ornamental Mortised', 'Other',
]

LETTERING_SEEDS = [
    'Italic', 'Serif', 'Sans-serif', 'Small', 'Large',
    'Outline', 'Bold', 'Block', 'Gothic', 'Thick', 'Thin',
]

SHAPE_CODE_TO_NAME = {
    'SL':      'SL - Straight Line',
    'C':       'C - Circle',
    'O':       'O - Oval',
    'DC':      'DC - Double Circle',
    'DO':      'DO - Double Oval',
    'DLC':     'DLC - Double Line Circle',
    'DLO':     'DLO - Double Line Oval',
    'DLDC':    'DLDC - Double Line Double Circle',
    'DLDO':    'DLDO - Double Line Double Oval',
    'NOR':     'NOR - No Outer Rim',
    'BOX':     'Box',
    'ARC':     'ARC - Arc or Semi-circle',
    'OCTAGON': 'Octagon',
    'PMK':     'Other',
}

NO_PAREN_MANUSCRIPT_MAX_HEAD_CHARS = 24


def promote_no_paren_to_manuscript(row):
    """Return True when a terse no-paren listing is a manuscript marking.

    Rule choice: the no-paren form also covers ordinary parenless handstamps,
    so promotion is limited to rows whose current head text is 24 characters or
    less after relationship inheritance. Inherited sizes block promotion.
    """
    if row.get('entry_form') != 'no_paren':
        return False
    if row.get('is_manuscript'):
        return False
    parsed_sizes = row.get('parsed_sizes') or []
    if parsed_sizes:
        return False
    text = str(row.get('seg_head') or row.get('clean_text') or '').strip()
    return 0 < len(text) <= NO_PAREN_MANUSCRIPT_MAX_HEAD_CHARS


def resolve_effective_shape(row):
    # Priority: paren-body shape code > bare diameter > Default Shape column.
    # Manuscript rows always return None -- they carry no stamped shape.
    # Returns (effective_code_upper_or_None, source_label).

    # Manuscript rows carry no shape attribute; shape_id will be null in output.
    if row.get('is_manuscript_section') or row.get('is_manuscript'):
        return None, 'manuscript_no_shape'

    # 1. Paren-body shape (from parsed_sizes -- use first non-None)
    for s in row['parsed_sizes']:
        if s.get('size_shape_code'):
            return s['size_shape_code'].upper(), 'paren_body'

    # 1b. Bare diameter -> circle. A single measured dimension with no shape
    # code and no second dimension is a circular datestamp diameter: the catalog
    # writes "27" (not "SL-27") for a round mark, and reserves "WxH" for straight
    # lines/ovals. Stronger evidence than a section default, so it precedes it
    # (Issue #38: ADA.MI "(1837-45;27;Red)" was defaulting to SL).
    for s in row['parsed_sizes']:
        if (s.get('size_dim1') is not None and s.get('size_dim2') is None
                and not s.get('size_shape_code')):
            return 'C', 'bare_diameter'

    # 2. Section-level Default Shape
    default = row.get('Default Shape')
    if pd.notna(default) and str(default).strip():
        ds = str(default).strip().upper()
        name_to_code = {
            'CIRCLE': 'C', 'OVAL': 'O', 'STRAIGHT LINE': 'SL',
            'BOX': 'BOX', 'ARC': 'ARC', 'OCTAGON': 'OCTAGON',
            'DOUBLE CIRCLE': 'DC', 'DOUBLE OVAL': 'DO',
            'DOUBLE LINE CIRCLE': 'DLC', 'DOUBLE LINE OVAL': 'DLO',
            'NO OUTER RIM': 'NOR', 'FANCY': 'C',
        }
        if ds in SHAPE_CODE_TO_NAME:
            return ds, 'default_shape'
        if ds in name_to_code:
            return name_to_code[ds], 'default_shape'
        for code in sorted(SHAPE_CODE_TO_NAME.keys(), key=len, reverse=True):
            if ds.startswith(code):
                return code, 'default_shape'
        # Unrecognized default shape -- fall through to no_shape

    # 3. No catalog evidence for shape.
    return None, 'no_shape'

def resolve_shape_name(code_upper):
    # Map an ASCC shape code to a seed shape name.
    # Returns (shape_name, error_or_None).
    # None input (manuscript rows) returns (None, None) -- shape FK stays null.
    # Unknown codes map to 'Other'.
    if code_upper is None or (isinstance(code_upper, float) and pd.isna(code_upper)):
        return None, None
    if code_upper in SHAPE_CODE_TO_NAME:
        return SHAPE_CODE_TO_NAME[code_upper], None
    return 'Other', f'unknown shape code: {code_upper}'

def _nkey(v):
    return None if pd.isna(v) else v

_VALID_NAME_RE = re.compile(r"^[A-Z][A-Z .\-]*[A-Z.]$")

def confidence_level(warnings):
    n = len(warnings)
    if n == 0:
        return 'HIGH'
    elif n <= 2:
        return 'MEDIUM'
    else:
        return 'LOW'
