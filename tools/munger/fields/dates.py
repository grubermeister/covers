import re

MONTHS_PAT = (
    r'(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?'
    r'|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b)'
)

DATE_FIELD_RE = re.compile(
    r'(?:' + MONTHS_PAT + r'|c?1[5-8]\d{2})',
    re.IGNORECASE
)



MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

FULL_DATE_RE = re.compile(
    r'^\s*(' + MONTHS_PAT + r')\.?\s*'
    r'(\d{1,2})\s*,\s*'
    r'(\d{4})\s*$',
    re.IGNORECASE
)

MONTH_YEAR_RE = re.compile(
    r'^\s*(' + MONTHS_PAT + r')\.?\s+'
    r'(\d{4})\s*$',
    re.IGNORECASE
)

NUMERIC_MONTH_YEAR_RE = re.compile(
    r'^\s*(0?[1-9]|1[0-2])\s+'
    r'(\d{4})\s*$',
    re.IGNORECASE
)

YEAR_RANGE_RE = re.compile(
    r'^\s*(?:c\.?\s*)?(\d{4})\s*[-]\s*(\d{2,4})\s*$',
    re.IGNORECASE
)

DECADE_RE = re.compile(r"^\s*(?:c\.?\s*)?(\d{4})'?s\s*$", re.IGNORECASE)

BARE_YEAR_RE = re.compile(r'^\s*(?:c\.?\s*)?(\d{4})\s*$', re.IGNORECASE)

CIRCA_RE = re.compile(r'^c\.?\s*\d|\bc\.?\s*\d', re.IGNORECASE)

def _unknown_date_result(raw):
    """Return the parse shape for a catalog date slot marked unknown.

    The ASCC semicolon fields are positional. A leading "--" can mean the
    date slot is present but unknown; downstream date exporters skip this
    because date_granularity is "UNKNOWN" rather than DAY/MONTH/YEAR/RANGE.
    """
    return {
        'date_month': None, 'date_day': None,
        'date_year_start': None, 'date_year_end': None,
        'date_granularity': 'UNKNOWN',
        'date_is_circa': False,
        'date_raw': raw,
        'date_error': None,
    }

def parse_date_field(text):
    """Decompose a date-classified paren field into structured components."""
    t = text.strip()
    if t == '--':
        return _unknown_date_result(t)

    is_circa = bool(CIRCA_RE.match(t))

    # 1. Full date: Month day, year
    m = FULL_DATE_RE.match(t)
    if m:
        month_str = m.group(1).lower().rstrip('.')
        month = MONTH_MAP.get(month_str)
        day = int(m.group(2))
        year = int(m.group(3))
        return {
            'date_month': month,
            'date_day': day,
            'date_year_start': year,
            'date_year_end': year,
            'date_granularity': 'DAY',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    # 2. Decade: 1850's or 1850s (apostrophe optional)
    m = DECADE_RE.match(t)
    if m:
        base = int(m.group(1))
        return {
            'date_month': None,
            'date_day': None,
            'date_year_start': base,
            'date_year_end': base + 9,
            'date_granularity': 'DECADE',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    # 3. Year range: 1850-53
    m = YEAR_RANGE_RE.match(t)
    if m:
        y1 = int(m.group(1))
        y2_str = m.group(2)
        if len(y2_str) == 2:
            y2 = int(str(y1)[:2] + y2_str)
        else:
            y2 = int(y2_str)
        return {
            'date_month': None,
            'date_day': None,
            'date_year_start': y1,
            'date_year_end': y2,
            'date_granularity': 'RANGE',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    # 4. Month + year (no day)
    m = MONTH_YEAR_RE.match(t)
    if m:
        month_str = m.group(1).lower().rstrip('.')
        month = MONTH_MAP.get(month_str)
        year = int(m.group(2))
        return {
            'date_month': month,
            'date_day': None,
            'date_year_start': year,
            'date_year_end': year,
            'date_granularity': 'MONTH',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    # 5. Numeric month + year (no day): 03 1852 -> March 1852.
    m = NUMERIC_MONTH_YEAR_RE.match(t)
    if m:
        month = int(m.group(1))
        year = int(m.group(2))
        return {
            'date_month': month,
            'date_day': None,
            'date_year_start': year,
            'date_year_end': year,
            'date_granularity': 'MONTH',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    # 6. Bare year
    m = BARE_YEAR_RE.match(t)
    if m:
        year = int(m.group(1))
        return {
            'date_month': None,
            'date_day': None,
            'date_year_start': year,
            'date_year_end': year,
            'date_granularity': 'YEAR',
            'date_is_circa': is_circa,
            'date_raw': t,
            'date_error': None,
        }

    return {
        'date_month': None, 'date_day': None,
        'date_year_start': None, 'date_year_end': None,
        'date_granularity': None, 'date_is_circa': is_circa,
        'date_raw': t,
        'date_error': f'unparsed date: {t!r}',
    }
