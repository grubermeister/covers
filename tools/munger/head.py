import re


import pandas as pd

from .fields.dates import MONTHS_PAT
_MS_VALUE_TOKEN = (
    r"(?:"
    r"\d[\d,]*(?:\.\d+)?-"
    r"|"
    r"\d[\d,]*(?:\.\d+)?"
    r"|---?"
    r")"
    r"(?:[/,-](?:\d[\d,]*(?:\.\d+)?|---?))*"
)
MS_TAIL_AT_END = re.compile(
    # Trailing value: digits, commas, decimal points, slashes, dashes
    # (slash-tiered values; `100/--`, `--/15.00`, `1500.00`, `--` all
    # match). A final catalog period is allowed but not captured. Requires
    # whitespace before so embedded year ranges like `1835-39` are not
    # mistaken for a value.
    r"\s+(" + _MS_VALUE_TOKEN + r")(?:\.)?\s*$"
)

MS_DASH_AT_END = re.compile(r"[\s/]*(--|---)\s*$")

_MONTH_DATE_UNIT = (
    r"\*?(?:"
    r"(?:"
    + MONTHS_PAT
    + r")\.?\s+\d{1,2}\s*,\s*\d{4}"
    r"|(?:"
    + MONTHS_PAT
    + r")\.?\s+\d{4}"
    r"|(?:0?[1-9]|1[0-2])\s+\d{4}"
    r"|(?:c\.?\s*)?\d{3,4}(?:'?[Ss])?(?:-\d{1,4}(?:'?[Ss])?)?"
    r")"
)

