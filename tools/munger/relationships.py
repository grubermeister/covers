import re
import pandas as pd

from .fields.rates import split_inline_rate_from_inscription
from .text_utils import strip_trailing_state_suffix


LEADING_INSCRIPTION_MARKER_RE = re.compile(r"^\s*(?:\(\s*1\s*\))\s*", re.IGNORECASE)
CATALOG_DATE_MARKER_RE = re.compile(r"\s*[(\[{]\s*[EL]\s*[)\]}]\s*", re.IGNORECASE)


def _strip_unambiguous_star_marker(text):
    """Remove one boundary catalog star; preserve multi-star inscriptions."""
    if text.count('*') != 1:
        return text
    return re.sub(r"^\s*\*|\*\s*$", "", text).strip()


def strip_inscription_markers(inscription):
    """Remove catalog-only markers from inscription text."""
    if inscription is None or (isinstance(inscription, float) and pd.isna(inscription)):
        return ''
    text = CATALOG_DATE_MARKER_RE.sub(' ', str(inscription)).strip()
    text = _strip_unambiguous_star_marker(text)
    while True:
        cleaned = LEADING_INSCRIPTION_MARKER_RE.sub('', text, count=1).strip()
        cleaned = _strip_unambiguous_star_marker(cleaned)
        cleaned, _tokens = split_inline_rate_from_inscription(cleaned)
        if cleaned == text:
            return re.sub(r"\s+", " ", cleaned).strip()
        text = cleaned


def extract_town_root(inscription):
    """Town root = everything before the first '/', or whole string if no '/'."""
    if inscription is None or (isinstance(inscription, float) and pd.isna(inscription)):
        return ''
    inscription = strip_inscription_markers(inscription)
    if '/' in inscription:
        return inscription.split('/')[0]
    return inscription


def _split_location_state_suffix(text):
    value = str(text or '').strip()
    for pattern in (
        r"^(?P<location>.+?)(?P<state>/\s*[A-Za-z]{1,4}\.?)$",
        r"^(?P<location>.+?)(?P<state>\s+[A-Za-z]{1,4}\.?)$",
    ):
        match = re.match(pattern, value)
        if match:
            return match.group('location').strip(), match.group('state')
    return '', ''


def _compact_location_token(text):
    return re.sub(r"[^A-Za-z0-9]+", "", str(text or '')).upper()


def _trailing_location_token(text):
    match = re.search(r"([A-Za-z.]+)\s*$", str(text or '').strip())
    return match.group(1) if match else ''


def _same_suffix_repeats_parent_tail(parent_stem, suffix_location):
    parent_tail = _compact_location_token(_trailing_location_token(parent_stem))
    suffix_tail = _compact_location_token(suffix_location)
    return 2 <= len(parent_tail) <= 4 and parent_tail == suffix_tail


def parent_townmark_text_for_same(parent_inscription, parent_town, suffix=''):
    """Return carry-source inscription text to use when catalog text says Same.

    Explicit Same with a suffix such as /VA or C.H./VA uses the immediate
    carry-source inscription stem so it can
    replace or extend the location text without copying the word Same.
    """
    parent_text = ''
    if parent_inscription is not None and not (
        isinstance(parent_inscription, float) and pd.isna(parent_inscription)
    ):
        parent_text = str(parent_inscription).strip()
    if not parent_text:
        parent_text = str(parent_town or '').strip()
    suffix_text = str(suffix or '').strip()
    if not suffix_text:
        return parent_text
    if '/' in parent_text:
        return extract_town_root(parent_text).strip() or parent_text
    stem = strip_trailing_state_suffix(parent_text)
    return stem or parent_text


def resolve_same_inscription(parent_inscription, parent_town, suffix=''):
    parent_text = parent_townmark_text_for_same(parent_inscription, parent_town, suffix)
    suffix_text = str(suffix or '').strip()
    if not suffix_text:
        return strip_inscription_markers(parent_text)
    parent_source = str(parent_inscription or parent_town or '').strip()
    if '/' not in parent_source:
        suffix_location, suffix_state = _split_location_state_suffix(suffix_text)
        if suffix_state and _same_suffix_repeats_parent_tail(parent_text, suffix_location):
            return strip_inscription_markers(parent_text + suffix_state)
    sep = '' if suffix_text.startswith('/') else ' '
    return strip_inscription_markers(parent_text + sep + suffix_text)


