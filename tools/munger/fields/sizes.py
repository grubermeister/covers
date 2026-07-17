import re

SIZE_SUFFIX_PAT = r'(?:YMDD|MDD|YMD|YD|MD|NOR)'

SHAPE_CODE_PAT = (
    r'(?:DLDC|DLDO|DLC|DLO|Octagon|Box|Arc|Pmk|SL|DC|DO|NOR|O|C)'
)

SIZE_IMPRESSION_PREFIX_RE = re.compile(r'^(negative|stencil)\s+', re.IGNORECASE)
SIZE_IMPRESSION_BY_TOKEN = {
    'negative': 'Negative',
    'stencil': 'Stencil',
}

SIZE_FIELD_RE = re.compile(
    r'(?:'
    + r'(?:(?:negative|stencil)\s+)?' + SHAPE_CODE_PAT + r'[\-\s]?\d'
    + r'|\d+\.?\d*\s*x\s*\d'
    + r'|^-{1,2}(?:\s*,'
    + SIZE_SUFFIX_PAT + r')?$'
    + r'|\d+\.?\d*\s*,'
    + SIZE_SUFFIX_PAT
    + r'|^'
    + SIZE_SUFFIX_PAT + r'$'
    + r')',
    re.IGNORECASE
)



SHAPE_CODES = ['DLDC', 'DLDO', 'DLC', 'DLO', 'Octagon', 'Box', 'Arc',
               'Pmk', 'SL', 'DC', 'DO', 'NOR', 'O', 'C']

SHAPE_CODE_SET = {s.upper() for s in SHAPE_CODES}

SIZE_DATEFORMAT_CODES = {'YMDD', 'MDD', 'YMD', 'YD', 'MD'}

SIZE_PARSE_RE = re.compile(
    r'^(irregular\s+)?'              # optional irregular prefix
    r'(' + SHAPE_CODE_PAT + r')?'    # optional shape code
    r'[\s\-]*'                       # separator
    r'('                             # dimension group
    r'  \d+\.?\d*\s*x\s*\d+\.?\d*'  # WxH
    r'  |\d+\.?\d*'                  # single diameter
    r'  |--?'                        # dash = unknown
    r')?'
    r'(?:\s*,\s*(.+))?'             # optional suffix (dateformat, qualifier)
    r'$',
    re.IGNORECASE | re.VERBOSE
)

_AMP_SHAPE_RE = re.compile(
    r'^(' + SHAPE_CODE_PAT + r')'           # first token: known shape
    r'((?:\s*&\s*[A-Za-z]+)+)'              # one or more '& word' alternatives
    r'(\s*[\s\-,].*)?$',                    # optional dimensions/suffix
    re.IGNORECASE
)

def _collapse_ampersand_shape(t):
    """If t looks like '<shape_a> & <shape_b> [& ...] <rest>', return
    '<first valid shape> <rest>'. Otherwise return t unchanged.
    A token is "valid" if its upper-cased form is in SHAPE_CODE_SET.

    The shape list may be separated from the dimensions by a dash or a
    comma ("arc & SL-46x26", "arc & SL,46"). A comma followed by a digit is
    normalized to a dash so the dimension parses as a dimension; a comma
    followed by a letter (suffix codes like ",YD") is left untouched.
    """
    if '&' not in t:
        return t
    m = _AMP_SHAPE_RE.match(t)
    if not m:
        return t
    first_token = m.group(1)
    alternatives = m.group(2)
    rest = m.group(3) or ''
    rest_stripped = rest.lstrip()
    if rest_stripped.startswith(',') and re.match(r'\s*\d', rest_stripped[1:]):
        rest = '-' + rest_stripped[1:].lstrip()
    # Ordered candidate list: first_token, then each '& word' alternative
    candidates = [first_token]
    candidates.extend(re.findall(r'&\s*([A-Za-z]+)', alternatives))
    for cand in candidates:
        if cand.upper() in SHAPE_CODE_SET:
            return cand + rest
    return t

def parse_size_field(text):
    """Decompose a size-classified paren field into components."""
    raw = text.strip()
    t = raw
    size_impression = None
    impression_m = SIZE_IMPRESSION_PREFIX_RE.match(t)
    if impression_m:
        impression_key = impression_m.group(1).lower()
        size_impression = SIZE_IMPRESSION_BY_TOKEN[impression_key]
        t = t[impression_m.end():].strip()

    # Catch bare dashes
    if t in ('-', '--'):
        return {
            'size_shape_code': None, 'size_dim1': None, 'size_dim2': None,
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_impression': size_impression,
            'size_raw': raw, 'size_error': None,
        }

    # Collapse ampersand-joined shape lists ("arc & SL-46x26" -> "arc-46x26")
    # before matching; size_raw below still records the original text.
    m = SIZE_PARSE_RE.match(_collapse_ampersand_shape(t))
    if not m:
        return {
            'size_shape_code': None, 'size_dim1': None, 'size_dim2': None,
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_impression': size_impression,
            'size_raw': raw, 'size_error': f'unparsed size: {raw!r}',
        }

    irregular_prefix = m.group(1)
    shape_raw = m.group(2)
    dim_raw = m.group(3)
    suffix_raw = m.group(4)

    is_irregular = bool(irregular_prefix)
    shape_code = shape_raw.upper() if shape_raw else None

    # Dimensions
    dim1, dim2 = None, None
    if dim_raw and dim_raw not in ('-', '--'):
        if 'x' in dim_raw.lower():
            parts = re.split(r'\s*x\s*', dim_raw, flags=re.IGNORECASE)
            dim1 = float(parts[0]) if parts[0] else None
            dim2 = float(parts[1]) if len(parts) > 1 and parts[1] else None
        else:
            dim1 = float(dim_raw)

    # Suffix: dateformat code, NOR, or free-text qualifier
    dateformat = None
    qualifier = None
    if suffix_raw:
        # May contain multiple tokens: "YD", "MDD", "NOR", "YMDD below"
        suffix_upper = suffix_raw.strip().upper()
        # Check if it starts with a known dateformat code
        for code in sorted(SIZE_DATEFORMAT_CODES, key=len, reverse=True):
            if suffix_upper.startswith(code):
                dateformat = code
                remainder = suffix_raw.strip()[len(code):].strip()
                if remainder:
                    qualifier = remainder
                break
        else:
            if suffix_upper == 'NOR':
                qualifier = 'NOR'
            else:
                qualifier = suffix_raw.strip()

    return {
        'size_shape_code': shape_code,
        'size_dim1': dim1,
        'size_dim2': dim2,
        'size_dateformat': dateformat,
        'size_is_irregular': is_irregular,
        'size_qualifier': qualifier,
        'size_impression': size_impression,
        'size_raw': raw,
        'size_error': None,
    }
