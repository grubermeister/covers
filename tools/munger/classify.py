import re
import pandas as pd

from .text_utils import strip_dot_leaders


_MS_TRUTHY = {'1', 'true', 'yes', 'y', 't'}

def _csv_manuscript_truthy(row):
    """True if the optional Manuscript column is present and truthy for this row."""
    val = row.get('Manuscript')
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    return str(val).strip().lower() in _MS_TRUTHY

RELATIONSHIP_PATTERN = re.compile(
    r'^\+?'
    r'(?:'
    r'Same'
    r'|\*?[(\[{]L[)\]}]\*?'
    r'|\*?[(\[{]E[)\]}]\*?'
    r')',
    re.IGNORECASE
)

def detect_cross_reference(text):
    """True if the entry is a pure cross-reference (See X, no semicolon-parenthetical)."""
    has_see = bool(re.search(r'\bSee\b', text))
    has_semicolon_paren = bool(re.search(r'\([^)]*;[^)]*\)', text))
    return has_see and not has_semicolon_paren

def detect_fragment(text):
    """True if the entry looks like a segmentation fragment."""
    t = text.strip()
    if not t:
        return True
    # Unmatched closing paren: ')' appears before any '('
    first_open = t.find('(')
    first_close = t.find(')')
    if first_close != -1 and (first_open == -1 or first_close < first_open):
        return True
    # Starts with lowercase (mid-sentence fragment)
    # But exclude known patterns: 'c1850' (c-date), 'la.' (abbreviation)
    if t[0].islower() and not re.match(r'^c\d{4}', t):
        return True
    return False

TRAILING_VALUE_PATTERN = re.compile(
    r'(?:'
    r'\d[\d,]*(?:\.\d+)?-'  # dangling-dash value: 7-
    r'|'
    r'\d[\d,]*(?:\.\d+)?'  # number: 3500.00 or 1,500 or 50
    r'|---?'                # dashes: -- or ---
    r')(?:\.)?\s*$'
)

def detect_structural_anatomy(text):
    """Returns a dict of which structural sub-signals are present."""
    result = {
        'semicolon_paren': bool(re.search(r'\([^)]*;[^)]*\)', text)),
        'four_digit_year': bool(re.search(r'\b1[78]\d{2}\b', text)),
        'decade_ref': bool(re.search(r"1[78]\d0['\'s]", text)),
        'c_year': bool(re.search(r'\bc1[78]\d{2}\b', text, re.IGNORECASE)),
    }
    result['any'] = any(result.values())
    return result

def classify_entry(row):
    """Apply signals in priority order. Returns (classification, confidence, reason)."""

    # Signal 1: relationship indicator -> auto listing
    if row['s1_relationship']:
        return 'listing', 'high', 'relationship_indicator'

    # Signal 2: cross-reference
    if row['s2_cross_ref']:
        return 'cross_reference', 'high', 'see_pattern'

    # Signals 4+5 conjunction
    has_value = row['s4_trailing_value']
    has_anatomy = row['s5_anatomy']

    # Signal 3: fragment -- only reject if the row lacks strong listing signals.
    # A lowercase-initial entry with both trailing value and full anatomy is a
    # stylistic catalog entry (e.g. 'wmfbURG'), not a segmentation artifact.
    if row['s3_fragment']:
        if has_value and has_anatomy:
            return 'listing', 'medium', 'fragment_with_anatomy'
        return 'non_entry', 'high', 'fragment'

    if has_value and has_anatomy:
        return 'listing', 'high', 'value_and_anatomy'
    elif has_value and not has_anatomy:
        return 'listing', 'low', 'value_only'
    elif has_anatomy and not has_value:
        return 'listing', 'low', 'anatomy_only'
    else:
        return 'non_entry', 'high', 'no_signals'
