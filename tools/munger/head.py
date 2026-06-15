import re


import pandas as pd
MS_TAIL_AT_END = re.compile(
    # Trailing value: digits, dots, commas, slashes, dashes (slash-tiered
    # values; `100/--`, `--/15.00`, `1500.00`, `--` all match). Requires
    # whitespace before so embedded year ranges like `1835-39` are not
    # mistaken for a value.
    r"\s+([0-9./,-]+|--)\s*$"
)

MS_DASH_AT_END = re.compile(r"[\s/]*(--|---)\s*$")

MS_DATE_AT_END = re.compile(
    r"[\s,]+"
    r"(\*?(?:c\.)?\d{4}(?:-\d{1,4})?(?:'?[Ss])?)"
    r"\s*$"
)

MS_SEP_AT_END = re.compile(r"[\s,/]+$")

def parse_manuscript_row(row):
    """Parse a Manuscript-section LISTING row into seg_head + seg_tail + ms_date_text."""
    text = str(row['clean_text']).strip()

    # 1. Pull off the trailing value as one whole token (handles slash
    #    tiers like `100/--` and `--/15.00`). The mandatory leading
    #    whitespace prevents matching an embedded year range.
    m = MS_TAIL_AT_END.search(text)
    if m:
        seg_tail = m.group(1)
        body = text[:m.start()].rstrip()
    else:
        seg_tail = None
        body = text

    # 2. Iteratively peel trailing date tokens, `--` placeholders, and
    #    standalone separators until the body stabilizes.
    dates = []
    while True:
        m_date = MS_DATE_AT_END.search(body)
        if m_date:
            raw = m_date.group(1).strip()
            if raw and raw != '--':
                dates.insert(0, raw)
            body = body[:m_date.start()].rstrip()
            continue
        m_dash = MS_DASH_AT_END.search(body)
        if m_dash:
            body = body[:m_dash.start()].rstrip()
            continue
        m_sep = MS_SEP_AT_END.search(body)
        if m_sep:
            body = body[:m_sep.start()].rstrip()
            continue
        break

    ms_date_text = ','.join(dates) if dates else None

    return pd.Series({
        'seg_head': body if body else None,
        'seg_paren': None,
        'seg_tail': seg_tail,
        'seg_error': None,
        'ms_date_text': ms_date_text,
    })

PAREN_GROUP_RE = re.compile(r'\(([^)]*)\)')

REL_INDICATOR_RE = re.compile(
    r'^(?:'
    r'Same'
    r'|[(\[{][LE][)\]}]\*?'
    r')'
)

def parse_head(row):
    """Extract structured components from seg_head."""
    head = str(row['seg_head']) if pd.notna(row['seg_head']) else ''

    # 1. First-of-town marker (leading *)
    first_of_town = head.startswith('*')
    if first_of_town:
        head = head[1:]

    # 2. Plus prefix (rare; allowed by S1 regex but uncommon)
    plus_prefix = head.startswith('+')
    if plus_prefix:
        head = head[1:]

    # 3. Relationship indicator
    rel_type = None
    if row['s1_relationship']:
        m = REL_INDICATOR_RE.match(head)
        if m:
            rel_type = m.group(0)
            head = head[m.end():]

    # 4. Annotations: all (...) groups remaining in head
    annotations = PAREN_GROUP_RE.findall(head)

    # 5. Name body: head text with annotation parens removed, stripped
    name_body = PAREN_GROUP_RE.sub('', head).strip()

    # 5b. Peel a bare trailing date field off town-table headings.
    #     Town-listing rows print as "NAME .... YEAR(S) .... VALUE" with
    #     dot-leader separators (ascc_page_extract compresses the leaders
    #     to "..."). strip_dot_leaders flattens those to spaces before
    #     segmentation, so once segment_entry removes the trailing value
    #     the head arrives here as e.g. "Accomack C.H 1835",
    #     "Aquia 1811,1849-55", or "Arbor Hill 1850's" -- the year is
    #     glued to the town name. parse_manuscript_row already peels this
    #     for Manuscript-flagged rows; reuse its exact loop (date, then
    #     dash placeholder, then trailing separators) so the normal
    #     (non-manuscript) path normalizes identically. The dash/sep peels
    #     also clear a "--/" residue left on the head when a slash-tiered
    #     value like "--/15.00" is only partly consumed by segment_entry,
    #     which otherwise hides the date from MS_DATE_AT_END's end-anchor.
    #     No-op for rows whose dates were parenthesized annotations (those
    #     were removed in step 5), so manuscript-section parsing is
    #     unchanged.
    if name_body:
        while True:
            m_date = MS_DATE_AT_END.search(name_body)
            if m_date:
                name_body = name_body[:m_date.start()].rstrip()
                continue
            m_dash = MS_DASH_AT_END.search(name_body)
            if m_dash:
                name_body = name_body[:m_dash.start()].rstrip()
                continue
            m_sep = MS_SEP_AT_END.search(name_body)
            if m_sep:
                name_body = name_body[:m_sep.start()].rstrip()
                continue
            break

    # 5c. Drop residues with no real town text (e.g. a row that was just a
    #     bare year like "1849"): they would otherwise become a PostOffice
    #     name made only of digits and fail downstream normalization. A
    #     None town routes the row to the UNKNOWN post office instead.
    if name_body and not re.search(r'[A-Za-z]{2,}', name_body):
        name_body = None

    name_body = name_body if name_body else None

    return pd.Series({
        'head_first_of_town': first_of_town,
        'head_rel_type': rel_type,
        'head_name_body': name_body,
        'head_annotations': annotations,
    })