def resolve_relationships(listings_df):
    """Walk listings in catalog order, resolve inheritance.

    Modifies listings_df in place, adding:
      parent_idx, prev_sibling_idx, resolved_inscription, resolved_town,
      s7_warnings

    parent_idx points at the most recent independent (parent) entry.
    prev_sibling_idx points at the carry-forward source for attribute
    inheritance: the immediately preceding sibling under the same
    parent, or (for the first child) the parent itself. None for
    independent and orphan-rel entries.
    """
    n = len(listings_df)
    parent_idx = [None] * n
    prev_sibling_idx = [None] * n
    resolved_inscription = [None] * n
    resolved_town = [None] * n
    s7_warnings = [[] for _ in range(n)]

    # Track the most recent independent entry by iteration position
    current_parent_pos = None
    # Track most recent child position per parent, for sibling-walk inheritance
    last_child_pos_by_parent = {}

    for pos in range(n):
        row = listings_df.iloc[pos]
        warnings = []

        if pd.isna(row['head_rel_type']) or row['head_rel_type'] is None:
            # --- Independent entry ---
            raw_inscription = row['head_name_body']
            if raw_inscription is None or (
                isinstance(raw_inscription, float) and pd.isna(raw_inscription)
            ):
                warnings.append('independent_no_name')
                inscription = ''
            else:
                inscription = strip_inscription_markers(raw_inscription)

            town = extract_town_root(inscription)

            parent_idx[pos] = None
            prev_sibling_idx[pos] = None
            resolved_inscription[pos] = inscription
            resolved_town[pos] = town
            current_parent_pos = pos
            last_child_pos_by_parent[pos] = None

        else:
            # --- Relationship entry ---
            if current_parent_pos is None:
                warnings.append('orphan_rel')
                # Best-effort: use own name body if any
                _nb = row['head_name_body']
                fallback = strip_inscription_markers(_nb)
                parent_idx[pos] = None
                prev_sibling_idx[pos] = None
                resolved_inscription[pos] = fallback
                resolved_town[pos] = extract_town_root(fallback) if fallback else ''
            else:
                parent_idx[pos] = listings_df.index[current_parent_pos]
                prev_child_pos = last_child_pos_by_parent.get(current_parent_pos)
                if prev_child_pos is None:
                    # First child: carry-forward source is the parent.
                    prev_sibling_idx[pos] = listings_df.index[current_parent_pos]
                    carry_source_pos = current_parent_pos
                else:
                    prev_sibling_idx[pos] = listings_df.index[prev_child_pos]
                    carry_source_pos = prev_child_pos
                last_child_pos_by_parent[current_parent_pos] = pos
                carry_inscription = resolved_inscription[carry_source_pos]
                carry_town = resolved_town[carry_source_pos]

                rel = row['head_rel_type']
                name_body = row['head_name_body']

                if rel == 'Same' and pd.notna(name_body):
                    name_body_clean = strip_inscription_markers(name_body)
                    # Different device, same town: reconstruct inscription.
                    # Same is a catalog placeholder, never inscription text.
                    # Use the immediate carry-source inscription stem, not the
                    # normalized
                    # post-office name, so punctuation and spelling stay tied
                    # to the prior resolved townmark text.
                    # When name_body does not start with '/' the source had
                    # a literal space between 'Same' and the name body
                    # (e.g. 'Same C.H./Va.') that parse_head stripped; put
                    # one space back to avoid 'ACCOMACKC.H./VA.'.
                    resolved_inscription[pos] = resolve_same_inscription(
                        carry_inscription,
                        carry_town,
                        name_body_clean,
                    )
                    if not name_body_clean.startswith('/'):
                        warnings.append('same_name_body_no_slash')
                    resolved_town[pos] = carry_town
                else:
                    # Same device (Same w/o name, (L), (E)): inherit
                    resolved_inscription[pos] = strip_inscription_markers(carry_inscription)
                    resolved_town[pos] = carry_town

                # Cross-section check
                parent_row = listings_df.iloc[current_parent_pos]
                if row.get('Default Shape') != parent_row.get('Default Shape'):
                    warnings.append('cross_section_parent')

        s7_warnings[pos] = warnings

    listings_df['parent_idx'] = parent_idx
    listings_df['prev_sibling_idx'] = prev_sibling_idx
    listings_df['resolved_inscription'] = resolved_inscription
    listings_df['resolved_town'] = resolved_town
    listings_df['s7_warnings'] = s7_warnings
    return listings_df

