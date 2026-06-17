import re


BRACKET_OPENERS = '[{|'
BRACKET_CLOSERS = ']}|'
BRACKET_DELIMS = BRACKET_OPENERS + BRACKET_CLOSERS


def _is_bracket_opener(ch):
    return ch in BRACKET_OPENERS


def _is_bracket_closer(ch, depth):
    # ASCC OCR sometimes closes a hint with another opener, as in "[C[".
    return depth > 0 and ch in BRACKET_DELIMS


def split_rate_tokens(field_text):
    """Split rate field on commas, respecting bracket OCR variants.
    Returns list of raw token strings."""
    tokens = []
    current = []
    depth = 0
    for ch in field_text:
        if _is_bracket_closer(ch, depth):
            depth -= 1
            current.append(ch)
        elif _is_bracket_opener(ch):
            depth += 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            tokens.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        tokens.append(''.join(current).strip())
    return [t for t in tokens if t]

RATE_AMOUNT_RE = re.compile(
    r'(\d+(?:[/-]\d+(?:/\d+)?)?)'  # amount: "3", "12-1/2", "3/CENTS"
)

RATE_BRACKET_RE = re.compile(
    r'[\[\{\|]([^\[\{\|\]\}]+)[\[\{\|\]\}]'
)

RATE_KEYWORD_RE = re.compile(
    r'\b(PAID|FREE|STEAM|DUE)\b', re.IGNORECASE
)

PM_RE = re.compile(r'P\.?M\.?\s*(Free|frank)', re.IGNORECASE)

NEGATIVE_RE = re.compile(r'^negative\s+', re.IGNORECASE)

ROMAN_RE = re.compile(r'^[IVXLCDM]+$')

def parse_rate_token(tok):
    """Parse a single rate token into structured components."""
    t = tok.strip()

    result = {
        'rate_keyword': None,
        'rate_amount_raw': None,
        'rate_bracket': None,
        'rate_is_manuscript': False,
        'rate_impression': None,
        'rate_raw': t,
    }

    # Check for negative impression prefix
    neg_m = NEGATIVE_RE.match(t)
    if neg_m:
        result['rate_impression'] = 'Negative'
        t = t[neg_m.end():]

    # P.M. notation
    pm_m = PM_RE.search(t)
    if pm_m:
        pm_type = pm_m.group(1).lower()
        if pm_type == 'free':
            result['rate_keyword'] = 'PM_FREE'
        else:
            result['rate_keyword'] = 'PM_FRANK'
        # May have trailing rate: "P.M.Free-Paid 10"
        remainder = t[pm_m.end():].strip().lstrip('-')
        if remainder:
            kw_m = RATE_KEYWORD_RE.search(remainder)
            if kw_m:
                result['rate_keyword'] = 'PM_FREE'  # compound; keep PM_FREE
                amt_after = remainder[kw_m.end():].strip()
                if amt_after:
                    amt_m = RATE_AMOUNT_RE.search(amt_after)
                    if amt_m:
                        result['rate_amount_raw'] = amt_m.group(1)
        return result

    # Bracket: [ms], [C], [F], [box], etc.
    br_m = RATE_BRACKET_RE.search(t)
    if br_m:
        bracket_val = br_m.group(1).strip()
        if bracket_val.lower() == 'ms':
            result['rate_is_manuscript'] = True
        else:
            result['rate_bracket'] = bracket_val

    # Keyword: PAID, FREE, STEAM, DUE
    kw_m = RATE_KEYWORD_RE.search(t)
    if kw_m:
        result['rate_keyword'] = kw_m.group(1).upper()

    # Amount: first numeric sequence not inside a keyword or bracket-only context.
    # Strip bracket content and keyword to find the rate amount.
    # Note: "with NN" (e.g. "with 24") is editorial filler in the catalog
    # text -- no keyword is assigned. The amount is extracted normally and
    # the leading "with" is preserved in rate_raw for downstream inscription.
    t_stripped = RATE_BRACKET_RE.sub('', t)
    t_stripped = RATE_KEYWORD_RE.sub('', t_stripped)
    t_stripped = PM_RE.sub('', t_stripped)
    t_stripped = t_stripped.replace('/', ' ').strip()
    amt_m = RATE_AMOUNT_RE.search(t_stripped)
    if amt_m:
        result['rate_amount_raw'] = amt_m.group(1)

    # Roman numeral check (V, X, etc.) when no other signal
    if (result['rate_keyword'] is None and result['rate_amount_raw'] is None
            and not result['rate_is_manuscript']):
        clean = RATE_BRACKET_RE.sub('', t).strip()
        if ROMAN_RE.match(clean):
            result['rate_amount_raw'] = clean

    return result

def parse_rate_field(text):
    """Decompose a rate-classified paren field into a list of parsed tokens."""
    tokens = split_rate_tokens(text)
    return [parse_rate_token(t) for t in tokens]
