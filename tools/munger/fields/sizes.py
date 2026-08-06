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
    + r'|^' + SHAPE_CODE_PAT + r'\s*-{1,2}(?:\s*,\s*NOR)?$'
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

EMBEDDED_SHAPE_SIZE_RE = re.compile(
    r'(?<![A-Za-z])'
    r'(?P<shape>' + SHAPE_CODE_PAT + r')'
    r'(?![A-Za-z])'
    r'(?P<alts>(?:\s*&\s*[A-Za-z]+)*)'
    r'(?P<sep>[\s\-,]*)'
    r'(?P<dim>\d+\.?\d*(?:\s*x\s*\d+\.?\d*)?|--?)'
    r'(?P<suffix>\s*,\s*.+)?$',
    re.IGNORECASE
)

COMMA_DIAMETER_MIN_MM = 13.0

COMMA_DIAMETER_LIST_RE = re.compile(
    r'^\d+\.?\d*(?:\s*,\s*\d+\.?\d*)+$',
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

def _embedded_shape_size_candidate(t):
    """Return (normal_size_text, desc_note) for modified size fields.

    ASCC size fields sometimes carry prose before the shape-size signature,
    such as "framed arc-32x19". Treat the parse as best effort: keep the
    recognized shape/dimensions for structured fields, and send the source
    descriptor before the dimensions to desc as the catalog dumping ground.
    """
    m = EMBEDDED_SHAPE_SIZE_RE.search(t)
    if not m:
        return None

    desc_note = t[:m.start('dim')].strip(' \t-,')
    if not desc_note:
        return None

    dim = m.group('dim')
    suffix = m.group('suffix') or ''
    size_text = (
        m.group('shape')
        + (m.group('alts') or '')
        + ('' if dim.startswith('-') else '-')
        + dim
        + suffix
    )
    return size_text, desc_note

def is_comma_diameter_list(t):
    if not COMMA_DIAMETER_LIST_RE.match(t):
        return False
    try:
        dims = [float(part.strip()) for part in t.split(',') if part.strip()]
    except ValueError:
        return False
    return len(dims) >= 2 and all(dim >= COMMA_DIAMETER_MIN_MM for dim in dims)

def _comma_diameter_list_candidate(t):
    """Return the first diameter plus a desc note for "31,32" style sizes."""
    if not is_comma_diameter_list(t):
        return None
    dims = [part.strip() for part in t.split(',') if part.strip()]
    if len(dims) < 2:
        return None
    return dims[0], "Sizes: " + ",".join(dims)

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
            'size_qualifier': None, 'size_desc_note': None,
            'size_impression': size_impression,
            'size_raw': raw, 'size_error': None,
        }

    # Collapse ampersand-joined shape lists ("arc & SL-46x26" -> "arc-46x26")
    # before matching; size_raw below still records the original text.
    size_desc_note = None
    if COMMA_DIAMETER_LIST_RE.match(t) and not is_comma_diameter_list(t):
        return {
            'size_shape_code': None, 'size_dim1': None, 'size_dim2': None,
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_desc_note': None,
            'size_impression': size_impression,
            'size_raw': raw, 'size_error': f'unparsed size: {raw!r}',
        }
    comma_diams = _comma_diameter_list_candidate(t)
    if comma_diams:
        size_text, size_desc_note = comma_diams
        m = SIZE_PARSE_RE.match(size_text)
    else:
        m = SIZE_PARSE_RE.match(_collapse_ampersand_shape(t))
    if not m:
        size_desc_note = None
        embedded = _embedded_shape_size_candidate(t)
        if embedded:
            size_text, size_desc_note = embedded
            m = SIZE_PARSE_RE.match(_collapse_ampersand_shape(size_text))
    if not m:
        return {
            'size_shape_code': None, 'size_dim1': None, 'size_dim2': None,
            'size_dateformat': None, 'size_is_irregular': False,
            'size_qualifier': None, 'size_desc_note': None,
            'size_impression': size_impression,
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
        'size_desc_note': size_desc_note,
        'size_impression': size_impression,
        'size_raw': raw,
        'size_error': None,
    }