def roll_up_catalog_text(listings_df):
    """Populate listings_df['rolled_catalog_text'].

    For independent listings (parent_idx is None): own clean_text plus all
    child clean_text lines below it. For child listings (parent_idx set):
    parent clean_text plus every sibling clean_text for that parent.
    Duplicate lines are collapsed by normalized-whitespace key, preserving
    the first original spelling and spacing.

    Must run after resolve_relationships(). Mutates listings_df in place
    and returns it.
    """
    def _txt(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ''
        return str(v)

    def _dedupe_catalog_lines(lines):
        """Return first-seen catalog lines after conservative whitespace keys."""
        out = []
        seen = set()
        for line in lines:
            key = re.sub(r"\s+", " ", _txt(line).strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(_txt(line))
        return out

    n = len(listings_df)

    # Pass 1: collect every child's clean_text per parent, in catalog order.
    children_by_parent = {}  # parent_idx label -> list of clean_text
    for pos in range(n):
        row = listings_df.iloc[pos]
        pidx = row.get('parent_idx')
        if pidx is None or (isinstance(pidx, float) and pd.isna(pidx)):
            continue
        children_by_parent.setdefault(pidx, []).append(_txt(row.get('clean_text')))

    # Pass 2: emit rolled text. Duplicate catalog text is display/provenance
    # noise, not evidence that source rows or image-bearing records are
    # duplicates. De-dupe only this rolled text, after the family is assembled.
    rolled = [None] * n
    for pos in range(n):
        row = listings_df.iloc[pos]
        own = _txt(row.get('clean_text'))
        pidx = row.get('parent_idx')
        if pidx is None or (isinstance(pidx, float) and pd.isna(pidx)):
            own_label = listings_df.index[pos]
            kids = children_by_parent.get(own_label, [])
            rolled[pos] = '\n'.join(_dedupe_catalog_lines([own] + list(kids)))
        else:
            parent_text = _txt(listings_df.loc[pidx, 'clean_text'])
            sibs = children_by_parent.get(pidx, [])
            rolled[pos] = '\n'.join(_dedupe_catalog_lines([parent_text] + list(sibs)))

    listings_df['rolled_catalog_text'] = rolled
    return listings_df

def _norm_for_alias(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    return re.sub(r"[.,\s]+$", "", str(s).strip()).upper() or None

def _is_abbrev_of(short, long):
    """Conservative: short shares first letter with long, is at least 3
    characters, at most half long's length, and short's letters appear
    as a subsequence in long. Catches FREDG -> FREDERICKSBURG, CULPE ->
    CULPEPER, CHS -> CHARLES; rejects CHARLE -> CHARLESTON (length
    ratio too high)."""
    if not short or not long or short[0] != long[0]:
        return False
    if len(short) < 3 or len(short) * 2 > len(long):
        return False
    j = 0
    for ch in long:
        if j < len(short) and ch == short[j]:
            j += 1
    return j == len(short)

OR_ALIAS_RE = re.compile(r"^\s*(.+?)\s+OR\s+(.+?)\s*$", re.IGNORECASE)

TOWN_HEADING_RE = re.compile(r"^[A-Za-z][A-Za-z .\-]{2,40}$")
