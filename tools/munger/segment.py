import re

from .classify import TRAILING_VALUE_PATTERN


import pandas as pd
def find_last_semicolon_paren(text):
    """Find the last balanced paren group that contains a semicolon.
    Returns (open_pos, close_pos) or None."""
    # Walk backward through all ')' positions
    i = len(text) - 1
    while i >= 0:
        if text[i] == ')':
            close_pos = i
            depth = 1
            j = i - 1
            while j >= 0 and depth > 0:
                if text[j] == ')':
                    depth += 1
                elif text[j] == '(':
                    depth -= 1
                j -= 1
            if depth == 0:
                open_pos = j + 1
                body = text[open_pos + 1:close_pos]
                if ';' in body:
                    return (open_pos, close_pos)
            # This paren group had no semicolon; keep scanning left
            i = (j + 1) - 1 if depth == 0 else i - 1
        else:
            i -= 1
    return None

def find_last_paren_group(text):
    """Find the last balanced paren group (any content).
    Returns (open_pos, close_pos) or None."""
    close_pos = text.rfind(')')
    if close_pos == -1:
        return None
    depth = 1
    j = close_pos - 1
    while j >= 0 and depth > 0:
        if text[j] == ')':
            depth += 1
        elif text[j] == '(':
            depth -= 1
        j -= 1
    if depth != 0:
        return None  # unmatched
    open_pos = j + 1
    return (open_pos, close_pos)

def classify_entry_form(row):
    """Determine structural form: manuscript, semicolon_paren, simple_paren, or no_paren."""
    # Manuscript-section rows take a dedicated parser path; see Step 0.5
    # and the parse_manuscript_row overlay cell after segmentation.
    if row.get('is_manuscript_section'):
        return 'manuscript'

    text = row['clean_text']

    # Form 1: last paren group with semicolons
    if find_last_semicolon_paren(text) is not None:
        return 'semicolon_paren'

    # Form 2: relationship indicator + has parens (single-attribute modification)
    if row['s1_relationship'] and '(' in text:
        return 'simple_paren'

    # Form 3: everything else
    return 'no_paren'

TRAILING_VALUE_RE = re.compile(
    r'(\d[\d,]*(?:\.\d+)?-|\d[\d,]*(?:\.\d+)?(?:/\d[\d,]*(?:\.\d+)?)*|---?)(?:\.)?\s*$'
)

def segment_entry(row):
    """Split entry into head / paren_body / tail based on entry_form."""
    text = row['clean_text']
    form = row['entry_form']

    # Manuscript-section rows are handled by parse_manuscript_row in the
    # next cell (overlay). Emit empty placeholders here so the column
    # shape matches the standard branches.
    if form == 'manuscript':
        return pd.Series({'seg_head': None, 'seg_paren': None, 'seg_tail': None,
                          'seg_error': None})

    if form == 'semicolon_paren':
        bounds = find_last_semicolon_paren(text)
        if bounds is None:
            return pd.Series({'seg_head': text, 'seg_paren': None, 'seg_tail': None,
                              'seg_error': 'semicolon_paren but no match'})
        open_pos, close_pos = bounds
        head = text[:open_pos].strip()
        paren_body = text[open_pos + 1:close_pos]
        tail = text[close_pos + 1:].strip()
        return pd.Series({'seg_head': head, 'seg_paren': paren_body,
                          'seg_tail': tail, 'seg_error': None})

    elif form == 'simple_paren':
        bounds = find_last_paren_group(text)
        if bounds is None:
            return pd.Series({'seg_head': text, 'seg_paren': None, 'seg_tail': None,
                              'seg_error': 'simple_paren but no paren found'})
        open_pos, close_pos = bounds
        head = text[:open_pos].strip()
        paren_body = text[open_pos + 1:close_pos]
        tail = text[close_pos + 1:].strip()
        return pd.Series({'seg_head': head, 'seg_paren': paren_body,
                          'seg_tail': tail, 'seg_error': None})

    else:  # no_paren
        m = TRAILING_VALUE_RE.search(text)
        if m is None:
            return pd.Series({'seg_head': text, 'seg_paren': None, 'seg_tail': None,
                              'seg_error': 'no_paren but no trailing value'})
        tail = m.group(1)
        head = text[:m.start()].strip()
        return pd.Series({'seg_head': head, 'seg_paren': None,
                          'seg_tail': tail, 'seg_error': None})

def split_paren_fields(row):
    """Split seg_paren on semicolons into positional list."""
    paren = row['seg_paren']
    if paren is None or (isinstance(paren, float) and pd.isna(paren)):
        return []
    fields = [f.strip() for f in paren.split(';')]
    return fields

TAIL_VALUE_RE = re.compile(
    r'('
    r'\d[\d,]*(?:\.\d+)?-'       # dangling-dash value: 7-
    r'|'
    r'\d[\d,]*(?:\.\d+)?'        # first number: 125, 1,200, 3500.00
    r'(?:[-/]\d[\d,]*(?:\.\d+)?)*'  # optional slash tiers or range: /15, -200
    r'|---?'                      # dashes: -- or ---
    r')(?:\.)?\s*$'
)

def decompose_tail(row):
    """Split seg_tail into annotation (nullable) and valuation."""
    tail = row['seg_tail']
    form = row['entry_form']

    if tail is None or (isinstance(tail, float) and pd.isna(tail)) or tail.strip() == '':
        return pd.Series({'tail_annotation': None, 'tail_valuation': None,
                          'tail_error': 'empty tail'})

    tail = tail.strip()

    # For no_paren, Step 2 already isolated the valuation
    if form == 'no_paren':
        return pd.Series({'tail_annotation': None, 'tail_valuation': tail,
                          'tail_error': None})

    # For paren forms, split on trailing value
    m = TAIL_VALUE_RE.search(tail)
    if m is None:
        return pd.Series({'tail_annotation': tail if tail else None,
                          'tail_valuation': None,
                          'tail_error': 'no valuation found in tail'})

    valuation = m.group(1)
    annotation = tail[:m.start()].strip()
    if annotation in ('', '.', '*', '+'):
        annotation = None
    return pd.Series({
        'tail_annotation': annotation,
        'tail_valuation': valuation,
        'tail_error': None
    })

def split_valuation_tiers(val_str):
    """Split a valuation string into positional tiers.
    Returns list of tier strings. Dashes become [None]. Ranges stay intact."""
    if val_str is None or (isinstance(val_str, float) and pd.isna(val_str)):
        return []
    val_str = val_str.strip()
    if val_str in ('--', '---'):
        return [None]  # unpriced
    # Split on / for tier separation
    tiers = val_str.split('/')
    return tiers
