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

IMPRESSION_PREFIX_RE = re.compile(r'^(negative|stencil)\s+', re.IGNORECASE)
IMPRESSION_BY_TOKEN = {
    'negative': 'Negative',
    'stencil': 'Stencil',
}
IMPRESSION_PREFIX_RE = re.compile(r'^(negative|stencil)\s+', re.IGNORECASE)
IMPRESSION_BY_TOKEN = {
    'negative': 'Negative',
    'stencil': 'Stencil',
}

ROMAN_RE = re.compile(r'^[IVXLDM]+$')

INLINE_RATE_AMOUNT_TEXT_RE = (
    r'(?:\d+(?:[/-]\d+(?:/\d+)?)?|[VX])'
    r'(?:\s*(?:Cts?\.?|CENTS?))?'
)
INLINE_RATE_BRACKET_TEXT_RE = (
    r'(?:\s*[\[\{\|][^\[\{\|\]\}]*[\[\{\|\]\}])?'
)
INLINE_RATE_KEYWORD_FIRST_RE = re.compile(
    r'^(?:PAID|FREE|STEAM|DUE)\b'
    r'(?:[\s/]*' + INLINE_RATE_AMOUNT_TEXT_RE + r')?'
    + INLINE_RATE_BRACKET_TEXT_RE + r'\.?$',
    re.IGNORECASE,
)
INLINE_RATE_AMOUNT_FIRST_RE = re.compile(
    r'^' + INLINE_RATE_AMOUNT_TEXT_RE + r'\s*/?\s*'
    r'(?:PAID|FREE|STEAM|DUE)\b'
    + INLINE_RATE_BRACKET_TEXT_RE + r'\.?$',
    re.IGNORECASE,
)
INLINE_RATE_BARE_AMOUNT_RE = re.compile(
    r'^' + INLINE_RATE_AMOUNT_TEXT_RE + INLINE_RATE_BRACKET_TEXT_RE + r'\.?$',
    re.IGNORECASE,
)


def _clean_inline_rate_tail(text):
    return str(text or '').strip().rstrip('.').strip()


def _inline_rate_tail(text, allow_bare_amount=False):
    tail = _clean_inline_rate_tail(text)
    if not tail:
        return False
    if INLINE_RATE_KEYWORD_FIRST_RE.match(tail):
        return True
    if INLINE_RATE_AMOUNT_FIRST_RE.match(tail):
        return True
    return allow_bare_amount and bool(INLINE_RATE_BARE_AMOUNT_RE.match(tail))


def split_inline_rate_from_inscription(inscription):
    """Split a trailing rate/auxmark token from townmark inscription text."""
    value = re.sub(r'\s+', ' ', str(inscription or '').strip())
    if not value:
        return '', []

    slash_matches = list(re.finditer(r'\s*/\s*', value))
    for match in slash_matches:
        head = value[:match.start()].strip()
        tail = value[match.end():].strip()
        if head and _inline_rate_tail(tail):
            return head, [parse_rate_token(_clean_inline_rate_tail(tail))]

    for match in reversed(slash_matches):
        head = value[:match.start()].strip()
        tail = value[match.end():].strip()
        if head and _inline_rate_tail(tail, allow_bare_amount=True):
            return head, [parse_rate_token(_clean_inline_rate_tail(tail))]

    for match in re.finditer(r'\s+', value):
        head = value[:match.start()].strip()
        tail = value[match.end():].strip()
        if head and _inline_rate_tail(tail):
            return head, [parse_rate_token(_clean_inline_rate_tail(tail))]

    return value, []

def parse_rate_token(tok):
    """Parse a single rate token into structured components."""
    t = tok.strip()

    result = {
        'rate_keyword': None,
        'rate_amount_raw': None,
        'rate_bracket': None,
        'rate_is_manuscript': False,
        'rate_impression': None,
        'rate_inscription_raw': None,
        'rate_raw': t,
    }

    # Check for an impression prefix such as "negative 5" or "stencil 5".
    impression_m = IMPRESSION_PREFIX_RE.match(t)
    if impression_m:
        impression_key = impression_m.group(1).lower()
        result['rate_impression'] = IMPRESSION_BY_TOKEN[impression_key]
        t = t[impression_m.end():].strip()
        result['rate_inscription_raw'] = t

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
        bracket_key = bracket_val.rstrip('.').lower()
        if bracket_key == 'ms':
            result['rate_is_manuscript'] = True
        elif bracket_key in ('neg', 'negative'):
            result['rate_impression'] = 'Negative'
        elif bracket_key == 'stencil':
            result['rate_impression'] = 'Stencil'
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
    t_stripped = t_stripped.strip()
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