MS_DATE_AT_END = re.compile(
    r"[\s,]+"
    r"("
    + _MONTH_DATE_UNIT +
    r"(?:\s*,\s*" + _MONTH_DATE_UNIT + r")*"
    r")"
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
NO_TOWN_MARKING_ANNOTATION_RE = re.compile(
    r'^\s*no\s+town\s+mark(?:ing)?\s*$',
    re.IGNORECASE,
)
NO_TOWN_MARKING_INSCRIPTION = '(No town marking)'
HEAD_CONTROL_ANNOTATION_RE = re.compile(
    r'^\s*(?:[EL]|no\s+town\s+mark(?:ing)?)\s*$',
    re.IGNORECASE,
)
HEAD_NUMERIC_ANNOTATION_RE = re.compile(r'^\s*\d+\s*$')
HEAD_NOTE_KEYWORD_RE = re.compile(
    r'\b(?:'
    r'italic|italics|italicized|'
    r'letter|letters|type|typeface|'
    r'backstamp|cds|fleuron|ornament|ornamental|'
    r'dot|dots|dash|dashes|period|comma|'
    r'without|with|above|below|between|around|'
    r'high|low|large|small|larger|smaller|tiny|'
    r'thick|thin|slanting|slanted|reversed|inverted|hollow|outline|'
    r'serif|serifs|seriffed|gothic|bold|block'
    r')\b',
    re.IGNORECASE,
)
HEAD_ITALIC_NOTE_RE = re.compile(r'\b(?:italic|italics|italicized)\b',
                                 re.IGNORECASE)
HEAD_NOT_ITALIC_NOTE_RE = re.compile(
    r'\b(?:not|without)\s+(?:in\s+)?(?:italic|italics|italicized)\b',
    re.IGNORECASE,
)
HEAD_THICK_NOTE_RE = re.compile(r'\bthick\b', re.IGNORECASE)
HEAD_THIN_NOTE_RE = re.compile(r'\bthin\b', re.IGNORECASE)
HEAD_LETTERING_NOTE_RE = re.compile(
    r'\b(?:'
    r'italic|italics|italicized|'
    r'sans\s+serif|sans\s+serifs|serif|serifs|seriffed|'
    r'letter|letters|type|typeface|script|slanting|slanted|'
    r'large|larger|small|smaller|tiny|thick|thin|bold|block|gothic|'
    r'hollow|outline'
    r')\b',
    re.IGNORECASE,
)

REL_INDICATOR_RE = re.compile(
    r'^(?:'
    r'Same'
    r'|[(\[{][LE][)\]}]\*?'
    r')'
)


def normalize_head_annotation_note(text):
    note = re.sub(r'\s+', ' ', str(text or '').strip())
    if re.fullmatch(r'backstamp', note, flags=re.IGNORECASE):
        return 'Backstamp'
    if re.fullmatch(r'no\s+town\s+cds', note, flags=re.IGNORECASE):
        return 'No town cds'
    return note


def _is_date_annotation(text):
    return bool(re.fullmatch(
        r'\s*' + _MONTH_DATE_UNIT
        + r'(?:\s*,\s*' + _MONTH_DATE_UNIT + r')*\s*',
        str(text or ''),
        flags=re.IGNORECASE,
    ))


def _is_head_note_annotation(head, match, allow_leading_note=False):
    note = normalize_head_annotation_note(match.group(1))
    if not note:
        return False
    if HEAD_CONTROL_ANNOTATION_RE.fullmatch(note):
        return False
    if HEAD_NUMERIC_ANNOTATION_RE.fullmatch(note):
        return False
    if _is_date_annotation(note):
        return False

    before = head[:match.start()]
    if not before.strip() and not allow_leading_note:
        return False

    prev = head[match.start() - 1] if match.start() > 0 else ''
    if prev.isspace() or not prev.isalnum():
        return True
    return bool(HEAD_NOTE_KEYWORD_RE.search(note))


def head_note_lettering_name(notes):
    for note in notes or []:
        text = str(note or '')
        if HEAD_NOT_ITALIC_NOTE_RE.search(text):
            continue
        if HEAD_ITALIC_NOTE_RE.search(text):
            return 'Italic'
        if HEAD_THICK_NOTE_RE.search(text):
            return 'Thick'
        if HEAD_THIN_NOTE_RE.search(text):
            return 'Thin'
    return None


def head_note_has_lettering_note(notes):
    return any(
        HEAD_LETTERING_NOTE_RE.search(str(note or ''))
        for note in notes or []
    )

def parse_head(row):
    """Extract structured components from seg_head."""
    head = str(row['seg_head']) if pd.notna(row['seg_head']) else ''

    # 1. Catalog ownership marker (leading *)
    head_without_indent = head.lstrip()
    has_leading_star = head_without_indent.startswith('*')
    if has_leading_star:
        head = head_without_indent[1:]

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

    # 4. Annotations: all (...) groups remaining in head. Parentheticals
    # after the town text are catalog notes, not inscription text; keep a
    # filtered note list for desc/lettering handling while preserving the
    # raw annotation list for diagnostics.
    annotation_matches = list(PAREN_GROUP_RE.finditer(head))
    annotations = [m.group(1) for m in annotation_matches]
    head_annotation_notes = [
        normalize_head_annotation_note(m.group(1))
        for m in annotation_matches
        if _is_head_note_annotation(head, m, rel_type is not None)
    ]

    # 5. Name body: head text with annotation parens removed, stripped
    name_body = PAREN_GROUP_RE.sub('', head).strip()
    if not name_body and any(
        NO_TOWN_MARKING_ANNOTATION_RE.match(a) for a in annotations
    ):
        name_body = NO_TOWN_MARKING_INSCRIPTION

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
    head_date_text = None
    if name_body:
        dates = []
        while True:
            m_date = MS_DATE_AT_END.search(name_body)
            if m_date:
                raw = m_date.group(1).strip()
                if raw and raw != '--':
                    dates.insert(0, raw)
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
        if dates:
            head_date_text = ','.join(dates)

    # 5c. Drop residues with no real town text (e.g. a row that was just a
    #     bare year like "1849"): they would otherwise become a PostOffice
    #     name made only of digits and fail downstream normalization. A
    #     None town routes the row to the UNKNOWN post office instead.
    if name_body and not re.search(r'[A-Za-z]{2,}', name_body):
        name_body = None

    name_body = name_body if name_body else None

    return pd.Series({
        'head_has_leading_star': has_leading_star,
        'head_rel_type': rel_type,
        'head_name_body': name_body,
        'head_annotations': annotations,
        'head_annotation_notes': head_annotation_notes,
        'head_lettering_name': head_note_lettering_name(head_annotation_notes),
        'head_has_lettering_note': head_note_has_lettering_note(
            head_annotation_notes,
        ),
        'head_date_text': head_date_text,
    })
