#!/usr/bin/env python3
"""ascc_data_munger -- ASCC catalog CSV -> Django-shape import bundle.

Hoistable function and constant definitions live at module scope;
functions or constants that depend on runtime pipeline state remain
inside main(). AUDIT_TS honors the ASCC_AUDIT_TS env var when set, for
diffable test runs.
"""
import argparse
import hashlib


import pandas as pd
import re
import os
import shutil
import mimetypes
from pathlib import Path
from PIL import Image as PILImage

from munger.assembly import LETTERING_SEEDS, SHAPE_SEEDS, _nkey, confidence_level, dt_date, resolve_effective_shape, resolve_shape_name
from munger.classify import RELATIONSHIP_PATTERN, TRAILING_VALUE_PATTERN, _csv_manuscript_truthy, classify_entry, detect_cross_reference, detect_fragment, detect_structural_anatomy
from munger.export import AUDIT_TAIL, AUDIT_USER_ID, INT_COLS, _by_listing, _cast_int_columns, _resolve_int_fk, _src_row_by
from munger.fields import _split_ms_date_token, classify_all_fields, classify_paren_field, subparse_fields, triage_other_field
from munger.fields.colors import parse_color_field
from munger.fields.dates import parse_date_field
from munger.fields.rates import RATE_BRACKET_RE, parse_rate_token, split_rate_tokens
from munger.fields.sizes import parse_size_field
from munger.head import parse_head, parse_manuscript_row
from munger.images import (
    MEDIA_ROOT,
    catalog_region_abbrev,
    image_filename,
    region_media_slug,
)
from munger.io import (
    OPTIONAL_COLS,
    REQUIRED_COLS,
    assign_section_regions,
    process_meta_rows,
)
from munger.rate_assembly import BRACKET_DIM_RE, BRACKET_SHAPE_MAP, _date_cls, _tm_codes_by_listing, parse_rate_amount
from munger.relationships import OR_ALIAS_RE, TOWN_HEADING_RE, _is_abbrev_of, _norm_for_alias, resolve_relationships, roll_up_catalog_text
from munger.segment import classify_entry_form, decompose_tail, segment_entry, split_paren_fields, split_valuation_tiers
from munger.text_utils import strip_dot_leaders

TOOLS_DIR = Path(__file__).resolve().parent
WIP_DIR = TOOLS_DIR / "wip"

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default=WIP_DIR / "cache" / "VA_ASCC_CTLG.csv")
    ap.add_argument("--input-dir", default=WIP_DIR / "in")
    ap.add_argument("--out-dir", default=WIP_DIR / "out")
    ap.add_argument("--reference-work-code", default="ASCC1")
    args = ap.parse_args(argv)

    INPUT_CSV = Path(args.input)
    INPUT_DIR = Path(args.input_dir)
    OUT_DIR = Path(args.out_dir)


    # ======================================================================
    # 0. Setup
    # ======================================================================
    # INPUT_CSV / INPUT_DIR / OUT_DIR supplied by main() argparse.
    REGION_ABBREV = catalog_region_abbrev(INPUT_CSV)
    REFERENCE_WORK_CODE = str(args.reference_work_code).strip()
    _rw_seed = pd.read_csv(os.path.join(INPUT_DIR, 'reference_works.csv'))
    _rw_match = _rw_seed[
        _rw_seed['code'].astype(str).str.strip() == REFERENCE_WORK_CODE
    ]
    if len(_rw_match) != 1:
        raise ValueError(
            f"reference_works.csv must contain exactly 1 row with "
            f"code={REFERENCE_WORK_CODE!r} (matched {len(_rw_match)})."
        )
    RW_ID   = int(_rw_match.iloc[0]['id'])
    RW_CODE = str(_rw_match.iloc[0]['code']).strip()
    _region_seed = pd.read_csv(os.path.join(INPUT_DIR, 'regions.csv'))
    _match = _region_seed[_region_seed['abbrev'].astype(str).str.upper() == REGION_ABBREV]
    if len(_match) != 1:
        raise ValueError(
            f"REGION_ABBREV={REGION_ABBREV!r} must match exactly 1 row in regions.csv "
            f"(matched {len(_match)}). Adjust INPUT_CSV or regions.csv."
        )
    REGION_ID = int(_match.iloc[0]['id'])
    print(f"rw_code={RW_CODE} (id={RW_ID})  region={REGION_ABBREV} (id={REGION_ID})")
    df = pd.read_csv(INPUT_CSV)
    print(f'Loaded {len(df)} rows from {INPUT_CSV}')
    print(f'Columns: {list(df.columns)}')
    missing_required = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing_required:
        raise ValueError(
            f'Required columns missing from {INPUT_CSV}: {missing_required}'
        )
    df['section_region_id'] = assign_section_regions(df, _region_seed, REGION_ID)
    meta_df = df[df['Type'] == 'META'].reset_index(drop=True)
    listings_df = df[df['Type'] == 'LISTING'].reset_index(drop=True)
    process_meta_rows(meta_df)
    df = listings_df
    print(f'  META rows:    {len(meta_df)}')
    print(f'  LISTING rows: {len(df)} (downstream)')
    present_optional = [c for c in OPTIONAL_COLS if c in df.columns]
    absent_optional = [c for c in OPTIONAL_COLS if c not in df.columns]
    print(f'Optional columns present: {present_optional}')
    if absent_optional:
        print(f'Optional columns absent (fallback behavior): {absent_optional}')

    # ======================================================================
    # 1. Preprocessing
    # ======================================================================
    df['clean_text'] = df['Listing'].apply(strip_dot_leaders)
    print('Sample cleaned entries:')
    for t in df['clean_text'].head(5):
        print(f'  {t}')
    df['is_manuscript_section'] = df.apply(_csv_manuscript_truthy, axis=1)
    print(f'Manuscript-section rows detected: '
          f'{int(df["is_manuscript_section"].sum())} / {len(df)}')

    # ======================================================================
    # Signal 1: Relationship Indicator Prefix
    # ======================================================================
    df['s1_relationship'] = df['clean_text'].apply(
        lambda t: bool(RELATIONSHIP_PATTERN.match(t))
    )
    print(f'Signal 1 hits: {df["s1_relationship"].sum()}')
    print()
    print('Examples:')
    for t in df.loc[df['s1_relationship'], 'clean_text'].head(8):
        print(f'  {t[:100]}')

    # ======================================================================
    # Signal 2: Cross-Reference
    # ======================================================================
    df['s2_cross_ref'] = df['clean_text'].apply(detect_cross_reference)
    print(f'Signal 2 hits: {df["s2_cross_ref"].sum()}')
    print()
    for t in df.loc[df['s2_cross_ref'], 'clean_text']:
        print(f'  {t[:120]}')

    # Parenthetical annotations promoted to the marking's desc. Each pattern
    # is matched case-insensitively against clean_text; when present, the
    # mapped desc-line is appended (newline-joined) to the townmark's desc.
    _DESC_PAREN_ANNOTATIONS = [
        (re.compile(r'\(backstamp\)', re.IGNORECASE),    'Backstamp'),
        (re.compile(r'\(no town cds\)', re.IGNORECASE),  'No town cds'),
    ]
    def _paren_annotation_lines(text):
        s = str(text or '')
        return [label for pat, label in _DESC_PAREN_ANNOTATIONS if pat.search(s)]
    df['paren_annotations_desc'] = df['clean_text'].apply(_paren_annotation_lines)
    # Keep has_backstamp as a separate column for any callers that depend on
    # the specific flag (none today, but cheaper than searching for them).
    df['has_backstamp'] = df['paren_annotations_desc'].apply(lambda xs: 'Backstamp' in xs)
    # See-clause handling for cross-references. The user wants these preserved
    # as real markings (not dropped), with the See-clause captured in desc.
    # Strategy: capture the see-clause text for desc, strip it out of
    # clean_text so the rest of the pipeline parses the row as a normal
    # listing, and clear s2_cross_ref so classify_entry routes it as such.
    # Without this, head parsing pollutes inscription/town with the See
    # fragment ("FREDERICKSBURGSee Colonial listing" -> creates a new bogus
    # post_office) and "(L) See State" rows lose their relationship marker.
    _SEE_PAREN_RE = re.compile(r'\(\s*(See\b[^)]*)\)', re.IGNORECASE)
    # Bare-See regex: stop the lazy capture before '--' (the no-valuation tail
    # marker), '...', ';', or end-of-string. The '--' alternative is critical:
    # otherwise the lazy match extends through it (no other terminator before
    # $) and the tail marker gets eaten, breaking later tail decomposition.
    _SEE_BARE_RE  = re.compile(r'\b(See\b[^;)]*?)(?=\s*(?:--|\.{2,}|;|$))', re.IGNORECASE)
    _MULTI_WS_RE  = re.compile(r'\s{2,}')
    def _extract_and_strip_see(text):
        s = str(text or '')
        clause = None
        m = _SEE_PAREN_RE.search(s)
        if m:
            clause = m.group(1).strip()
            s = _SEE_PAREN_RE.sub('', s, count=1)
        else:
            m = _SEE_BARE_RE.search(s)
            if m:
                clause = m.group(1).strip()
                s = _SEE_BARE_RE.sub('', s, count=1)
        s = _MULTI_WS_RE.sub(' ', s).strip()
        return clause, s
    _see_clauses = []
    _cleaned_texts = []
    # Bare-rel-marker pattern: '(L) --', '(E) --', etc. with nothing else.
    # After see-clause stripping, these rows have no second paren for
    # segment_entry's simple_paren form to consume, so the rel marker ends up
    # in seg_paren instead of seg_head -- head parsing then misses the rel
    # type and the row gets treated as an orphan independent entry. Pad with
    # '(cross-ref)' to give segmentation a sacrificial last-paren group;
    # head parsing then correctly extracts (L)/(E) as the rel indicator and
    # resolve_relationships inherits inscription/town from the parent.
    _BARE_REL_RE = re.compile(r'^\s*[(\[{][LE][)\]}]\s*--\s*$', re.IGNORECASE)
    for _, _row in df.iterrows():
        if _row['s2_cross_ref']:
            _c, _s = _extract_and_strip_see(_row['clean_text'])
            _see_clauses.append(_c)
            if _s:
                if _BARE_REL_RE.match(_s):
                    _s = _s.replace('--', '(cross-ref) --', 1)
                _cleaned_texts.append(_s)
            else:
                _cleaned_texts.append(_row['clean_text'])
        else:
            _see_clauses.append(None)
            _cleaned_texts.append(_row['clean_text'])
    df['see_clause'] = _see_clauses
    df['clean_text'] = _cleaned_texts
    # Re-route cleaned cross-refs through normal classification: their text no
    # longer matches detect_cross_reference, so flip the signal to match.
    df.loc[df['s2_cross_ref'] & df['see_clause'].notna(), 's2_cross_ref'] = False
    print(f'See-clause extracted: {df["see_clause"].notna().sum()} rows (re-classified as listings)')

    # ======================================================================
    # Signal 3: Fragment Detection
    # ======================================================================
    df['s3_fragment'] = df['clean_text'].apply(detect_fragment)
    print(f'Signal 3 hits: {df["s3_fragment"].sum()}')
    print()
    for t in df.loc[df['s3_fragment'], 'clean_text']:
        print(f'  {repr(t[:120])}')

    # ======================================================================
    # Signal 4: Trailing Valuation
    # ======================================================================
    df['s4_trailing_value'] = df['clean_text'].apply(
        lambda t: bool(TRAILING_VALUE_PATTERN.search(t))
    )
    print(f'Signal 4 hits: {df["s4_trailing_value"].sum()} / {len(df)}')
    print()
    misses = df[~df['s4_trailing_value']]
    if len(misses):
        print('Entries WITHOUT trailing value:')
        for t in misses['clean_text']:
            print(f'  {repr(t[:120])}')
    else:
        print('All entries have a trailing value.')

    # ======================================================================
    # Signal 5: Core Structural Anatomy
    # ======================================================================
    anatomy = df['clean_text'].apply(detect_structural_anatomy).apply(pd.Series)
    df['s5_semicolon_paren'] = anatomy['semicolon_paren']
    df['s5_four_digit_year'] = anatomy['four_digit_year']
    df['s5_decade_ref'] = anatomy['decade_ref']
    df['s5_c_year'] = anatomy['c_year']
    df['s5_anatomy'] = anatomy['any']
    print(f'Signal 5 hits: {df["s5_anatomy"].sum()} / {len(df)}')
    print()
    print('Sub-signal breakdown:')
    print(f'  Semicolon-parenthetical: {df["s5_semicolon_paren"].sum()}')
    print(f'  Four-digit year:         {df["s5_four_digit_year"].sum()}')
    print(f'  Decade reference:        {df["s5_decade_ref"].sum()}')
    print(f'  c-prefixed year:         {df["s5_c_year"].sum()}')
    print()
    misses = df[~df['s5_anatomy']]
    if len(misses):
        print(f'Entries WITHOUT structural anatomy ({len(misses)}):')
        for t in misses['clean_text']:
            print(f'  {repr(t[:120])}')
    else:
        print('All entries have structural anatomy.')

    # ======================================================================
    # 3. Classification
    # ======================================================================
    classifications = df.apply(classify_entry, axis=1, result_type='expand')
    df['classification'] = classifications[0]
    df['confidence'] = classifications[1]
    df['reason'] = classifications[2]
    print('Classification results:')
    print(df['classification'].value_counts())
    print()
    print('By reason:')
    print(df.groupby(['classification', 'confidence', 'reason']).size().reset_index(name='count').to_string(index=False))
    review = df[(df['confidence'] == 'low') | (df['classification'] != 'listing')].copy()

    # ======================================================================
    # 4. Review: Low-Confidence and Non-Listings
    # ======================================================================
    print(f'Entries requiring review: {len(review)} / {len(df)}')
    print()
    for _, row in review.iterrows():
        print(f'[{row["classification"]}] ({row["confidence"]}, {row["reason"]})')
        print(f'  {row["clean_text"][:140]}')
        print()
    report_cols = [
        'clean_text',
        's1_relationship',
        's2_cross_ref',
        's3_fragment',
        's4_trailing_value',
        's5_anatomy',
        'classification',
        'confidence',
        'reason',
    ]

    # ======================================================================
    # 5. Signal Summary
    # ======================================================================
    report = df[report_cols].copy()
    report['clean_text'] = report['clean_text'].str[:80]
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_colwidth', 82)
    pd.set_option('display.width', 200)
    report
    total = len(df)

    # ======================================================================
    # 6. Observations
    # ======================================================================
    print(f'Total entries: {total}')
    print()
    print('Signal coverage:')
    print(f'  S1 (relationship indicator): {df["s1_relationship"].sum()} ({df["s1_relationship"].sum()/total*100:.1f}%)')
    print(f'  S2 (cross-reference):        {df["s2_cross_ref"].sum()} ({df["s2_cross_ref"].sum()/total*100:.1f}%)')
    print(f'  S3 (fragment):               {df["s3_fragment"].sum()} ({df["s3_fragment"].sum()/total*100:.1f}%)')
    print(f'  S4 (trailing value):         {df["s4_trailing_value"].sum()} ({df["s4_trailing_value"].sum()/total*100:.1f}%)')
    print(f'  S5 (structural anatomy):     {df["s5_anatomy"].sum()} ({df["s5_anatomy"].sum()/total*100:.1f}%)')
    print()
    print('Classification summary:')
    for cls in ['listing', 'cross_reference', 'non_entry']:
        subset = df[df['classification'] == cls]
        n = len(subset)
        low = (subset['confidence'] == 'low').sum()
        if n > 0:
            conf_note = f' ({low} low-confidence)' if low else ''
            print(f'  {cls}: {n} ({n/total*100:.1f}%){conf_note}')

    # ======================================================================
    # 2.1 Entry Form Classification
    # ======================================================================
    listings = df[df['classification'] == 'listing'].copy()
    print(f'Segmenting {len(listings)} listings')
    listings['entry_form'] = listings.apply(classify_entry_form, axis=1)
    print()
    print('Entry form distribution:')
    for form, count in listings['entry_form'].value_counts().items():
        print(f'  {form}: {count} ({count/len(listings)*100:.1f}%)')

    # ======================================================================
    # 2.2 Segmentation
    # ======================================================================
    segments = listings.apply(segment_entry, axis=1)
    listings = pd.concat([listings, segments], axis=1)
    non_ms_errors = listings[
        listings['seg_error'].notna() & (listings['entry_form'] != 'manuscript')
    ]
    print(f'Segmentation errors: {len(non_ms_errors)} / {len(listings)}')
    if len(non_ms_errors):
        for _, row in non_ms_errors.iterrows():
            print(f'  [{row["entry_form"]}] {row["seg_error"]}')
            print(f'    {row["clean_text"][:140]}')
        print()
    print(f'Successful segmentations (excluding manuscript overlay): '
          f'{len(listings) - len(non_ms_errors) - (listings["entry_form"] == "manuscript").sum()}')
    errors = non_ms_errors  # alias for downstream summary cells
    ok = listings[listings['seg_error'].isna() & (listings['entry_form'] != 'manuscript')]
    if len(non_ms_errors):
        detail = non_ms_errors[['clean_text', 'entry_form', 'seg_error']].head(30).copy()
        detail.insert(0, 'csv_line', detail.index + 2)
        raise AssertionError(
            f'Silent drop: {len(non_ms_errors)} listing(s) failed segmentation. '
            f'These rows would be carried forward with empty seg_head/seg_paren/seg_tail.\n'
            f'First offenders (csv_line = line number in the input CSV):\n{detail.to_string(index=False)}'
        )
    listings['ms_date_text'] = pd.Series([None] * len(listings), index=listings.index, dtype='object')
    ms_mask = listings['is_manuscript_section'].fillna(False)
    if ms_mask.any():
        ms_parsed = listings[ms_mask].apply(parse_manuscript_row, axis=1)
        # Cast target columns to object dtype before scatter-assign; the
        # placeholder None values from segment_entry made them float64,
        # which cannot accept the string values produced by the overlay.
        for col in ms_parsed.columns:
            if col in listings.columns:
                listings[col] = listings[col].astype(object)
            listings.loc[ms_mask, col] = ms_parsed[col]
    n_ms = int(ms_mask.sum())
    n_with_date = int(listings.loc[ms_mask, 'ms_date_text'].notna().sum()) if n_ms else 0
    n_with_tail = int(listings.loc[ms_mask, 'seg_tail'].notna().sum()) if n_ms else 0
    print(f'Manuscript overlay: {n_ms} rows; {n_with_date} captured a date; '
          f'{n_with_tail} captured a trailing value.')
    if n_ms:
        print()
        print('Sample manuscript rows after overlay:')
        sample = listings[ms_mask][['clean_text', 'seg_head', 'ms_date_text', 'seg_tail']].head(8)
        print(sample.to_string(index=False))
    ok = listings[listings['seg_error'].isna()]

    # ======================================================================
    # 2.3 Validation
    # ======================================================================
    print('=== SEMICOLON_PAREN examples ===')
    sp = ok[ok['entry_form'] == 'semicolon_paren']
    print(f'Count: {len(sp)}')
    for _, row in sp.head(8).iterrows():
        print(f'  raw:   {row["clean_text"][:100]}')
        print(f'  head:  {row["seg_head"]}')
        print(f'  paren: {row["seg_paren"]}')
        print(f'  tail:  {row["seg_tail"]}')
        print()
    print('=== SIMPLE_PAREN examples ===')
    simp = ok[ok['entry_form'] == 'simple_paren']
    print(f'Count: {len(simp)}')
    for _, row in simp.head(8).iterrows():
        print(f'  raw:   {row["clean_text"][:100]}')
        print(f'  head:  {row["seg_head"]}')
        print(f'  paren: {row["seg_paren"]}')
        print(f'  tail:  {row["seg_tail"]}')
        print()
    print('=== NO_PAREN examples ===')
    np_ = ok[ok['entry_form'] == 'no_paren']
    print(f'Count: {len(np_)}')
    for _, row in np_.head(8).iterrows():
        print(f'  raw:   {row["clean_text"][:100]}')
        print(f'  head:  {row["seg_head"]}')
        print(f'  tail:  {row["seg_tail"]}')
        print()
    ok = listings[listings['seg_error'].isna()]

    # ======================================================================
    # Sanity checks
    # ======================================================================
    empty_tail = ok[ok['seg_tail'].isna() | (ok['seg_tail'] == '')]
    print(f'Empty seg_tail: {len(empty_tail)}')
    if len(empty_tail):
        for _, row in empty_tail.iterrows():
            print(f'  [{row["entry_form"]}] {row["clean_text"][:120]}')
        print()
    non_rel = ok[~ok['s1_relationship']]
    empty_head = non_rel[non_rel['seg_head'].isna() | (non_rel['seg_head'] == '')]
    print(f'Empty seg_head (non-relationship): {len(empty_head)}')
    if len(empty_head):
        for _, row in empty_head.iterrows():
            print(f'  [{row["entry_form"]}] {row["clean_text"][:120]}')
        print()
    paren_forms = ok[ok['entry_form'].isin(['semicolon_paren', 'simple_paren'])]
    empty_paren = paren_forms[paren_forms['seg_paren'].isna() | (paren_forms['seg_paren'] == '')]
    print(f'Empty seg_paren (paren forms): {len(empty_paren)}')
    if len(empty_paren):
        for _, row in empty_paren.iterrows():
            print(f'  [{row["entry_form"]}] {row["clean_text"][:120]}')
        print()
    print(f'\n=== ALL simple_paren entries ({len(simp)}) ===')
    for _, row in ok[ok['entry_form'] == 'simple_paren'].iterrows():
        print(f'  head={row["seg_head"]!r}  paren={row["seg_paren"]!r}  tail={row["seg_tail"]!r}')
    total = len(listings)

    # ======================================================================
    # 2.4 Step 2 Summary
    # ======================================================================
    ok_count = len(ok)
    err_count = len(errors)
    print(f'Step 2: Structural Segmentation')
    print(f'  Input listings: {total}')
    print(f'  Successful:     {ok_count} ({ok_count/total*100:.1f}%)')
    print(f'  Errors:         {err_count} ({err_count/total*100:.1f}%)')
    print()
    print(f'Entry form breakdown:')
    for form in ['semicolon_paren', 'simple_paren', 'no_paren']:
        n = (listings['entry_form'] == form).sum()
        print(f'  {form}: {n} ({n/total*100:.1f}%)')

    # ======================================================================
    # 3.1 Paren Field Splitting
    # ======================================================================
    listings['paren_fields'] = listings.apply(split_paren_fields, axis=1)
    listings['paren_field_count'] = listings['paren_fields'].apply(len)
    print('Paren field count distribution:')
    fc = listings['paren_field_count'].value_counts().sort_index()
    for n, count in fc.items():
        print(f'  {n} fields: {count} ({count/len(listings)*100:.1f}%)')
    print()
    print(f'Entries with 0 fields (no_paren): {(listings["paren_field_count"] == 0).sum()}')
    for n in sorted(listings['paren_field_count'].unique()):
        subset = listings[listings['paren_field_count'] == n]
        print(f'=== {n} FIELDS ({len(subset)} entries) ===')
        for _, row in subset.head(5).iterrows():
            print(f'  raw:    {row["clean_text"][:110]}')
            if n > 0:
                print(f'  fields: {row["paren_fields"]}')
            print()

    # ======================================================================
    # 3.2 Tail Decomposition
    # ======================================================================
    tail_parts = listings.apply(decompose_tail, axis=1)
    listings = pd.concat([listings, tail_parts], axis=1)
    errors = listings[listings['tail_error'].notna()]
    print(f'Tail decomposition errors: {len(errors)} / {len(listings)}')
    if len(errors):
        for _, row in errors.head(10).iterrows():
            print(f'  [{row["entry_form"]}] {row["tail_error"]}')
            print(f'    seg_tail={row["seg_tail"]!r}')
            print(f'    raw: {row["clean_text"][:120]}')
            print()
    if len(errors):
        detail = errors[['clean_text', 'entry_form', 'seg_tail', 'tail_error']].head(30).copy()
        detail.insert(0, 'csv_line', detail.index + 2)
        raise AssertionError(
            f'Silent drop: {len(errors)} listing(s) failed tail decomposition. '
            f'These rows would carry null tail_valuation and vanish from postmark_valuation.\n'
            f'First offenders (csv_line = line number in the input CSV):\n{detail.to_string(index=False)}'
        )
    has_annotation = listings['tail_annotation'].notna() & (listings['tail_annotation'] != '')
    print(f'Entries with tail annotation: {has_annotation.sum()} ({has_annotation.sum()/len(listings)*100:.1f}%)')
    annotated = listings[listings['tail_annotation'].notna() & (listings['tail_annotation'] != '')]
    if len(annotated):
        print(f'=== ALL TAIL ANNOTATIONS ({len(annotated)}) ===')
        for _, row in annotated.iterrows():
            print(f'  annotation={row["tail_annotation"]!r}  val={row["tail_valuation"]!r}')
            print(f'    raw: {row["clean_text"][:120]}')
            print()
    else:
        print('No tail annotations found in this file.')

    # ======================================================================
    # 3.3 Valuation Tier Splitting
    # ======================================================================
    listings['valuation_tiers'] = listings['tail_valuation'].apply(split_valuation_tiers)
    listings['valuation_tier_count'] = listings['valuation_tiers'].apply(len)
    print('Valuation tier count distribution:')
    tc = listings['valuation_tier_count'].value_counts().sort_index()
    for n, count in tc.items():
        print(f'  {n} tiers: {count} ({count/len(listings)*100:.1f}%)')
    print()
    multi = listings[listings['valuation_tier_count'] > 1]
    if len(multi):
        print(f'=== MULTI-TIER VALUATIONS ({len(multi)}) ===')
        for _, row in multi.head(10).iterrows():
            print(f'  {row["tail_valuation"]} -> {row["valuation_tiers"]}')
            print(f'    raw: {row["clean_text"][:100]}')
            print()
    unpriced = listings[listings['valuation_tiers'].apply(lambda t: len(t) == 1 and t[0] is None)]
    if len(unpriced):
        print(f'=== UNPRICED ENTRIES ({len(unpriced)}) ===')
        for _, row in unpriced.head(10).iterrows():
            print(f'  {row["clean_text"][:100]}')
    total = len(listings)

    # ======================================================================
    # 3.4 Step 3 Summary
    # ======================================================================
    seg_err = listings['seg_error'].notna().sum() if 'seg_error' in listings.columns else 0
    tail_err = listings['tail_error'].notna().sum()
    has_ann = (listings['tail_annotation'].notna() & (listings['tail_annotation'] != '')).sum()
    print(f'Step 3: Paren Field Splitting and Tail Extraction')
    print(f'  Input listings: {total}')
    print()
    print(f'  Paren field counts:')
    for n, count in listings['paren_field_count'].value_counts().sort_index().items():
        print(f'    {n} fields: {count}')
    print()
    print(f'  Tail decomposition:')
    print(f'    Errors:      {tail_err}')
    print(f'    Annotations: {has_ann}')
    print()
    print(f'  Valuation tiers:')
    for n, count in listings['valuation_tier_count'].value_counts().sort_index().items():
        label = 'unpriced' if n == 1 and listings[listings['valuation_tier_count'] == n]['valuation_tiers'].apply(lambda t: t[0] is None).all() else f'{n}-tier'
        print(f'    {n} tiers: {count}')

    # ======================================================================
    # Step 4: Head Parsing
    # ======================================================================
    head_parts = listings.apply(parse_head, axis=1)
    listings = pd.concat([listings, head_parts], axis=1)
    print(f'Step 4: Head parsing applied to {len(listings)} listings')
    print(f'  First-of-town markers: {listings["head_first_of_town"].sum()}')
    print(f'  Relationship indicators: {listings["head_rel_type"].notna().sum()}')
    has_name = listings['head_name_body'].notna()
    print(f'  Entries with name body: {has_name.sum()} ({has_name.sum()/len(listings)*100:.1f}%)')
    has_ann = listings['head_annotations'].apply(lambda a: len(a) > 0)
    print(f'  Entries with annotations: {has_ann.sum()} ({has_ann.sum()/len(listings)*100:.1f}%)')
    rel_counts = listings['head_rel_type'].value_counts(dropna=False)
    print('Relationship indicator distribution:')
    for val, count in rel_counts.items():
        label = repr(val) if val is not None else '(none -- independent entry)'
        print(f'  {label}: {count}')
    print()
    for rt in listings['head_rel_type'].dropna().unique():
        subset = listings[listings['head_rel_type'] == rt]
        print(f'=== rel_type={rt!r} ({len(subset)} entries) ===')
        for _, row in subset.head(4).iterrows():
            print(f'  seg_head={row["seg_head"]!r}  ->  name_body={row["head_name_body"]!r}  ann={row["head_annotations"]}')
        print()
    independent = listings[listings['head_rel_type'].isna()]
    missing_name = independent[independent['head_name_body'].isna()]
    print(f'Independent entries missing name body: {len(missing_name)} / {len(independent)}')
    if len(missing_name):
        for _, row in missing_name.head(5).iterrows():
            print(f'  seg_head={row["seg_head"]!r}  clean_text={row["clean_text"][:100]}')
    print()
    all_annotations = [a for ann_list in listings['head_annotations'] for a in ann_list]
    ann_counts = pd.Series(all_annotations).value_counts()
    print(f'Annotation values ({len(all_annotations)} total across {has_ann.sum()} entries):')
    for val, count in ann_counts.head(20).items():
        print(f'  {val!r}: {count}')
    print()
    multi_ann = listings[listings['head_annotations'].apply(len) > 1]
    if len(multi_ann):
        print(f'=== MULTI-ANNOTATION HEADS ({len(multi_ann)}) ===')
        for _, row in multi_ann.head(10).iterrows():
            print(f'  seg_head={row["seg_head"]!r}  ->  name={row["head_name_body"]!r}  ann={row["head_annotations"]}')
        print()
    rel_with_name = listings[listings['head_rel_type'].notna() & listings['head_name_body'].notna()]
    if len(rel_with_name):
        print(f'=== REL-INDICATOR ENTRIES WITH NAME BODY ({len(rel_with_name)}) ===')
        for _, row in rel_with_name.head(15).iterrows():
            print(f'  seg_head={row["seg_head"]!r}  rel={row["head_rel_type"]!r}  name={row["head_name_body"]!r}  ann={row["head_annotations"]}')
    print('Optional CSV columns carried on listings DataFrame:')
    for col in OPTIONAL_COLS:
        if col in listings.columns:
            non_null = listings[col].notna().sum()
            nunique = listings[col].nunique()
            print(f'  {col}: {non_null} non-null, {nunique} distinct values')
            if nunique <= 15:
                for val, count in listings[col].value_counts().head(10).items():
                    print(f'    {val!r}: {count}')
        else:
            print(f'  {col}: (absent)')
        print()

    # ======================================================================
    # 4.5 Step 4 Summary
    # ======================================================================
    total = len(listings)
    print(f'Step 4: Head Parsing')
    print(f'  Input listings: {total}')
    print()
    print(f'  First-of-town: {listings["head_first_of_town"].sum()} ({listings["head_first_of_town"].sum()/total*100:.1f}%)')
    print()
    print(f'  Relationship indicators:')
    for val, count in listings['head_rel_type'].value_counts(dropna=True).items():
        print(f'    {val!r}: {count}')
    none_count = listings['head_rel_type'].isna().sum()
    print(f'    (independent): {none_count}')
    print()
    print(f'  Name body present: {listings["head_name_body"].notna().sum()}')
    print(f'  Entries with annotations: {(listings["head_annotations"].apply(len) > 0).sum()}')

    # ======================================================================
    # Step 5: Paren Field-Type Classification
    # ======================================================================
    assert classify_paren_field('April 8,1800') == 'date'
    assert classify_paren_field('1850-53') == 'date'
    assert classify_paren_field("1850's") == 'date'
    assert classify_paren_field('Ms') == 'ms'
    assert classify_paren_field('SL-16.5x5,MDD') == 'size'
    assert classify_paren_field('DC-25,YD') == 'size'
    assert classify_paren_field('32') == 'size'
    assert classify_paren_field('--,YD') == 'size'
    assert classify_paren_field('FREE') == 'rate'
    assert classify_paren_field('25[ms]') == 'rate'
    assert classify_paren_field('P.M.Free') == 'rate'
    assert classify_paren_field('Geo.Fisher P.M.frank') == 'rate'
    assert classify_paren_field('Black') == 'color'
    assert classify_paren_field('Red,Blue') == 'color'
    assert classify_paren_field('Olive-Yellow') == 'color'
    print('All self-tests passed')
    listings['paren_field_types'] = listings['paren_fields'].apply(classify_all_fields)
    all_fields = []
    for _, row in listings.iterrows():
        for pos, (field, ftype) in enumerate(zip(row['paren_fields'], row['paren_field_types'])):
            all_fields.append({
                'position': pos,
                'field_text': field,
                'field_type': ftype,
                'field_count': row['paren_field_count'],
            })
    field_df = pd.DataFrame(all_fields)
    total_fields = len(field_df)
    print(f'Total paren fields classified: {total_fields}')
    print()
    print('Overall type distribution:')
    for ftype, count in field_df['field_type'].value_counts().items():
        print(f'  {ftype}: {count} ({count/total_fields*100:.1f}%)')
    print('Field type by position:')
    print()
    for pos in sorted(field_df['position'].unique()):
        subset = field_df[field_df['position'] == pos]
        print(f'Position {pos} ({len(subset)} fields):')
        for ftype, count in subset['field_type'].value_counts().items():
            print(f'  {ftype}: {count} ({count/len(subset)*100:.1f}%)')
        print()
    listings['field_type_sig'] = listings['paren_field_types'].apply(
        lambda types: '|'.join(types) if types else '(none)'
    )
    sig_counts = listings['field_type_sig'].value_counts()
    print(f'Distinct type signatures: {len(sig_counts)}')
    print()
    print('Type signature distribution:')
    for sig, count in sig_counts.head(20).items():
        print(f'  {sig}: {count} ({count/len(listings)*100:.1f}%)')
    has_other = listings[listings['field_type_sig'].str.contains('other')]
    print(f'\nEntries with "other" fields: {has_other.sum() if isinstance(has_other, pd.Series) else len(has_other)}')
    for ftype in ['date', 'ms', 'size', 'rate', 'color', 'other']:
        subset = field_df[field_df['field_type'] == ftype]
        if len(subset) == 0:
            continue
        print(f'=== {ftype.upper()} ({len(subset)} fields) ===')

        # Show unique values (up to 30)
        uniques = subset['field_text'].value_counts()
        shown = 0
        for val, count in uniques.items():
            print(f'  {val!r}: {count}')
            shown += 1
            if shown >= 30:
                remaining = len(uniques) - shown
                if remaining > 0:
                    print(f'  ... and {remaining} more distinct values')
                break
        print()
    other_fields = field_df[field_df['field_type'] == 'other']
    if len(other_fields) == 0:
        print('No unclassified fields -- all paren tokens matched a known type.')
    else:
        print(f'=== UNCLASSIFIED FIELDS ({len(other_fields)}) ===')
        print()
        for _, frow in other_fields.iterrows():
            # Find the parent listing
            match = listings[
                listings['paren_fields'].apply(lambda pf: frow['field_text'] in pf)
            ]
            if len(match):
                first = match.iloc[0]
                print(f'  field={frow["field_text"]!r}  pos={frow["position"]}')
                print(f'    fields={first["paren_fields"]}')
                print(f'    types={first["paren_field_types"]}')
                print(f'    raw: {first["clean_text"][:120]}')
                print()
    if len(field_df[field_df['position'] == 0]) > 0:
        pos0 = field_df[field_df['position'] == 0]
        non_date_pos0 = pos0[pos0['field_type'] != 'date']
        print(f'Position 0 non-date fields: {len(non_date_pos0)} / {len(pos0)}')
        if len(non_date_pos0):
            for _, frow in non_date_pos0.iterrows():
                match = listings[
                    listings['paren_fields'].apply(lambda pf: len(pf) > 0 and pf[0] == frow['field_text'])
                ]
                if len(match):
                    first = match.iloc[0]
                    print(f'  {frow["field_type"]}: {frow["field_text"]!r}')
                    print(f'    raw: {first["clean_text"][:120]}')
                    print()
    multi = field_df[field_df['field_count'] >= 3].copy()
    if len(multi):
        last_pos = multi.groupby('field_count')['position'].transform('max')
        last_fields = multi[multi['position'] == last_pos]
        non_color_last = last_fields[last_fields['field_type'] != 'color']
        print(f'Last-position non-color fields (3+ field entries): {len(non_color_last)} / {len(last_fields)}')
        if len(non_color_last):
            for _, frow in non_color_last.head(10).iterrows():
                print(f'  pos={frow["position"]} type={frow["field_type"]}: {frow["field_text"]!r}')

    # ======================================================================
    # 5.8 Step 5 Summary
    # ======================================================================
    total = len(listings)
    has_fields = listings[listings['paren_field_count'] > 0]
    total_fields = field_df.shape[0]
    print(f'Step 5: Paren Field-Type Classification')
    print(f'  Listings with paren fields: {len(has_fields)} / {total}')
    print(f'  Total fields classified: {total_fields}')
    print()
    print(f'  Type counts:')
    for ftype, count in field_df['field_type'].value_counts().items():
        print(f'    {ftype}: {count} ({count/total_fields*100:.1f}%)')
    print()
    print(f'  Top type signatures:')
    for sig, count in sig_counts.head(5).items():
        print(f'    {sig}: {count}')
    print()
    other_count = (field_df['field_type'] == 'other').sum()
    print(f'  Unclassified ("other"): {other_count} ({other_count/total_fields*100:.1f}%)')

    # ======================================================================
    # Step 6: Field-Level Sub-Parsing
    # ======================================================================
    assert parse_date_field('April 8,1800') == {
        'date_month': 4, 'date_day': 8, 'date_year_start': 1800,
        'date_year_end': 1800, 'date_granularity': 'DAY',
        'date_is_circa': False, 'date_raw': 'April 8,1800', 'date_error': None,
    }
    assert parse_date_field("1850's")['date_granularity'] == 'DECADE'
    assert parse_date_field("1850's")['date_year_end'] == 1859
    assert parse_date_field('1850-53')['date_year_end'] == 1853
    assert parse_date_field('1852')['date_granularity'] == 'YEAR'
    assert parse_date_field('c1840')['date_is_circa'] is True
    assert parse_date_field('c1840')['date_year_start'] == 1840
    assert parse_date_field('Oct.22,1803')['date_month'] == 10
    print('Date sub-parser self-tests passed')
    r = parse_size_field('SL-16.5x5,MDD')
    assert r['size_shape_code'] == 'SL'
    assert r['size_dim1'] == 16.5
    assert r['size_dim2'] == 5.0
    assert r['size_dateformat'] == 'MDD'
    r = parse_size_field('DC-25,YD')
    assert r['size_shape_code'] == 'DC'
    assert r['size_dim1'] == 25.0
    assert r['size_dateformat'] == 'YD'
    r = parse_size_field('32')
    assert r['size_shape_code'] is None
    assert r['size_dim1'] == 32.0
    assert r['size_dateformat'] is None
    r = parse_size_field('--,YD')
    assert r['size_dim1'] is None
    assert r['size_dateformat'] == 'YD'
    r = parse_size_field('DO-30x24')
    assert r['size_shape_code'] == 'DO'
    assert r['size_dim1'] == 30.0
    assert r['size_dim2'] == 24.0
    r = parse_size_field('SL-45x4,YMDD below')
    assert r['size_shape_code'] == 'SL'
    assert r['size_dateformat'] == 'YMDD'
    assert r['size_qualifier'] == 'below'
    r = parse_size_field('arc & SL-46x26')
    assert r['size_shape_code'] == 'ARC'
    assert r['size_dim1'] == 46.0
    assert r['size_dim2'] == 26.0
    assert r['size_raw'] == 'arc & SL-46x26'
    r = parse_size_field('arc&SL-46x26')
    assert r['size_shape_code'] == 'ARC'
    assert r['size_dim1'] == 46.0
    r = parse_size_field('arc & SL,46')
    assert r['size_shape_code'] == 'ARC'
    assert r['size_dim1'] == 46.0
    r = parse_size_field('arc & SL,46x26')
    assert r['size_shape_code'] == 'ARC'
    assert r['size_dim1'] == 46.0
    assert r['size_dim2'] == 26.0
    r = parse_size_field('arc & SL,YD')
    assert r['size_shape_code'] == 'ARC'
    assert r['size_dim1'] is None
    assert r['size_dateformat'] == 'YD'
    r = parse_size_field('Arc & nonsense-25')
    assert r['size_shape_code'] == 'ARC'
    r = parse_size_field('arc-46x26')
    assert r['size_shape_code'] == 'ARC'
    print('Size sub-parser self-tests passed')

    # ======================================================================
    # Step 6.3: Rate sub-parser
    # ======================================================================
    assert split_rate_tokens('PAID/3[C],FREE') == ['PAID/3[C]', 'FREE']
    assert split_rate_tokens('5,10,PAID') == ['5', '10', 'PAID']
    assert split_rate_tokens('25,12-1/2[ms]Black') == ['25', '12-1/2[ms]Black']
    r = parse_rate_token('PAID/3[C]')
    assert r['rate_keyword'] == 'PAID'
    assert r['rate_amount_raw'] == '3'
    assert r['rate_bracket'] == 'C'
    r = parse_rate_token('25[ms]')
    assert r['rate_amount_raw'] == '25'
    assert r['rate_is_manuscript'] is True
    r = parse_rate_token('FREE')
    assert r['rate_keyword'] == 'FREE'
    assert r['rate_amount_raw'] is None
    r = parse_rate_token('P.M.Free')
    assert r['rate_keyword'] == 'PM_FREE'
    r = parse_rate_token('with 24')
    assert r['rate_keyword'] is None
    assert r['rate_amount_raw'] == '24'
    assert r['rate_raw'] == 'with 24'
    r = parse_rate_token('negative 5[C]')
    assert r['rate_impression'] == 'Negative'
    assert r['rate_amount_raw'] == '5'
    assert r['rate_bracket'] == 'C'
    r = parse_rate_token('STEAM')
    assert r['rate_keyword'] == 'STEAM'
    print('Rate sub-parser self-tests passed')
    assert parse_color_field('Black') == ['BLACK']
    assert parse_color_field('Red,Blue,Black') == ['RED', 'BLUE', 'BLACK']
    assert parse_color_field('Olive-Yellow') == ['OLIVE-YELLOW']
    print('Color sub-parser self-tests passed')
    assert triage_other_field('185-')[0] == 'date'
    assert triage_other_field('186-')[0] == 'date'
    assert triage_other_field('DC--')[0] == 'size'
    assert triage_other_field('DLC--')[0] == 'size'
    assert triage_other_field('5,10')[0] == 'rate'
    assert triage_other_field('12-1/2')[0] == 'rate'
    assert triage_other_field('irregular 34')[0] == 'size'
    assert triage_other_field('30,32')[0] == 'size'
    assert triage_other_field('Red,Purple,Blue,Brownish')[0] == 'color'
    assert triage_other_field('Double 50')[0] == 'rate'
    print('Other-field triage self-tests passed')
    parsed = listings.apply(subparse_fields, axis=1)
    listings = pd.concat([listings, parsed], axis=1)
    print('Step 6: Field-level sub-parsing applied')
    print(f'  Listings processed: {len(listings)}')
    print(f'  Manuscript entries: {listings["is_manuscript"].sum()}')
    if 'Manuscript' in listings.columns:
        csv_ms_hits = listings.apply(_csv_manuscript_truthy, axis=1).sum()
        print(f'    (of which CSV Manuscript column contributed: {csv_ms_hits})')
    print(f'  Entries with dates: {listings["parsed_dates"].apply(len).gt(0).sum()}')
    print(f'  Entries with sizes: {listings["parsed_sizes"].apply(len).gt(0).sum()}')
    print(f'  Entries with rates: {listings["parsed_rates"].apply(len).gt(0).sum()}')
    print(f'  Entries with colors: {listings["parsed_colors"].apply(len).gt(0).sum()}')
    total_reclassified = listings['reclassified_fields'].apply(len).sum()
    print(f'  Other fields reclassified: {total_reclassified}')
    remaining_other = listings['other_fields'].apply(len).sum()
    print(f'  Remaining unresolved other fields: {remaining_other}')
    _ms_dates_added = 0
    for idx in listings.index[listings['is_manuscript_section'].fillna(False)]:
        raw = listings.at[idx, 'ms_date_text']
        sub_tokens = _split_ms_date_token(raw)
        if not sub_tokens:
            continue
        existing = listings.at[idx, 'parsed_dates']
        if not isinstance(existing, list):
            existing = []
        new_dates = list(existing)
        for tok in sub_tokens:
            try:
                parsed = parse_date_field(tok)
            except Exception as exc:
                # Keep failures non-fatal but visible -- flag in s7 later if needed.
                print(f'  WARN: parse_date_field({tok!r}) failed for listing idx={idx}: {exc}')
                continue
            new_dates.append(parsed)
            _ms_dates_added += 1
        listings.at[idx, 'parsed_dates'] = new_dates
    print(f'Step 6.6b: folded {_ms_dates_added} manuscript-row dates into parsed_dates.')
    date_errors = []
    for idx, row in listings.iterrows():
        for d in row['parsed_dates']:
            if d.get('date_error') and 'reclassified' not in str(d.get('date_error', '')):
                date_errors.append((idx, d['date_raw'], d['date_error']))
    print(f'Date parse errors: {len(date_errors)}')
    for idx, raw, err in date_errors[:10]:
        print(f'  [{idx}] {raw!r}: {err}')
    print()
    size_errors = []
    for idx, row in listings.iterrows():
        for s in row['parsed_sizes']:
            if s.get('size_error'):
                size_errors.append((idx, s['size_raw'], s['size_error']))
    print(f'Size parse errors: {len(size_errors)}')
    for idx, raw, err in size_errors[:10]:
        print(f'  [{idx}] {raw!r}: {err}')
    print()
    reclass_all = []
    for idx, row in listings.iterrows():
        for rc in row['reclassified_fields']:
            reclass_all.append((idx, rc['field'], rc['new_type']))
    print(f'Reclassified other fields: {len(reclass_all)}')
    for idx, field, new_type in reclass_all[:15]:
        print(f'  [{idx}] {field!r} -> {new_type}')
    print()
    unresolved = []
    for idx, row in listings.iterrows():
        for f in row['other_fields']:
            unresolved.append((idx, f, row['clean_text'][:100]))
    print(f'Unresolved other fields: {len(unresolved)}')
    for idx, field, raw in unresolved:
        print(f'  [{idx}] {field!r}')
        print(f'    raw: {raw}')
        print()
    all_dates = [d for dlist in listings['parsed_dates'] for d in dlist]
    if all_dates:
        gran_counts = pd.Series([d['date_granularity'] for d in all_dates]).value_counts()
        print('Date granularity distribution:')
        for g, c in gran_counts.items():
            print(f'  {g}: {c}')
        print()
    all_sizes = [s for slist in listings['parsed_sizes'] for s in slist]
    if all_sizes:
        shape_counts = pd.Series([s['size_shape_code'] or '(bare dimension)' for s in all_sizes]).value_counts()
        print('Size shape code distribution:')
        for s, c in shape_counts.items():
            print(f'  {s}: {c}')
        print()

        # Dateformat on size
        df_counts = pd.Series([s['size_dateformat'] or '(none)' for s in all_sizes]).value_counts()
        print('Size dateformat distribution:')
        for d, c in df_counts.items():
            print(f'  {d}: {c}')
        print()
    all_rate_tokens = [t for rlist in listings['parsed_rates'] for toks in rlist for t in toks]
    if all_rate_tokens:
        kw_counts = pd.Series([t['rate_keyword'] or '(bare amount)' for t in all_rate_tokens]).value_counts()
        print('Rate keyword distribution:')
        for k, c in kw_counts.items():
            print(f'  {k}: {c}')
        print()

        ms_rate = sum(1 for t in all_rate_tokens if t['rate_is_manuscript'])
        print(f'Manuscript rate tokens: {ms_rate}')
        neg_rate = sum(1 for t in all_rate_tokens if t['rate_impression'] == 'Negative')
        print(f'Negative-impression rate tokens: {neg_rate}')
        print()
    all_colors = [c for clist in listings['parsed_colors'] for c in clist]
    if all_colors:
        color_counts = pd.Series(all_colors).value_counts()
        print('Color distribution:')
        for c, n in color_counts.items():
            print(f'  {c}: {n}')
        print()

    # ======================================================================
    # 6.9 Step 6 Summary
    # ======================================================================
    total = len(listings)
    print('Step 6: Field-Level Sub-Parsing')
    print(f'  Listings processed: {total}')
    print()
    print(f'  Dates parsed:        {sum(len(d) for d in listings["parsed_dates"])}')
    print(f'  Sizes parsed:        {sum(len(s) for s in listings["parsed_sizes"])}')
    print(f'  Rate fields parsed:  {sum(len(r) for r in listings["parsed_rates"])}')
    print(f'    Rate tokens total: {sum(len(t) for rlist in listings["parsed_rates"] for t in rlist)}')
    print(f'  Colors extracted:    {sum(len(c) for c in listings["parsed_colors"])}')
    print(f'  Manuscript entries:  {listings["is_manuscript"].sum()}')
    print()
    print(f'  Reclassified others: {sum(len(r) for r in listings["reclassified_fields"])}')
    print(f'  Unresolved others:   {sum(len(o) for o in listings["other_fields"])}')

    # ======================================================================
    # Step 7: Relationship Resolution
    # ======================================================================
    listings = resolve_relationships(listings)
    listings = roll_up_catalog_text(listings)
    print(f'Step 7: Relationship resolution applied to {len(listings)} listings')
    print(f'  Independent entries: {listings["parent_idx"].isna().sum()}')
    print(f'  Resolved from parent: {listings["parent_idx"].notna().sum()}')
    print(f'  Distinct resolved towns: {listings["resolved_town"].nunique()}')
    inherited_ms_count = 0
    inherited_color_count = 0
    inherited_size_count = 0
    inherited_dates_count = 0
    # Walk in catalog order. Source is the preceding sibling under the same
    # parent (or the parent itself, for first children) -- prev_sibling_idx
    # encodes both cases. Because earlier siblings have already been mutated
    # by this same loop, reading from listings.loc[src_idx] yields the
    # post-inheritance value, giving us transitive carry-forward "until
    # someone overrides" semantics without a second pass.
    for pos in range(len(listings)):
        row = listings.iloc[pos]
        src_idx = row['prev_sibling_idx']
        if src_idx is None or (isinstance(src_idx, float) and pd.isna(src_idx)):
            continue

        src = listings.loc[src_idx]
        child_types = set(row['paren_field_types'])

        # is_manuscript
        if 'ms' not in child_types and 'size' not in child_types:
            if src['is_manuscript'] != row['is_manuscript']:
                listings.iat[pos, listings.columns.get_loc('is_manuscript')] = src['is_manuscript']
                inherited_ms_count += 1

        # parsed_colors
        if not row['parsed_colors']:
            if src['parsed_colors']:
                listings.iat[pos, listings.columns.get_loc('parsed_colors')] = src['parsed_colors'].copy()
                inherited_color_count += 1

        # parsed_sizes
        if not row['parsed_sizes']:
            if src['parsed_sizes']:
                listings.iat[pos, listings.columns.get_loc('parsed_sizes')] = src['parsed_sizes'].copy()
                inherited_size_count += 1

        # parsed_dates
        if not row['parsed_dates']:
            if src['parsed_dates']:
                listings.iat[pos, listings.columns.get_loc('parsed_dates')] = src['parsed_dates'].copy()
                inherited_dates_count += 1
    print()
    print('Step 7.1b: Attribute inheritance (from preceding sibling)')
    print(f'  is_manuscript inherited:  {inherited_ms_count}')
    print(f'  parsed_colors inherited:  {inherited_color_count}')
    print(f'  parsed_sizes inherited:   {inherited_size_count}')
    print(f'  parsed_dates inherited:   {inherited_dates_count}')
    canonical_by_alias = {}
    for rt in listings['resolved_town'].dropna().unique():
        m = OR_ALIAS_RE.match(str(rt))
        if not m:
            continue
        a = _norm_for_alias(m.group(1))
        b = _norm_for_alias(m.group(2))
        if not a or not b:
            continue
        # Catalog convention: the first-listed name in `X or Y` is the
        # preferred spelling; the second is an acknowledged alternate.
        canonical = a
        canonical_by_alias[a] = canonical
        canonical_by_alias[b] = canonical
        canonical_by_alias[_norm_for_alias(rt)] = canonical
    meta_by_loc = {}
    for _, mrow in meta_df.iterrows():
        txt = str(mrow.get('Listing', '')).strip()
        if not TOWN_HEADING_RE.match(txt):
            continue
        page = mrow.get('Page')
        chunk = mrow.get('Chunk')
        if pd.isna(page) or pd.isna(chunk):
            continue
        key = (int(page), int(chunk))
        # Last META at a given location wins (closest-above semantics).
        meta_by_loc[key] = _norm_for_alias(txt)
    hs_first = listings[
        listings['head_first_of_town'].fillna(False)
        & (~listings['is_manuscript_section'].fillna(False))
    ]
    for _, lrow in hs_first.iterrows():
        rt = _norm_for_alias(lrow['resolved_town'])
        if not rt:
            continue
        page = lrow.get('Page')
        chunk = lrow.get('Chunk')
        if pd.isna(page) or pd.isna(chunk):
            continue
        full = meta_by_loc.get((int(page), int(chunk)))
        if full and _is_abbrev_of(rt, full):
            canonical_by_alias.setdefault(rt, full)
    def _apply_alias(rt):
        key = _norm_for_alias(rt)
        if key is None:
            return rt
        return canonical_by_alias.get(key, rt)
    listings['resolved_town_pre_alias'] = listings['resolved_town']
    listings['resolved_town'] = listings['resolved_town'].map(_apply_alias)
    n_changed = (
        listings['resolved_town'].astype('string').fillna('')
        != listings['resolved_town_pre_alias'].astype('string').fillna('')
    ).sum()
    print(f'Step 7.6: alias merge rewrote {n_changed} resolved_town values '
          f'across {len(canonical_by_alias)} alias entries.')
    if canonical_by_alias:
        pairs = sorted(set(
            (k, v) for k, v in canonical_by_alias.items() if k != v
        ))
        if pairs:
            print(f'  Aliases ({len(pairs)} non-identity):')
            for k, v in pairs[:50]:
                print(f'    {k!r} -> {v!r}')
            if len(pairs) > 50:
                print(f'    ... {len(pairs) - 50} more')
    for rt in [None, 'Same', '(L)', '(E)']:
        if rt is None:
            subset = listings[listings['head_rel_type'].isna()]
            label = '(independent)'
        else:
            subset = listings[listings['head_rel_type'] == rt]
            label = rt
        print(f'=== rel_type={label} ({len(subset)} entries) ===')
        for _, row in subset.head(6).iterrows():
            parent_info = ''
            if pd.notna(row['parent_idx']):
                p = listings.loc[row['parent_idx']]
                parent_info = f'  parent_head={p["seg_head"]!r}'
            print(f'  seg_head={row["seg_head"]!r}')
            print(f'    -> inscription={row["resolved_inscription"]!r}  town={row["resolved_town"]!r}{parent_info}')
            if row['s7_warnings']:
                print(f'    WARNINGS: {row["s7_warnings"]}')
        print()
    same_with_name = listings[
        (listings['head_rel_type'] == 'Same') & listings['head_name_body'].notna()
    ]
    print(f'=== Same with name body ({len(same_with_name)} entries) ===')
    for _, row in same_with_name.head(10).iterrows():
        if pd.notna(row['parent_idx']):
            p = listings.loc[row['parent_idx']]
            print(f'  head={row["seg_head"]!r}  name_body={row["head_name_body"]!r}')
            print(f'    parent inscription={p["resolved_inscription"]!r}  parent town={p["resolved_town"]!r}')
            print(f'    -> resolved={row["resolved_inscription"]!r}  town={row["resolved_town"]!r}')
            if row['s7_warnings']:
                print(f'    WARNINGS: {row["s7_warnings"]}')
            print()
        else:
            print(f'  head={row["seg_head"]!r}  name_body={row["head_name_body"]!r}  *** ORPHAN ***')
    missing_inscription = listings['resolved_inscription'].isna() | (listings['resolved_inscription'] == '')
    missing_town = listings['resolved_town'].isna() | (listings['resolved_town'] == '')
    print(f'V1: Missing resolved_inscription: {missing_inscription.sum()}')
    print(f'V1: Missing resolved_town: {missing_town.sum()}')
    if missing_town.any():
        town_problem = listings[missing_town]
        town_cols = [c for c in ['head_rel_type', 'clean_text',
                                 'resolved_inscription'] if c in town_problem.columns]
        town_view = town_problem[town_cols].head(30).copy()
        town_view.insert(0, 'csv_line', town_view.index + 2)
        print(f'WARNING: {len(town_problem)} listing(s) have no resolved_town '
              f'(expected for no-town markings; will fall back to UNKNOWN PostOffice).')
        print('csv_line = line number in the input CSV')
        print(town_view.to_string(index=False))
    null_inscription = listings['resolved_inscription'].isna()
    empty_inscription = (~null_inscription) & (listings['resolved_inscription'] == '')
    if empty_inscription.any():
        no_town = listings[empty_inscription]
        cols_nt = [c for c in ['head_rel_type', 'clean_text'] if c in no_town.columns]
        detail_nt = no_town[cols_nt].head(20).copy()
        detail_nt.insert(0, 'csv_line', detail_nt.index + 2)
        print(f'WARNING: {len(no_town)} listing(s) have empty resolved_inscription '
              f'(no-town CDS or unresolvable head_name_body). '
              f'Step 8.8 UNKNOWN PostOffice fallback will apply.\n'
              + detail_nt.to_string(index=False))
    if null_inscription.any():
        problem = listings[null_inscription]
        cols = [c for c in ['head_rel_type', 'clean_text',
                            'resolved_inscription', 'resolved_town'] if c in problem.columns]
        detail = problem[cols].head(30).copy()
        detail.insert(0, 'csv_line', detail.index + 2)
        raise AssertionError(
            f'Silent drop: {len(problem)} listing(s) have null resolved_inscription '
            f'(should never happen -- resolve_relationships always assigns a value).\n'
            f'First offenders (csv_line = line number in the input CSV):\n{detail.to_string(index=False)}'
        )
    print()
    rel_entries = listings[listings['head_rel_type'].notna()]
    orphans = rel_entries[rel_entries['parent_idx'].isna()]
    print(f'V2: Orphan rel entries: {len(orphans)}')
    if len(orphans):
        cols = [c for c in ['head_rel_type', 'clean_text'] if c in orphans.columns]
        detail = orphans[cols].copy()
        detail.insert(0, 'csv_line', detail.index + 2)
        # resolve_relationships already applied a best-effort fallback inscription
        # (own name body if present, otherwise empty string). Warn here so the
        # human can inspect cross-page or cross-section parent misses, but let
        # the row proceed -- it will get an UNKNOWN PostOffice via Step 8.8.
        print(f'WARNING: {len(orphans)} relationship entry(s) have no resolvable parent '
              f'(orphan_rel). They inherit from best-effort fallback inscription.\n'
              + detail.to_string(index=False))
    print()
    fot_entries = listings[listings['head_first_of_town']]
    fot_rel = fot_entries[fot_entries['head_rel_type'].notna()]
    print(f'V3: first_of_town entries: {len(fot_entries)}')
    print(f'    of which are rel indicators: {len(fot_rel)}')
    if len(fot_rel):
        for _, row in fot_rel.iterrows():
            print(f'  [{row["head_rel_type"]}] {row["clean_text"][:100]}')
    print()
    all_warnings = [w for wlist in listings['s7_warnings'] for w in wlist]
    print(f'V4: Total warnings: {len(all_warnings)}')
    if all_warnings:
        warn_counts = pd.Series(all_warnings).value_counts()
        for w, c in warn_counts.items():
            print(f'  {w}: {c}')
    print()
    same_with_name = listings[
        (listings['head_rel_type'] == 'Same') & listings['head_name_body'].notna()
    ]
    no_slash = same_with_name[~same_with_name['head_name_body'].str.startswith('/')]
    print(f'V5: Same-with-name entries: {len(same_with_name)}')
    print(f'    starting with /: {len(same_with_name) - len(no_slash)}')
    print(f'    NOT starting with / (flagged): {len(no_slash)}')
    if len(no_slash):
        for _, row in no_slash.iterrows():
            print(f'  head={row["seg_head"]!r}  name_body={row["head_name_body"]!r}')
            print(f'    resolved={row["resolved_inscription"]!r}')
    town_counts = listings['resolved_town'].value_counts()
    print(f'Distinct towns: {len(town_counts)}')
    print(f'Towns with most listings:')
    for town, count in town_counts.head(20).items():
        print(f'  {town!r}: {count}')
    print()
    single = (town_counts == 1).sum()
    print(f'Single-listing towns: {single} ({single/len(town_counts)*100:.1f}%)')
    print()
    inscription_per_town = listings.groupby('resolved_town')['resolved_inscription'].nunique()
    multi_variant = inscription_per_town[inscription_per_town > 1]
    print(f'Towns with multiple inscription variants: {len(multi_variant)}')
    for town, n_variants in multi_variant.head(15).items():
        variants = listings[listings['resolved_town'] == town]['resolved_inscription'].unique()
        print(f'  {town!r}: {n_variants} variants')
        for v in variants:
            print(f'    {v!r}')

    # ======================================================================
    # 7.5 Step 7 Summary
    # ======================================================================
    total = len(listings)
    rel_count = listings['head_rel_type'].notna().sum()
    warn_count = sum(len(w) for w in listings['s7_warnings'])
    print('Step 7: Relationship Resolution')
    print(f'  Listings processed: {total}')
    print()
    print(f'  Independent entries:  {total - rel_count}')
    print(f'  Resolved from parent: {rel_count}')
    print(f'    Same (no name):     {((listings["head_rel_type"] == "Same") & listings["head_name_body"].isna()).sum()}')
    print(f'    Same (with name):   {((listings["head_rel_type"] == "Same") & listings["head_name_body"].notna()).sum()}')
    print(f'    (L):                {(listings["head_rel_type"] == "(L)").sum()}')
    print(f'    (E):                {(listings["head_rel_type"] == "(E)").sum()}')
    print()
    print(f'  Distinct towns:       {listings["resolved_town"].nunique()}')
    print(f'  Total warnings:       {warn_count}')
    print()
    rel_entries = listings[listings['parent_idx'].notna()]
    print('  Attribute inheritance impact (rel entries only):')
    ms_from_parent = rel_entries[rel_entries['is_manuscript']].shape[0]
    print(f'    Manuscript after inheritance: {ms_from_parent}')
    has_colors = rel_entries[rel_entries['parsed_colors'].apply(len) > 0].shape[0]
    print(f'    With colors after inheritance: {has_colors}')
    has_sizes = rel_entries[rel_entries['parsed_sizes'].apply(len) > 0].shape[0]
    print(f'    With sizes after inheritance: {has_sizes}')

    # ======================================================================
    # 8.1 Value Table Construction
    # ======================================================================
    shapes_df = pd.DataFrame({
        'shape_id': range(1, len(SHAPE_SEEDS) + 1),
        'name': SHAPE_SEEDS,
    })
    shape_lookup = dict(zip(shapes_df['name'].str.upper(), shapes_df['shape_id']))
    all_color_names = sorted({
        c for clist in listings['parsed_colors'] for c in clist
    })
    colors_df = pd.DataFrame({
        'color_id': range(1, len(all_color_names) + 1),
        'name': all_color_names,
    })
    color_lookup = dict(zip(colors_df['name'], colors_df['color_id']))
    letterings_df = pd.DataFrame({
        'lettering_id': range(1, len(LETTERING_SEEDS) + 1),
        'name': LETTERING_SEEDS,
    })
    lettering_lookup = dict(zip(
        letterings_df['name'].str.lower(),
        letterings_df['lettering_id'],
    ))
    _lettering_aliases = {
        'italics': 'italic',
        'serifs': 'serif',
        'sans serifs': 'sans-serif',
        'sans serif': 'sans-serif',
    }
    for alias, canonical in _lettering_aliases.items():
        if canonical in lettering_lookup:
            lettering_lookup[alias] = lettering_lookup[canonical]
    print(f'Value tables constructed:')
    print(f'  Shapes:     {len(shapes_df)} seeds')
    print(f'  Colors:     {len(colors_df)} discovered')
    print(f'  Letterings: {len(letterings_df)} seeds')
    print()
    print(f'Colors: {all_color_names}')

    # ======================================================================
    # 8.2 Effective Shape Resolution
    # ======================================================================
    shape_resolution = listings.apply(
        lambda row: pd.Series(resolve_effective_shape(row),
                              index=['effective_shape_code', 'shape_source']),
        axis=1
    )
    listings = pd.concat([listings, shape_resolution], axis=1)
    resolved = listings['effective_shape_code'].apply(
        lambda c: pd.Series(resolve_shape_name(c),
                            index=['shape_name', 'shape_error'])
    )
    listings = pd.concat([listings, resolved], axis=1)
    listings['shape_id'] = listings['shape_name'].apply(
        lambda n: None if (n is None or (isinstance(n, float) and pd.isna(n))) else shape_lookup.get(n.upper())
    )
    ms_with_shape = listings[
        listings['is_manuscript_section'].fillna(False) &
        listings['shape_id'].notna()
    ]
    if len(ms_with_shape):
        raise AssertionError(
            f'{len(ms_with_shape)} manuscript row(s) incorrectly received a shape_id.'
        )
    print('Shape resolution source distribution:')
    for src, count in listings['shape_source'].value_counts().items():
        print(f'  {src}: {count}')
    print()
    print('Shape distribution:')
    for name, count in listings['shape_name'].dropna().value_counts().items():
        print(f'  {name}: {count}')
    n_ms_null = int(listings['shape_name'].isna().sum())
    if n_ms_null:
        print(f'  (null -- manuscript): {n_ms_null}')
    print()
    errors = listings[listings['shape_error'].notna()]
    if len(errors):
        print(f'Shape resolution errors: {len(errors)}')
        for _, row in errors.head(10).iterrows():
            print(f'  {row["shape_error"]}  raw: {row["clean_text"][:80]}')
    else:
        print('No shape resolution errors.')

    # ======================================================================
    # 8.25 Image Flow-Down Pre-Pass
    # ======================================================================
    # Catalog layout convention: when N images sit directly above a listing,
    # only the first (counter=1, top-left in reading order) belongs to that
    # listing; images 2..N "flow down" to the next listing in catalog order,
    # recursively, stopping when the next listing already has its own
    # OCR-detected images above it (non-zero 'Images Above').
    #
    # This pre-pass computes, per source-listing row, the definitive list of
    # image file refs (page, chunk, counter) that listing owns. Refs are
    # later used by Step 11 to emit image rows. File names are unchanged by
    # flow-down; only the listing they associate with changes.
    _ordered_idx = list(listings.index)
    _refs_by_idx = {}
    _ocr_count_by_idx = {}
    for _idx in _ordered_idx:
        _row = listings.loc[_idx]
        _ia = _row.get('Images Above')
        _n = int(_ia) if pd.notna(_ia) else 0
        _ocr_count_by_idx[_idx] = _n
        _page = _row.get('Page')
        _chunk = _row.get('Chunk')
        if _n > 0 and pd.notna(_page) and pd.notna(_chunk):
            _refs_by_idx[_idx] = [
                (int(_page), int(_chunk), _c) for _c in range(1, _n + 1)
            ]
        else:
            _refs_by_idx[_idx] = []
    _flow_events = 0
    for _pos, _idx in enumerate(_ordered_idx):
        _refs = _refs_by_idx[_idx]
        if len(_refs) <= 1:
            continue
        if _pos + 1 >= len(_ordered_idx):
            continue
        _next_idx = _ordered_idx[_pos + 1]
        if _ocr_count_by_idx[_next_idx] > 0:
            continue
        # Flow refs[1:] down to next listing; refs[0] stays here.
        _refs_by_idx[_next_idx] = _refs[1:] + _refs_by_idx[_next_idx]
        _refs_by_idx[_idx] = _refs[:1]
        _flow_events += 1
    listings['image_file_refs'] = listings.index.map(_refs_by_idx)
    _total_refs = sum(len(v) for v in _refs_by_idx.values())
    _listings_with_imgs = sum(1 for v in _refs_by_idx.values() if v)
    print()
    print('Image flow-down pre-pass:')
    print(f'  Total image refs:              {_total_refs}')
    print(f'  Listings with at least 1 ref:  {_listings_with_imgs}')
    print(f'  Flow-down events applied:      {_flow_events}')

    # ======================================================================
    # 8.3 Color Fan-Out
    # ======================================================================
    expanded_rows = []
    next_townmark_id = 1
    for idx, row in listings.iterrows():
        colors = row['parsed_colors']
        is_multi_color = len(colors) > 1

        if not colors:
            r = {
                'townmark_id': next_townmark_id,
                'source_listing_idx': idx,
                'color_name': None,
                'color_id': None,
                'is_multi_color_fanout': False,
                'fanout_idx': 0,
            }
            expanded_rows.append(r)
            next_townmark_id += 1
        else:
            for i, color_name in enumerate(colors):
                r = {
                    'townmark_id': next_townmark_id,
                    'source_listing_idx': idx,
                    'color_name': color_name,
                    'color_id': color_lookup.get(color_name),
                    'is_multi_color_fanout': is_multi_color,
                    'fanout_idx': i,
                }
                expanded_rows.append(r)
                next_townmark_id += 1
    fanout_df = pd.DataFrame(expanded_rows)
    multi_color_listings = listings[listings['parsed_colors'].apply(len) > 1]
    total_townmarks = len(fanout_df)
    print(f'Color fan-out results:')
    print(f'  Input listings: {len(listings)}')
    print(f'  Output townmark rows: {total_townmarks}')
    print(f'  Net expansion: {total_townmarks - len(listings)} rows')
    print(f'  Multi-color source listings: {len(multi_color_listings)}')
    print(f'  Rows from multi-color fan-out: {fanout_df["is_multi_color_fanout"].sum()}')
    print()
    color_counts = listings['parsed_colors'].apply(len).value_counts().sort_index()
    print('Colors-per-listing distribution:')
    for n, count in color_counts.items():
        print(f'  {n} colors: {count} listings')

    # ======================================================================
    # 8.4 Townmark Record Assembly
    # ======================================================================
    def build_townmark(fan_row):
        """Assemble a single Townmark record from a fan-out row + source listing."""
        src = listings.loc[fan_row['source_listing_idx']]

        is_ms = bool(src['is_manuscript'])

        # Dimensions: first parsed_sizes entry (if any)
        width, height = None, None
        is_irreg = None if is_ms else False
        date_format = None
        if src['parsed_sizes']:
            s = src['parsed_sizes'][0]
            width = s.get('size_dim1')
            height = s.get('size_dim2')
            if width is not None and height is None:
                height = width
            if s.get('size_is_irregular'):
                is_irreg = True
            if s.get('size_dateformat'):
                date_format = s['size_dateformat']

        # Shape: null for manuscript, required for handstamped
        shape_id = None if is_ms else src['shape_id']

        # Impression: null for manuscript, default Normal for handstamped
        impression = None if is_ms else 'Normal'

        # Lettering: null for manuscript (per invariant), resolved from annotations otherwise
        lettering_id = None if is_ms else src.get('lettering_id')

        # Parent listing (intermediate for review)
        parent_listing_idx = src.get('parent_idx')
        if pd.notna(parent_listing_idx):
            parent_listing_idx = int(parent_listing_idx)
        else:
            parent_listing_idx = None

        # Images above: catalog page image count (intermediate field for human review)
        images_above = src.get('Images Above')
        if pd.notna(images_above):
            images_above = int(images_above)
        else:
            images_above = None

        # Page and chunk from the OCR extractor. Together they identify the
        # extracted image files used in Step 11:
        # backend/media/<region>/va-<page>-<chunk>-<counter>.png
        page = src.get('Page')
        if pd.notna(page):
            page = int(page)
        else:
            page = None

        chunk = src.get('Chunk')
        if pd.notna(chunk):
            chunk = int(chunk)
        else:
            chunk = None

        # Townmark code: minted from RW_CODE + REGION_ABBREV + the listing's
        # position in the original input CSV (1-based) + fanout index. The
        # .{fanout_idx} suffix keeps codes unique across multi-color fan-outs
        # of the same listing. This is an intermediate join key that wires
        # townmarks_df codes are referenced as marking_code in dates_seen during construction;
        # it is resolved to the integer marking_id at emit time (Step 10) and
        # never appears in the final markings.csv. source_listing_idx preserves
        # the original CSV row index because listings is a filter-copy of df.
        listing_pos = int(fan_row['source_listing_idx']) + 1
        code = f"{RW_CODE}-{REGION_ABBREV}-{listing_pos}.{int(fan_row['fanout_idx'])}"

        return {
            'townmark_id': fan_row['townmark_id'],
            'code': code,
            'catalog_text': src['rolled_catalog_text'],
            'inscription_text': src['resolved_inscription'],
            'is_manuscript': is_ms,
            'shape_id': shape_id,
            'lettering_id': lettering_id,
            'color_id': fan_row['color_id'],
            'width': width,
            'height': height,
            'is_irregular': is_irreg,
            'date_format': date_format,
            'date_type': None,
            'impression': impression,
            'post_office_id': None,  # filled in 8.8
            'source_listing_idx': fan_row['source_listing_idx'],
            'color_name': fan_row['color_name'],
            'is_multi_color_fanout': fan_row['is_multi_color_fanout'],
            'images_above': images_above,
            'page': page,
            'chunk': chunk,
            'parent_listing_idx': parent_listing_idx,
        }
    townmarks_df = pd.DataFrame(
        [build_townmark(row) for _, row in fanout_df.iterrows()]
    )
    _pm_by_listing_color = {}
    for _, row in townmarks_df.iterrows():
        key = (row['source_listing_idx'], row['color_name'])
        _pm_by_listing_color[key] = row['townmark_id']
    _pm_first_by_listing = townmarks_df.groupby('source_listing_idx')['townmark_id'].first()
    def resolve_parent_pm(row):
        pidx = row['parent_listing_idx']
        if pd.isna(pidx) or pidx is None:
            return None
        pidx = int(pidx)
        key = (pidx, row['color_name'])
        if key in _pm_by_listing_color:
            return _pm_by_listing_color[key]
        return _pm_first_by_listing.get(pidx)
    townmarks_df['parent_townmark_id'] = townmarks_df.apply(resolve_parent_pm, axis=1)
    townmarks_df.drop(columns=['parent_listing_idx'], inplace=True)
    print(f'Townmarks assembled: {len(townmarks_df)}')
    print(f'  Manuscript: {townmarks_df["is_manuscript"].sum()}')
    print(f'  Handstamped: {(~townmarks_df["is_manuscript"]).sum()}')
    print(f'  With code: {townmarks_df["code"].notna().sum()}')
    print(f'  With color: {townmarks_df["color_id"].notna().sum()}')
    print(f'  With dimensions: {townmarks_df["width"].notna().sum()}')
    print(f'  With images_above: {townmarks_df["images_above"].notna().sum()}')
    print(f'  With parent (inherited): {townmarks_df["parent_townmark_id"].notna().sum()}')
    print(f'  With date_fmt: {townmarks_df["date_format"].notna().sum()}')
    print(f'  Multi-color fan-out rows: {townmarks_df["is_multi_color_fanout"].sum()}')
    print()
    coded = townmarks_df[townmarks_df['code'].notna()]
    if len(coded):
        dup = coded['code'][coded['code'].duplicated()]
        if len(dup):
            print(f'WARNING: duplicate Townmark codes detected ({len(dup)}):')
            for c in dup.unique()[:10]:
                print(f'  {c!r}')
        else:
            print(f'Townmark.code unique across all {len(coded)} coded rows.')
        print()
    if townmarks_df['parent_townmark_id'].notna().any():
        children = townmarks_df[townmarks_df['parent_townmark_id'].notna()]
        color_matched = 0
        fallback_used = 0
        for _, row in children.iterrows():
            parent_pm = townmarks_df[townmarks_df['townmark_id'] == row['parent_townmark_id']].iloc[0]
            if row['color_name'] == parent_pm['color_name']:
                color_matched += 1
            else:
                fallback_used += 1
        print(f'Parent-townmark linkage:')
        print(f'  Color-matched: {color_matched}')
        print(f'  Fallback (first of parent): {fallback_used}')
        print()
    shape_dist = townmarks_df['shape_id'].value_counts(dropna=False).sort_index()
    print('Shape distribution on townmarks:')
    for sid, count in shape_dist.items():
        if pd.isna(sid):
            print(f'  (null -- manuscript): {count}')
        else:
            name = shapes_df.loc[shapes_df['shape_id'] == sid, 'name'].iloc[0]
            print(f'  {name}: {count}')
    print()
    lettering_hits = townmarks_df['lettering_id'].notna().sum()
    print(f'Lettering assigned on townmarks: {lettering_hits}')
    if lettering_hits:
        for lid, count in townmarks_df['lettering_id'].value_counts(dropna=True).items():
            name = letterings_df.loc[letterings_df['lettering_id'] == lid, 'name'].iloc[0]
            print(f'  {name}: {count}')

    # ======================================================================
    # 8.5 DateObserved Assembly
    # ======================================================================
    date_rows = []
    next_date_id = 1
    for _, pm in townmarks_df.iterrows():
        src = listings.loc[pm['source_listing_idx']]
        for d in src['parsed_dates']:
            gran = d['date_granularity']

            if gran == 'DAY':
                try:
                    obs_date = dt_date(d['date_year_start'], d['date_month'], d['date_day'])
                except (ValueError, TypeError):
                    obs_date = None
                date_rows.append({
                    'date_observed_id': next_date_id,
                    'townmark_id': pm['townmark_id'],
                    'date': str(obs_date) if obs_date else None,
                    'granularity': 'DAY',
                    'date_raw': d.get('date_raw'),
                    'date_error': d.get('date_error'),
                })
                next_date_id += 1

            elif gran == 'MONTH':
                try:
                    obs_date = dt_date(d['date_year_start'], d['date_month'], 1)
                except (ValueError, TypeError):
                    obs_date = None
                date_rows.append({
                    'date_observed_id': next_date_id,
                    'townmark_id': pm['townmark_id'],
                    'date': str(obs_date) if obs_date else None,
                    'granularity': 'MONTH',
                    'date_raw': d.get('date_raw'),
                    'date_error': d.get('date_error'),
                })
                next_date_id += 1

            elif gran == 'YEAR':
                try:
                    obs_date = dt_date(d['date_year_start'], 1, 1)
                except (ValueError, TypeError):
                    obs_date = None
                date_rows.append({
                    'date_observed_id': next_date_id,
                    'townmark_id': pm['townmark_id'],
                    'date': str(obs_date) if obs_date else None,
                    'granularity': 'YEAR',
                    'date_raw': d.get('date_raw'),
                    'date_error': d.get('date_error'),
                })
                next_date_id += 1

            elif gran in ('RANGE', 'DECADE'):
                # Two bookend YEAR rows
                for yr in (d['date_year_start'], d['date_year_end']):
                    try:
                        obs_date = dt_date(int(yr), 1, 1)
                    except (ValueError, TypeError):
                        obs_date = None
                    date_rows.append({
                        'date_observed_id': next_date_id,
                        'townmark_id': pm['townmark_id'],
                        'date': str(obs_date) if obs_date else None,
                        'granularity': 'YEAR',
                        'date_raw': d.get('date_raw'),
                        'date_error': d.get('date_error'),
                    })
                    next_date_id += 1
    date_observed_df = pd.DataFrame(date_rows) if date_rows else pd.DataFrame(
        columns=['date_observed_id', 'townmark_id', 'date', 'granularity',
                 'date_raw', 'date_error']
    )
    print(f'DateObserved rows: {len(date_observed_df)}')
    if len(date_observed_df):
        print(f'  Linked to {date_observed_df["townmark_id"].nunique()} townmarks')
        gran_dist = date_observed_df['granularity'].value_counts()
        for g, c in gran_dist.items():
            print(f'  {g}: {c}')
        errors = date_observed_df[date_observed_df['date'].isna()]
        if len(errors):
            print(f'  Date construction errors: {len(errors)}')

    # ======================================================================
    # 8.6 Valuation Assembly
    # ======================================================================
    val_rows = []
    next_val_id = 1
    for _, pm in townmarks_df.iterrows():
        src = listings.loc[pm['source_listing_idx']]
        tiers = src['valuation_tiers']

        for pos_0, tier_str in enumerate(tiers):
            amount = None
            if tier_str is not None:
                # Strip commas, parse as float
                try:
                    amount = float(tier_str.replace(',', ''))
                except (ValueError, AttributeError):
                    amount = None  # unparseable -- leave null

            val_rows.append({
                'valuation_id': next_val_id,
                'townmark_id': pm['townmark_id'],
                'amount': amount,
                'appraisal_position': pos_0 + 1,  # 1-based
                'appraisal_date': None,  # catalog publication date; TBD
            })
            next_val_id += 1
    townmark_valuation_df = pd.DataFrame(val_rows) if val_rows else pd.DataFrame(
        columns=['valuation_id', 'townmark_id', 'amount',
                 'appraisal_position', 'appraisal_date']
    )
    print(f'TownmarkValuation rows: {len(townmark_valuation_df)}')
    if len(townmark_valuation_df):
        print(f'  Linked to {townmark_valuation_df["townmark_id"].nunique()} postmarks')
        pos_dist = townmark_valuation_df['appraisal_position'].value_counts().sort_index()
        for pos, c in pos_dist.items():
            print(f'  Position {pos}: {c}')
        unpriced = townmark_valuation_df['amount'].isna().sum()
        print(f'  Unpriced (null amount): {unpriced}')

    # ======================================================================
    # 8.8 PostOffice Normalization
    # ======================================================================
    _apostrophe_re = re.compile(r"[\u2019']")  # straight + curly apostrophe
    _amp_re        = re.compile(r"\s*&\s*")
    _strip_punct   = re.compile(r"[,/=()\[\]:`*]")
    _double_dash   = re.compile(r"-{2,}")
    _multi_space   = re.compile(r"\s+")
    _edge_trim     = re.compile(r"^[\s.\-]+|[\s.,\-]+$")
    listings['normalized_town'] = (
        listings['resolved_town'].astype('string')
        .str.upper()
        .str.replace(_apostrophe_re, '', regex=True)            # BARNETT'S -> BARNETTS
        .str.replace(_amp_re, ' AND ', regex=True)              # B&O -> B AND O
        .str.replace(_strip_punct, ' ', regex=True)             # , / = ( ) -> space
        .str.replace(_double_dash, '-', regex=True)             # -- -> -
        .str.replace(_multi_space, ' ', regex=True)             # collapse spaces
        .str.replace(_edge_trim, '', regex=True)                # trim edges
        .replace('', pd.NA)
    )
    listings['state_code'] = pd.Series(
        REGION_ABBREV, index=listings.index, dtype='string'
    )
    post_offices_df = (
        listings[['state_code', 'normalized_town']]
        .dropna(subset=['normalized_town'])
        .drop_duplicates()
        .sort_values(['state_code', 'normalized_town'], na_position='first')
        .reset_index(drop=True)
    )
    post_offices_df.insert(0, 'post_office_id', post_offices_df.index + 1)
    post_offices_df['name'] = post_offices_df['normalized_town']
    post_offices_df = post_offices_df[['post_office_id', 'name', 'state_code']]
    po_id_by_key = {
        (_nkey(sc), nt): pid
        for pid, nt, sc in post_offices_df[['post_office_id', 'name', 'state_code']].itertuples(index=False)
    }
    listings['post_office_id'] = [
        po_id_by_key.get((_nkey(sc), nt)) if pd.notna(nt) else None
        for sc, nt in zip(listings['state_code'], listings['normalized_town'])
    ]
    townmarks_df['post_office_id'] = townmarks_df['source_listing_idx'].map(
        listings['post_office_id']
    )
    unresolved = townmarks_df['post_office_id'].isna()
    if unresolved.any():
        unresolved_states = (
            townmarks_df.loc[unresolved, 'source_listing_idx']
            .map(listings['state_code'])
        )
        distinct_states = unresolved_states.unique()
        next_po_id = int(post_offices_df['post_office_id'].max() or 0) + 1
        unknown_rows = []
        unknown_by_state = {}
        for sc in distinct_states:
            unknown_rows.append({
                'post_office_id': next_po_id,
                'name': 'UNKNOWN',
                'state_code': sc,
            })
            unknown_by_state[_nkey(sc)] = next_po_id
            next_po_id += 1
        post_offices_df = pd.concat(
            [post_offices_df, pd.DataFrame(unknown_rows)],
            ignore_index=True,
        )
        townmarks_df.loc[unresolved, 'post_office_id'] = unresolved_states.map(
            lambda sc: unknown_by_state[_nkey(sc)]
        ).values
        print(f'  Assigned {unresolved.sum()} townmarks to UNKNOWN post office(s) across {len(unknown_by_state)} state(s)')
    # One junction row per distinct (post office, section region) pair: a
    # post office listed under several catalog sections (e.g. Detroit under
    # Northwest Territory, Indiana Territory, Michigan Territory, and
    # statehood) links to each of those regions. POs with no listing-derived
    # pair (the UNKNOWN fallbacks) get the catalog default region.
    _po_region_pairs = (
        listings[['post_office_id', 'section_region_id']]
        .dropna()
        .astype(int)
        .drop_duplicates()
        .rename(columns={'section_region_id': 'region_id'})
    )
    _covered_pos = set(_po_region_pairs['post_office_id'])
    _uncovered = [
        {'post_office_id': int(pid), 'region_id': REGION_ID}
        for pid in post_offices_df['post_office_id']
        if int(pid) not in _covered_pos
    ]
    if _uncovered:
        _po_region_pairs = pd.concat(
            [_po_region_pairs, pd.DataFrame(_uncovered)], ignore_index=True
        )
    _po_region_pairs = (
        _po_region_pairs
        .sort_values(['post_office_id', 'region_id'])
        .reset_index(drop=True)
    )
    post_office_regions_df = pd.DataFrame({
        'post_office_region_id': range(1, len(_po_region_pairs) + 1),
        'post_office_id': _po_region_pairs['post_office_id'].astype(int).values,
        'region_id': _po_region_pairs['region_id'].astype(int).values,
    })
    print(f'PostOffice records: {len(post_offices_df)}')
    print(f'  From {listings["resolved_town"].nunique()} raw distinct towns')
    print(f'  To {len(post_offices_df)} normalized post offices')
    print(f'PostOfficeRegion links: {len(post_office_regions_df)} '
          f'across {post_office_regions_df["region_id"].nunique()} region(s)')
    if post_offices_df['state_code'].notna().any():
        per_state = post_offices_df.groupby('state_code').size()
        print(f'  Per state:')
        for sc, n in per_state.items():
            print(f'    {sc}: {n} post offices')
    print()
    raw_to_norm = (
        listings.groupby(['state_code', 'normalized_town'], dropna=False)['resolved_town']
        .apply(lambda x: list(x.unique()))
    )
    collapsed = raw_to_norm[raw_to_norm.apply(len) > 1]
    if len(collapsed):
        print(f'Towns collapsed by normalization: {len(collapsed)}')
        for (sc, norm), variants in list(collapsed.items())[:20]:
            tag = f'[{sc}] ' if pd.notna(sc) else ''
            print(f'  {tag}{norm!r} <- {variants}')
    else:
        print('No normalization collapses (all raw towns map 1:1).')
    print()
    missing_po = townmarks_df['post_office_id'].isna().sum()
    if missing_po:
        print(f'WARNING: {missing_po} townmarks missing post_office_id')
    else:
        print('All townmarks have a post_office_id.')
    _names = post_offices_df['name'].astype('string').fillna('')
    _bad_mask = ~_names.str.fullmatch(r"[A-Z][A-Z .\-]*[A-Z.]", na=False)
    _bad_mask &= ~_names.str.fullmatch(r"[A-Z]", na=False)
    _bad = post_offices_df[_bad_mask]
    if len(_bad):
        sample = _bad[['post_office_id', 'name', 'state_code']].head(30).to_string(index=False)
        raise AssertionError(
            f'PostOffice normalization produced {len(_bad)} name(s) with '
            f'characters outside [A-Z, space, period, single dash]. '
            f'First {min(30, len(_bad))}:\n{sample}\n\n'
            f'Common causes:\n'
            f'  - Manuscript-section row whose date token did not match\n'
            f'    MANUSCRIPT_DATE_RE in the Step 2.5 overlay (extend regex).\n'
            f'  - Source character not handled by the normalization rules\n'
            f'    above (add a replacement).\n'
        )

    # ======================================================================
    # 8.9 Assembly Confidence
    # ======================================================================
    def compute_assembly_warnings(pm_row):
        """Collect all warnings applicable to a single townmark."""
        src = listings.loc[pm_row['source_listing_idx']]
        warnings = list(src.get('s7_warnings', []))

        # Step 1 confidence
        if src.get('confidence') == 'low':
            warnings.append(f'low_classification_confidence:{src.get("reason")}')

        # Multi-color ambiguity
        if pm_row['is_multi_color_fanout']:
            warnings.append('multi_color_fanout')

        # Shape fallback
        if src.get('shape_source') == 'catalog_fallback':
            warnings.append('shape_from_catalog_fallback')

        # Shape resolution error
        if pd.notna(src.get('shape_error')):
            warnings.append(f'shape_error:{src["shape_error"]}')

        # Missing dates
        if not src['parsed_dates']:
            warnings.append('no_dates_parsed')

        # Multiple size fields (ambiguous dimensions)
        if len(src['parsed_sizes']) > 1:
            warnings.append(f'multiple_size_fields:{len(src["parsed_sizes"])}')

        # Unresolved other fields
        if src['other_fields']:
            warnings.append(f'unresolved_other_fields:{len(src["other_fields"])}')

        return warnings
    townmarks_df['s8_warnings'] = townmarks_df.apply(
        compute_assembly_warnings, axis=1
    )
    townmarks_df['assembly_confidence'] = townmarks_df['s8_warnings'].apply(confidence_level)
    print('Assembly confidence distribution:')
    for level, count in townmarks_df['assembly_confidence'].value_counts().items():
        print(f'  {level}: {count}')
    print()
    all_warnings = [w for wlist in townmarks_df['s8_warnings'] for w in wlist]
    if all_warnings:
        warn_counts = pd.Series(all_warnings).value_counts()
        print(f'Warning frequency ({len(all_warnings)} total):')
        for w, c in warn_counts.items():
            print(f'  {w}: {c}')
    else:
        print('No warnings -- all townmarks at HIGH confidence.')

    # ======================================================================
    # 8.10 Step 8 Summary
    # ======================================================================
    print('=' * 60)
    print('Step 8: Townmark Assembly -- Final Summary')
    print('=' * 60)
    print()
    print(f'Input listings:           {len(listings)}')
    print(f'Output townmarks:         {len(townmarks_df)}')
    print(f'  (expansion from color fan-out: +{len(townmarks_df) - len(listings)})')
    print()
    print('Domain entity tables:')
    print(f'  townmarks_df:           {len(townmarks_df)} rows')
    print(f'  date_observed_df:       {len(date_observed_df)} rows')
    print(f'  townmark_valuation_df:  {len(townmark_valuation_df)} rows')
    print(f'  post_offices_df:        {len(post_offices_df)} rows')
    print()
    print('Value/lookup tables:')
    print(f'  colors_df:              {len(colors_df)} rows')
    print(f'  shapes_df:              {len(shapes_df)} rows')
    print()
    print('Confidence distribution:')
    for level in ['HIGH', 'MEDIUM', 'LOW']:
        count = (townmarks_df['assembly_confidence'] == level).sum()
        pct = count / len(townmarks_df) * 100
        print(f'  {level}: {count} ({pct:.1f}%)')
    print()
    print('FK integrity checks:')
    orphan_colors = townmarks_df[
        townmarks_df['color_id'].notna() &
        ~townmarks_df['color_id'].isin(colors_df['color_id'])
    ]
    print(f'  Townmarks with invalid color_id: {len(orphan_colors)}')
    orphan_shapes = townmarks_df[
        townmarks_df['shape_id'].notna() &
        ~townmarks_df['shape_id'].isin(shapes_df['shape_id'])
    ]
    print(f'  Townmarks with invalid shape_id: {len(orphan_shapes)}')
    orphan_po = townmarks_df[
        townmarks_df['post_office_id'].notna() &
        ~townmarks_df['post_office_id'].isin(post_offices_df['post_office_id'])
    ]
    print(f'  Townmarks with invalid post_office_id: {len(orphan_po)}')
    orphan_dates = date_observed_df[
        ~date_observed_df['townmark_id'].isin(townmarks_df['townmark_id'])
    ] if len(date_observed_df) else pd.DataFrame()
    print(f'  DateObserved with invalid townmark_id: {len(orphan_dates)}')
    orphan_vals = townmark_valuation_df[
        ~townmark_valuation_df['townmark_id'].isin(townmarks_df['townmark_id'])
    ] if len(townmark_valuation_df) else pd.DataFrame()
    print(f'  TownmarkValuation with invalid townmark_id: {len(orphan_vals)}')

    # ======================================================================
    # 8.11 Sample Inspection
    # ======================================================================
    def inspect_townmark(pm_id):
        """Print full assembled state for a single townmark."""
        pm = townmarks_df[townmarks_df['townmark_id'] == pm_id].iloc[0]
        print(f'=== Townmark {pm_id} ===')
        print(f'  catalog_text:    {pm["catalog_text"][:100]}')
        print(f'  inscription:     {pm["inscription_text"]}')
        print(f'  is_manuscript:   {pm["is_manuscript"]}')

        shape_name = '(null)'
        if pd.notna(pm['shape_id']):
            shape_name = shapes_df.loc[shapes_df['shape_id'] == pm['shape_id'], 'name'].iloc[0]
        print(f'  shape:           {shape_name}')

        color_name = pm.get('color_name', '(null)')
        print(f'  color:           {color_name}')
        print(f'  dimensions:      {pm["width"]}x{pm["height"]}')
        print(f'  date_fmt:     {pm["date_format"]}')
        print(f'  impression:      {pm["impression"]}')
        print(f'  is_irreg:    {pm["is_irregular"]}')

        po_name = '(null)'
        if pd.notna(pm['post_office_id']):
            po_name = post_offices_df.loc[
                post_offices_df['post_office_id'] == pm['post_office_id'], 'name'
            ].iloc[0]
        print(f'  post_office:     {po_name}')

        parent_pm = pm.get('parent_townmark_id')
        print(f'  parent_pm_id:    {parent_pm}')

        print(f'  confidence:      {pm["assembly_confidence"]}')
        if pm['s8_warnings']:
            print(f'  warnings:        {pm["s8_warnings"]}')

        # DateObserved
        dates = date_observed_df[date_observed_df['townmark_id'] == pm_id]
        print(f'  dates ({len(dates)}):')
        for _, d in dates.iterrows():
            print(f'    {d["date"]} ({d["granularity"]})')

        # Valuations
        vals = townmark_valuation_df[townmark_valuation_df['townmark_id'] == pm_id]
        print(f'  valuations ({len(vals)}):')
        for _, v in vals.iterrows():
            print(f'    pos {v["appraisal_position"]}: {v["amount"]}')
        print()
    inspect_townmark(1)
    multi_color = townmarks_df[townmarks_df['is_multi_color_fanout']]
    if len(multi_color):
        first_mc_src = multi_color['source_listing_idx'].iloc[0]
        siblings = townmarks_df[townmarks_df['source_listing_idx'] == first_mc_src]
        print(f'--- Multi-color siblings from listing idx {first_mc_src} ---')
        for _, sib in siblings.iterrows():
            inspect_townmark(sib['townmark_id'])
    ms_entries = townmarks_df[townmarks_df['is_manuscript']]
    if len(ms_entries):
        inspect_townmark(ms_entries['townmark_id'].iloc[0])
    inherited = townmarks_df[townmarks_df['parent_townmark_id'].notna()]
    if len(inherited):
        sample = inherited.iloc[0]
        print(f'--- Inherited townmark (child -> parent) ---')
        inspect_townmark(int(sample['townmark_id']))
        print(f'  Parent townmark:')
        inspect_townmark(int(sample['parent_townmark_id']))

    # ======================================================================
    # 9.1 Rate Amount Parsing
    # ======================================================================
    assert parse_rate_amount('5') == (5.0, False)
    assert parse_rate_amount('25') == (25.0, False)
    assert parse_rate_amount('12-1/2') == (12.5, False)
    assert parse_rate_amount('6-1/4') == (6.25, False)
    assert parse_rate_amount('1/2') == (0.5, False)
    assert parse_rate_amount('V') == (5.0, True)
    assert parse_rate_amount('X') == (10.0, True)
    assert parse_rate_amount(None) == (None, False)
    print('Rate amount parser self-tests passed')

    # ======================================================================
    # 9.2 Bracket Shape Resolution
    # ======================================================================
    def resolve_bracket(bracket_text):
        """Resolve a bracket descriptor into shape/lettering/dimension components.
        Returns dict with keys: shape_name, lettering_name, width, height, qualifier.
        """
        if not bracket_text:
            return {'shape_name': None, 'lettering_name': None,
                    'width': None, 'height': None, 'qualifier': None}

        text = bracket_text.strip()
        text_lower = text.lower()
        shape_name = None
        lettering_name = None
        width = None
        height = None
        qualifier = None

        # Direct shape match
        if shape_name is None:
            # Try the full text as a shape
            if text_lower in BRACKET_SHAPE_MAP:
                shape_name = BRACKET_SHAPE_MAP[text_lower]
            else:
                # Try each word against the shape map
                for word in text_lower.split():
                    if word in BRACKET_SHAPE_MAP:
                        shape_name = BRACKET_SHAPE_MAP[word]
                        break

        # Lettering: check full text and individual words against lettering lookup
        if text_lower in lettering_lookup:
            lettering_name = text_lower
        else:
            for word in re.split(r'[\s,]+', text_lower):
                if word in lettering_lookup:
                    lettering_name = word
                    break

        # Extract dimensions from bracket content
        dim_m = BRACKET_DIM_RE.search(text)
        if dim_m:
            width = float(dim_m.group(1))
            if dim_m.group(2):
                height = float(dim_m.group(2))

        # Anything not recognized as shape/lettering/dimension is a qualifier
        if shape_name is None and lettering_name is None and width is None:
            qualifier = text

        return {
            'shape_name': shape_name,
            'lettering_name': lettering_name,
            'width': width,
            'height': height,
            'qualifier': qualifier,
        }
    assert resolve_bracket('C')['shape_name'] == 'C'
    assert resolve_bracket('box')['shape_name'] == 'BOX'
    assert resolve_bracket('arc')['shape_name'] == 'ARC'
    assert resolve_bracket('octagon')['shape_name'] == 'Octagon'
    r = resolve_bracket('cogged circle')
    assert r['shape_name'] == 'C'
    r = resolve_bracket('octagon 23')
    assert r['shape_name'] == 'Octagon'
    assert r['width'] == 23.0
    r = resolve_bracket('30x23 rectangle')
    assert r['shape_name'] == 'BOX'
    assert r['width'] == 30.0
    assert r['height'] == 23.0
    assert resolve_bracket('hdstp rate')['qualifier'] == 'hdstp rate'
    assert resolve_bracket(None)['shape_name'] is None
    r = resolve_bracket('italics')
    assert r['lettering_name'] == 'italics'
    assert r['qualifier'] is None
    r = resolve_bracket('cross hatched letters')
    assert r['lettering_name'] is None  # no seed match
    assert r['qualifier'] == 'cross hatched letters'
    assert resolve_bracket('C')['lettering_name'] is None
    print('Bracket resolver self-tests passed')

    def resolve_bracket_shape_id(shape_code_or_name):
        # Bracket shape parsing returns ASCC codes such as "O" and "ARC".
        # shape_lookup is keyed by seed names such as "O - Oval".
        if not shape_code_or_name:
            return None
        raw = str(shape_code_or_name).strip()
        if not raw:
            return None
        direct_id = shape_lookup.get(raw.upper())
        if direct_id is not None:
            return direct_id
        shape_name, shape_error = resolve_shape_name(raw.upper())
        if shape_error is not None or not shape_name:
            return None
        return shape_lookup.get(shape_name.upper())

    assert resolve_bracket_shape_id('O') == shape_lookup['O - OVAL']
    assert resolve_bracket_shape_id(resolve_bracket('oval')['shape_name']) == shape_lookup['O - OVAL']
    assert resolve_bracket_shape_id(resolve_bracket('arc')['shape_name']) == shape_lookup['ARC - ARC OR SEMI-CIRCLE']
    assert resolve_bracket_shape_id(resolve_bracket('box')['shape_name']) == shape_lookup['BOX']

    # ======================================================================
    # 9.3 Token Classification & Entity Emission
    # ======================================================================
    ratemark_rows = []
    auxmark_rows = []
    tm_rm_rows = []
    next_rm_id = 1
    next_aux_id = 1
    next_tm_rm_id = 1
    pm_code_lookup = townmarks_df.set_index('townmark_id')['code'].to_dict()
    rm_counter_by_tm = {}      # {pm_id: next_rm_index}
    aux_counter_by_parent = {} # {(parent_type, parent_id): next_aux_index}
    pm_by_listing = townmarks_df.groupby('source_listing_idx')['townmark_id'].apply(list).to_dict()
    for listing_idx, row in listings.iterrows():
        townmark_ids = pm_by_listing.get(listing_idx, [])
        if not townmark_ids:
            continue

        # Flatten parsed_rates: list of lists -> flat list of tokens
        all_tokens = [tok for field_toks in row['parsed_rates'] for tok in field_toks]

        # Pre-fetch color for each townmark in this listing
        pm_color_map = {}
        for pm_id in townmark_ids:
            pm_color_map[pm_id] = townmarks_df.loc[
                townmarks_df['townmark_id'] == pm_id, 'color_id'
            ].iloc[0]

        for tok in all_tokens:
            kw = tok['rate_keyword']
            amt_raw = tok['rate_amount_raw']
            bracket = tok['rate_bracket']
            is_ms = tok['rate_is_manuscript']
            impression_override = tok.get('rate_impression')

            # Parse amount
            rate_value, is_roman = parse_rate_amount(amt_raw)
            has_amount = rate_value is not None

            # Resolve bracket -> shape/lettering
            br = resolve_bracket(bracket)
            bracket_shape_id = resolve_bracket_shape_id(br['shape_name'])
            bracket_lettering_id = lettering_lookup.get(br['lettering_name']) if br['lettering_name'] else None

            # Determine impression
            if is_ms:
                mark_impression = None
            elif impression_override:
                mark_impression = impression_override
            else:
                mark_impression = 'Normal'

            # Tokens with an amount: emit one Ratemark per townmark.
            # When the token also carries a keyword (PAID/FREE/STEAM/DUE) the
            # keyword is part of the inscription on the same handstamp, so it
            # is preserved in inscription_text and NO compound Auxmark is
            # emitted. Standalone keywords (no amount) still produce an
            # Auxmark parented to the Townmark -- see the elif branch below.
            if has_amount:
                for pm_id in townmark_ids:
                    pm_color = pm_color_map[pm_id]

                    # Inscription text: preserve raw token as inscribed, with
                    # the bracket qualifier stripped. Examples:
                    #   "3/PAID"          -> "3/PAID"
                    #   "PAID/3[C]"       -> "PAID/3"
                    #   "25[ms]"          -> "25"
                    #   "with 24"         -> "with 24"
                    if is_roman:
                        rm_inscription = amt_raw
                    else:
                        rm_inscription = RATE_BRACKET_RE.sub('', tok['rate_raw']).strip()
                        if not rm_inscription:
                            rm_inscription = amt_raw or ''

                    rm_id = next_rm_id
                    pm_code = pm_code_lookup.get(pm_id)
                    rm_idx = rm_counter_by_tm.get(pm_id, 0)
                    rm_code = f'{pm_code}/RM{rm_idx}' if pm_code else None
                    rm_counter_by_tm[pm_id] = rm_idx + 1
                    ratemark_rows.append({
                        'ratemark_id': rm_id,
                        'inscription_text': rm_inscription,
                        'rate_value': rate_value,
                        'is_manuscript': is_ms,
                        'shape_id': None if is_ms else bracket_shape_id,
                        'lettering_id': None if is_ms else bracket_lettering_id,
                        'color_id': pm_color,
                        'width': br['width'] if not is_ms else None,
                        'height': br['height'] if not is_ms else None,
                        'is_irregular': None if is_ms else False,
                        'impression': mark_impression,
                        'source_listing_idx': listing_idx,
                        'rate_raw': tok['rate_raw'],
                        'bracket_qualifier': br.get('qualifier'),
                        'code': rm_code,
                    })
                    next_rm_id += 1

                    # TownmarkRatemark junction
                    tm_rm_rows.append({
                        'townmark_ratemark_id': next_tm_rm_id,
                        'townmark_id': pm_id,
                        'ratemark_id': rm_id,
                        'placement_type': None,
                    })
                    next_tm_rm_id += 1

            # Standalone keyword (no amount): emit Auxmark per townmark
            elif kw:
                aux_inscription = kw
                if kw in ('PM_FREE', 'PM_FRANK'):
                    aux_inscription = 'FREE' if kw == 'PM_FREE' else 'FRANK'

                for pm_id in townmark_ids:
                    pm_color = pm_color_map[pm_id]

                    pm_code_standalone = pm_code_lookup.get(pm_id)
                    aux_parent_key_s = ('TOWNMARK', pm_id)
                    aux_idx_s = aux_counter_by_parent.get(aux_parent_key_s, 0)
                    aux_code_s = f'{pm_code_standalone}/AM{aux_idx_s}' if pm_code_standalone else None
                    aux_counter_by_parent[aux_parent_key_s] = aux_idx_s + 1

                    auxmark_rows.append({
                        'auxmark_id': next_aux_id,
                        'inscription_text': aux_inscription,
                        'parent_mark_type': 'TOWNMARK',
                        'parent_mark_id': pm_id,
                        'is_manuscript': False,
                        'shape_id': bracket_shape_id,
                        'lettering_id': bracket_lettering_id,
                        'color_id': pm_color,
                        'width': br['width'],
                        'height': br['height'],
                        'is_irregular': False,
                        'impression': mark_impression,
                        'source_listing_idx': listing_idx,
                        'code': aux_code_s,
                    })

                    next_aux_id += 1
    print(f'Token classification complete')
    print(f'  Ratemarks emitted: {len(ratemark_rows)}')
    print(f'  Auxmarks emitted: {len(auxmark_rows)}')
    print(f'  TownmarkRatemark junctions: {len(tm_rm_rows)}')

    # ======================================================================
    # 9.4 DataFrame Construction
    # ======================================================================
    ratemarks_df = pd.DataFrame(ratemark_rows) if ratemark_rows else pd.DataFrame(
        columns=['ratemark_id', 'inscription_text', 'rate_value', 'is_manuscript',
                 'shape_id', 'lettering_id', 'color_id', 'width', 'height',
                 'is_irregular', 'impression', 'source_listing_idx', 'rate_raw',
                 'bracket_qualifier']
    )
    auxmarks_df = pd.DataFrame(auxmark_rows) if auxmark_rows else pd.DataFrame(
        columns=['auxmark_id', 'inscription_text', 'parent_mark_type',
                 'parent_mark_id', 'is_manuscript', 'shape_id', 'lettering_id',
                 'color_id', 'width', 'height', 'is_irregular', 'impression',
                 'source_listing_idx']
    )
    townmark_ratemark_df = pd.DataFrame(tm_rm_rows) if tm_rm_rows else pd.DataFrame(
        columns=['townmark_ratemark_id', 'townmark_id', 'ratemark_id',
                 'placement_type']
    )
    print('DataFrames constructed:')
    print(f'  ratemarks_df:          {len(ratemarks_df)} rows')
    print(f'  auxmarks_df:           {len(auxmarks_df)} rows')
    print(f'  townmark_ratemark_df:  {len(townmark_ratemark_df)} rows')

    # ======================================================================
    # 9.5 Value Distributions
    # ======================================================================
    if len(ratemarks_df):
        print('Ratemark distributions:')
        print(f'  Total ratemarks: {len(ratemarks_df)}')
        print(f'  Manuscript ratemarks: {ratemarks_df["is_manuscript"].sum()}')
        print(f'  With shape: {ratemarks_df["shape_id"].notna().sum()}')
        print(f'  With bracket qualifier (unresolved): {ratemarks_df["bracket_qualifier"].notna().sum()}')
        print()

        # Rate value distribution
        rate_vals = ratemarks_df['rate_value'].dropna()
        if len(rate_vals):
            print(f'  Rate value range: {rate_vals.min():.1f} - {rate_vals.max():.1f} cents')
            print(f'  Most common values:')
            for val, count in rate_vals.value_counts().head(10).items():
                print(f'    {val:.1f}: {count}')
            print()

        # Shape distribution on ratemarks
        rm_shape_dist = ratemarks_df['shape_id'].value_counts(dropna=False)
        print('  Shape distribution:')
        for sid, count in rm_shape_dist.items():
            if pd.isna(sid):
                print(f'    (null): {count}')
            else:
                name = shapes_df.loc[shapes_df['shape_id'] == sid, 'name'].iloc[0]
                print(f'    {name}: {count}')
        print()

        # Lettering distribution on ratemarks
        rm_lettering_hits = ratemarks_df['lettering_id'].notna().sum()
        print(f'  Lettering assigned: {rm_lettering_hits}')
        if rm_lettering_hits:
            for lid, count in ratemarks_df['lettering_id'].value_counts(dropna=True).items():
                name = letterings_df.loc[letterings_df['lettering_id'] == lid, 'name'].iloc[0]
                print(f'    {name}: {count}')
        print()
    if len(auxmarks_df):
        print('Auxmark distributions:')
        print(f'  Total auxmarks: {len(auxmarks_df)}')
        print(f'  Parented to Townmark: {(auxmarks_df["parent_mark_type"] == "TOWNMARK").sum()}')
        print(f'  Parented to Ratemark: {(auxmarks_df["parent_mark_type"] == "RATEMARK").sum()}')
        print()

        # Inscription text distribution
        print('  Inscription text distribution:')
        for text, count in auxmarks_df['inscription_text'].value_counts().items():
            print(f'    {text}: {count}')
        print()

        # Lettering distribution on auxmarks
        aux_lettering_hits = auxmarks_df['lettering_id'].notna().sum()
        print(f'  Lettering assigned: {aux_lettering_hits}')
        if aux_lettering_hits:
            for lid, count in auxmarks_df['lettering_id'].value_counts(dropna=True).items():
                name = letterings_df.loc[letterings_df['lettering_id'] == lid, 'name'].iloc[0]
                print(f'    {name}: {count}')
        print()
    if len(townmark_ratemark_df):
        print('TownmarkRatemark junction:')
        print(f'  Total rows: {len(townmark_ratemark_df)}')
        print(f'  Distinct townmarks linked: {townmark_ratemark_df["townmark_id"].nunique()}')
        print(f'  Distinct ratemarks linked: {townmark_ratemark_df["ratemark_id"].nunique()}')
        rms_per_tm = townmark_ratemark_df.groupby('townmark_id').size()
        print(f'  Ratemarks per townmark: min={rms_per_tm.min()}, max={rms_per_tm.max()}, mean={rms_per_tm.mean():.1f}')

    # ======================================================================
    # 9.6 FK Integrity Checks
    # ======================================================================
    print('Step 9 FK integrity checks:')
    if len(ratemarks_df):
        orphan = ratemarks_df[
            ratemarks_df['shape_id'].notna() &
            ~ratemarks_df['shape_id'].isin(shapes_df['shape_id'])
        ]
        print(f'  Ratemarks with invalid shape_id: {len(orphan)}')
    if len(ratemarks_df):
        orphan = ratemarks_df[
            ratemarks_df['color_id'].notna() &
            ~ratemarks_df['color_id'].isin(colors_df['color_id'])
        ]
        print(f'  Ratemarks with invalid color_id: {len(orphan)}')
    if len(auxmarks_df):
        pm_auxmarks = auxmarks_df[auxmarks_df['parent_mark_type'] == 'TOWNMARK']
        orphan_pm = pm_auxmarks[~pm_auxmarks['parent_mark_id'].isin(townmarks_df['townmark_id'])]
        print(f'  Auxmarks with invalid Townmark parent: {len(orphan_pm)}')

        rm_auxmarks = auxmarks_df[auxmarks_df['parent_mark_type'] == 'RATEMARK']
        if len(ratemarks_df):
            orphan_rm = rm_auxmarks[~rm_auxmarks['parent_mark_id'].isin(ratemarks_df['ratemark_id'])]
        else:
            orphan_rm = rm_auxmarks
        print(f'  Auxmarks with invalid Ratemark parent: {len(orphan_rm)}')
    if len(auxmarks_df):
        orphan = auxmarks_df[
            auxmarks_df['shape_id'].notna() &
            ~auxmarks_df['shape_id'].isin(shapes_df['shape_id'])
        ]
        print(f'  Auxmarks with invalid shape_id: {len(orphan)}')
    if len(townmark_ratemark_df):
        orphan = townmark_ratemark_df[
            ~townmark_ratemark_df['townmark_id'].isin(townmarks_df['townmark_id'])
        ]
        print(f'  TownmarkRatemark with invalid townmark_id: {len(orphan)}')
    if len(townmark_ratemark_df) and len(ratemarks_df):
        orphan = townmark_ratemark_df[
            ~townmark_ratemark_df['ratemark_id'].isin(ratemarks_df['ratemark_id'])
        ]
        print(f'  TownmarkRatemark with invalid ratemark_id: {len(orphan)}')

    # ======================================================================
    # 9.7 Step 9 Summary
    # ======================================================================
    print('=' * 60)
    print('Step 9: Ratemark & Auxmark Assembly -- Final Summary')
    print('=' * 60)
    print()
    print(f'Input: {len(listings)} listings with {sum(len(t) for rlist in listings["parsed_rates"] for t in rlist)} rate tokens')
    print()
    print('New domain entity tables:')
    print(f'  ratemarks_df:          {len(ratemarks_df)} rows')
    print(f'  auxmarks_df:           {len(auxmarks_df)} rows')
    print(f'  townmark_ratemark_df:  {len(townmark_ratemark_df)} rows')
    print()
    listings_with_rates = listings[listings['parsed_rates'].apply(
        lambda rlist: any(len(toks) > 0 for toks in rlist)
    )].index
    listings_with_ratemarks = set(ratemarks_df['source_listing_idx']) if len(ratemarks_df) else set()
    listings_with_auxmarks = set(auxmarks_df['source_listing_idx']) if len(auxmarks_df) else set()
    print(f'Listings with rate tokens: {len(listings_with_rates)}')
    print(f'Listings producing ratemarks: {len(listings_with_ratemarks)}')
    print(f'Listings producing auxmarks: {len(listings_with_auxmarks)}')
    print(f'Listings with no rate/aux output: {len(listings) - len(listings_with_ratemarks | listings_with_auxmarks)}')

    # ======================================================================
    # 9.8 DateSeen (Marking-Scoped)
    # ======================================================================
    _tm_codes_by_lst = _tm_codes_by_listing(townmarks_df)
    # Ratemarks and auxmarks inherit dates from their parent townmark by
    # default; emit dates_seen rows keyed by their codes too. Both frames
    # carry source_listing_idx + code, so the same helper works.
    _rm_codes_by_lst = _tm_codes_by_listing(ratemarks_df)
    _ax_codes_by_lst = _tm_codes_by_listing(auxmarks_df)
    ds_rows = []  # (marking_code, date, granularity)
    for listing_idx, src in listings.iterrows():
        tm_codes = [c for c in _tm_codes_by_lst.get(listing_idx, []) if c]
        rm_codes = [c for c in _rm_codes_by_lst.get(listing_idx, []) if c]
        ax_codes = [c for c in _ax_codes_by_lst.get(listing_idx, []) if c]
        all_codes = tm_codes + rm_codes + ax_codes
        if not all_codes:
            continue
        for d in (src.get('parsed_dates') or []):
            gran = d.get('date_granularity')
            obs_rows = []
            try:
                if gran == 'DAY':
                    obs = _date_cls(d['date_year_start'], d['date_month'], d['date_day'])
                    obs_rows.append((str(obs), 'DAY'))
                elif gran == 'MONTH':
                    obs = _date_cls(d['date_year_start'], d['date_month'], 1)
                    obs_rows.append((str(obs), 'MONTH'))
                elif gran == 'YEAR':
                    obs = _date_cls(d['date_year_start'], 1, 1)
                    obs_rows.append((str(obs), 'YEAR'))
                elif gran in ('RANGE', 'DECADE'):
                    for yr in (d['date_year_start'], d['date_year_end']):
                        obs = _date_cls(int(yr), 1, 1)
                        obs_rows.append((str(obs), 'YEAR'))
            except (ValueError, TypeError, KeyError):
                # Bad date components in source; Step 6 already reports parse errors.
                continue
            for obs_str, out_gran in obs_rows:
                for mc in all_codes:
                    ds_rows.append({
                        'marking_code': mc,
                        'date': obs_str,
                        'granularity': out_gran,
                    })
    dates_seen_df = pd.DataFrame(ds_rows) if ds_rows else pd.DataFrame(
        columns=['marking_code', 'date', 'granularity']
    )
    print(f'dates_seen_df: {len(dates_seen_df)} rows (subject_type=MARKING)')

    # ======================================================================
    # 9.10 Sample Inspection
    # ======================================================================
    def inspect_ratemark(rm_id):
        """Print full assembled state for a single ratemark."""
        rm = ratemarks_df[ratemarks_df['ratemark_id'] == rm_id].iloc[0]
        print(f'  === Ratemark {rm_id} ===')
        print(f'    inscription:   {rm["inscription_text"]}')
        print(f'    rate_value:    {rm["rate_value"]}')
        print(f'    is_manuscript: {rm["is_manuscript"]}')

        shape_name = '(null)'
        if pd.notna(rm.get('shape_id')):
            shape_name = shapes_df.loc[shapes_df['shape_id'] == rm['shape_id'], 'name'].iloc[0]
        print(f'    shape:         {shape_name}')

        color_name = '(null)'
        if pd.notna(rm.get('color_id')):
            color_name = colors_df.loc[colors_df['color_id'] == rm['color_id'], 'name'].iloc[0]
        print(f'    color:         {color_name}')
        print(f'    impression:    {rm["impression"]}')
        print(f'    raw:           {rm["rate_raw"]}')

        # Auxmarks parented to this ratemark
        child_aux = auxmarks_df[
            (auxmarks_df['parent_mark_type'] == 'RATEMARK') &
            (auxmarks_df['parent_mark_id'] == rm_id)
        ]
        if len(child_aux):
            for _, ax in child_aux.iterrows():
                aux_color = '(null)'
                if pd.notna(ax.get('color_id')):
                    aux_color = colors_df.loc[colors_df['color_id'] == ax['color_id'], 'name'].iloc[0]
                print(f'    -> Auxmark {ax["auxmark_id"]}: {ax["inscription_text"]} [{aux_color}]')
        print()
    def inspect_townmark_rates(pm_id):
        """Print all rate/aux associations for a townmark."""
        pm = townmarks_df[townmarks_df['townmark_id'] == pm_id].iloc[0]
        color_name = '(null)'
        if pd.notna(pm.get('color_id')):
            color_name = colors_df.loc[colors_df['color_id'] == pm['color_id'], 'name'].iloc[0]
        print(f'Townmark {pm_id}: {pm["catalog_text"][:100]}')
        print(f'  inscription: {pm["inscription_text"]}')
        print(f'  color:       {color_name}')
        print()

        # Linked ratemarks
        links = townmark_ratemark_df[townmark_ratemark_df['townmark_id'] == pm_id]
        if len(links):
            print(f'  Linked ratemarks ({len(links)}):')
            for _, lnk in links.iterrows():
                inspect_ratemark(lnk['ratemark_id'])
        else:
            print('  No linked ratemarks')

        # Direct auxmarks (parented to this townmark)
        direct_aux = auxmarks_df[
            (auxmarks_df['parent_mark_type'] == 'TOWNMARK') &
            (auxmarks_df['parent_mark_id'] == pm_id)
        ]
        if len(direct_aux):
            print(f'  Direct auxmarks ({len(direct_aux)}):')
            for _, ax in direct_aux.iterrows():
                shape_name = '(null)'
                if pd.notna(ax.get('shape_id')):
                    shape_name = shapes_df.loc[shapes_df['shape_id'] == ax['shape_id'], 'name'].iloc[0]
                aux_color = '(null)'
                if pd.notna(ax.get('color_id')):
                    aux_color = colors_df.loc[colors_df['color_id'] == ax['color_id'], 'name'].iloc[0]
                print(f'    Auxmark {ax["auxmark_id"]}: {ax["inscription_text"]} [{shape_name}] [{aux_color}]')
        print()
    if len(townmark_ratemark_df):
        # Pick a townmark that has both ratemarks and direct auxmarks
        pms_with_rm = set(townmark_ratemark_df['townmark_id'])
        pms_with_aux = set(auxmarks_df.loc[
            auxmarks_df['parent_mark_type'] == 'TOWNMARK', 'parent_mark_id'
        ]) if len(auxmarks_df) else set()
        both = pms_with_rm & pms_with_aux
        if both:
            sample_pm = sorted(both)[0]
            print('--- Sample: Townmark with both ratemarks and auxmarks ---')
            inspect_townmark_rates(sample_pm)

        # 2. A townmark with only auxmarks (keyword-only tokens)
        aux_only = pms_with_aux - pms_with_rm
        if aux_only:
            sample_pm = sorted(aux_only)[0]
            print('--- Sample: Townmark with auxmarks only ---')
            inspect_townmark_rates(sample_pm)

        # 3. A ratemark with compound bracket (if any exist)
        if len(ratemarks_df):
            bracket_rms = ratemarks_df[ratemarks_df['bracket_qualifier'].notna()]
            if len(bracket_rms):
                print('--- Sample: Ratemark with unresolved bracket qualifier ---')
                inspect_ratemark(bracket_rms['ratemark_id'].iloc[0])

    # ======================================================================
    # Step 10: Output
    # ======================================================================
    os.makedirs(OUT_DIR, exist_ok=True)
    _emitted_listing_idxs = set()
    for _frame in (townmarks_df, ratemarks_df, auxmarks_df):
        if _frame is not None and len(_frame) and "source_listing_idx" in _frame.columns:
            _emitted_listing_idxs.update(_frame["source_listing_idx"].dropna().astype(int).tolist())
    _expected = set(listings.index.tolist())
    _missing = sorted(_expected - _emitted_listing_idxs)
    if _missing:
        head = _missing[:20]
        raise AssertionError(
            f"Listing-coverage check failed: {len(_missing)} listing(s) produced no "
            f"marking rows. First {len(head)}: {head}"
            + (" ..." if len(_missing) > len(head) else "")
        )
    print(f"Coverage check: all {len(listings)} listings emitted at least one marking.")
    AUDIT_TS = os.environ.get("ASCC_AUDIT_TS") or pd.Timestamp.now(tz="UTC").isoformat(timespec="microseconds")
    def _stamp(frame):
        out = frame.copy()
        out["created_date"]  = AUDIT_TS
        out["modified_date"] = AUDIT_TS
        out["created_by"]    = AUDIT_USER_ID
        out["modified_by"]   = AUDIT_USER_ID
        return out

    tm_idx_by_listing = _by_listing(townmarks_df, "townmark_id") if (townmarks_df is not None and "townmark_id" in townmarks_df.columns) else {}
    rm_idx_by_listing = _by_listing(ratemarks_df, "ratemark_id") if (ratemarks_df is not None and "ratemark_id" in ratemarks_df.columns) else {}
    ax_idx_by_listing = _by_listing(auxmarks_df, "auxmark_id")  if (auxmarks_df  is not None and "auxmark_id"  in auxmarks_df.columns)  else {}
    marking_id_by_tm = {}
    marking_id_by_rm = {}
    marking_id_by_ax = {}
    emit_order = []  # list of ("TM"|"RM"|"AX", source_id, marking_id) in emission order
    _next_marking_id = 1
    # Iterate listings.index, not range(len(listings)): the index can be
    # non-contiguous (gaps and trailing values past len), and source_listing_idx
    # on townmark/ratemark/auxmark frames is set from the actual index.
    for listing_idx in listings.index:
        for pm_id in tm_idx_by_listing.get(listing_idx, []):
            marking_id_by_tm[pm_id] = _next_marking_id
            emit_order.append(("TM", pm_id, _next_marking_id))
            _next_marking_id += 1
        for rm_id in rm_idx_by_listing.get(listing_idx, []):
            marking_id_by_rm[rm_id] = _next_marking_id
            emit_order.append(("RM", rm_id, _next_marking_id))
            _next_marking_id += 1
        for ax_id in ax_idx_by_listing.get(listing_idx, []):
            marking_id_by_ax[ax_id] = _next_marking_id
            emit_order.append(("AX", ax_id, _next_marking_id))
            _next_marking_id += 1
    print(f"Assigned marking ids: 1..{_next_marking_id - 1} ({len(emit_order)} markings)")
    _po_src = post_offices_df.reset_index(drop=True).copy()
    _po_src.insert(0, "id", range(1, len(_po_src) + 1))
    po_id_by_internal = dict(zip(_po_src["post_office_id"], _po_src["id"]))
    _colors_src = colors_df.reset_index(drop=True).copy()
    _colors_src.insert(0, "id", range(1, len(_colors_src) + 1))
    color_id_by_internal = dict(zip(_colors_src["color_id"], _colors_src["id"])) if "color_id" in _colors_src.columns else {}
    _letterings_src = letterings_df.reset_index(drop=True).copy()
    _letterings_src.insert(0, "id", range(1, len(_letterings_src) + 1))
    lettering_id_by_internal = dict(zip(_letterings_src["lettering_id"], _letterings_src["id"])) if "lettering_id" in _letterings_src.columns else {}
    _shapes_src = shapes_df.reset_index(drop=True).copy()
    _shapes_src.insert(0, "id", range(1, len(_shapes_src) + 1))
    shape_id_by_internal = dict(zip(_shapes_src["shape_id"], _shapes_src["id"])) if "shape_id" in _shapes_src.columns else {}
    colors_out = pd.DataFrame({
        "id": _colors_src["id"],
        "name": _colors_src["name"],
        "hex_val": _colors_src["hex_val"] if "hex_val" in _colors_src.columns else None,
        "pantone_code": _colors_src["pantone_code"] if "pantone_code" in _colors_src.columns else None,
    })
    colors_out = _stamp(colors_out)
    letterings_out = pd.DataFrame({
        "id": _letterings_src["id"],
        "name": _letterings_src["name"],
    })
    letterings_out = _stamp(letterings_out)
    shapes_out = pd.DataFrame({
        "id": _shapes_src["id"],
        "name": _shapes_src["name"],
        "code": pd.NA,
    })
    shapes_out = _stamp(shapes_out)
    post_offices_out = pd.DataFrame({
        "id": _po_src["id"],
        "name": _po_src["name"],
    })
    post_offices_out = _stamp(post_offices_out)
    _por_src = post_office_regions_df.copy()
    _por_src["post_office_export_id"] = _por_src["post_office_id"].map(po_id_by_internal)
    _missing_por = _por_src["post_office_export_id"].isna().sum()
    if _missing_por:
        raise ValueError(
            f"{_missing_por} post_office_regions rows reference an unknown "
            f"post_office_id; check Step 8.8 fan-out vs. Step 10 id assignment."
        )
    post_office_regions_out = pd.DataFrame({
        "id": _por_src["post_office_region_id"].astype(int).values,
        "post_office": _por_src["post_office_export_id"].astype(int).values,
        "region": _por_src["region_id"].astype(int).values,
    })
    post_office_regions_out = _stamp(post_office_regions_out)
    # source_listing_idx -> rolled catalog text (shared by all fan-outs of a listing).
    # Ratemarks and auxmarks inherit their owning townmark's catalog_txt via this map.
    catalog_text_by_listing = {}
    if townmarks_df is not None and len(townmarks_df):
        for _, _tm in townmarks_df.iterrows():
            sli = _tm.get("source_listing_idx")
            if sli is not None and sli not in catalog_text_by_listing:
                catalog_text_by_listing[sli] = _tm.get("catalog_text")
    # Per-listing desc text, looked up at townmark emission time. Combines
    # the parenthetical annotation lines (Backstamp, No town cds, ...) and
    # the See-clause for cross-reference rows.
    desc_by_listing = {}
    for _lidx, _lrow in listings.iterrows():
        _lines = list(_lrow.get('paren_annotations_desc') or [])
        _see = _lrow.get('see_clause')
        if _see and isinstance(_see, str) and _see.strip():
            _lines.append(_see.strip())
        if _lines:
            desc_by_listing[_lidx] = "\n".join(_lines)
    marking_rows = []
    for kind, src_id, mk_id in emit_order:
        if kind == "TM":
            r = _src_row_by(townmarks_df, "townmark_id", src_id)
            type_label = "TOWNMARK"
            rate_val = None
            catalog_txt = r.get("catalog_text") if r is not None else None
            date_fmt = r.get("date_format") if r is not None else None
        elif kind == "RM":
            r = _src_row_by(ratemarks_df, "ratemark_id", src_id)
            type_label = "RATEMARK"
            rate_val = r.get("rate_value") if r is not None else None
            catalog_txt = catalog_text_by_listing.get(r.get("source_listing_idx")) if r is not None else None
            date_fmt = None
        else:
            r = _src_row_by(auxmarks_df, "auxmark_id", src_id)
            type_label = "AUXMARK"
            rate_val = None
            catalog_txt = catalog_text_by_listing.get(r.get("source_listing_idx")) if r is not None else None
            date_fmt = None
        if r is None:
            continue
        src_idx = r.get("source_listing_idx")
        po_internal = r.get("post_office_id")
        if po_internal is None or (isinstance(po_internal, float) and pd.isna(po_internal)):
            # Fall back to the listing's townmark post_office (same convention as
            # the previous notebook). Pull from townmarks_df keyed by listing.
            if townmarks_df is not None and "post_office_id" in townmarks_df.columns:
                sel = townmarks_df[townmarks_df["source_listing_idx"] == src_idx]
                if len(sel):
                    po_internal = sel.iloc[0]["post_office_id"]
        is_ms = bool(r.get("is_manuscript"))
        shape_int = r.get("shape_id")
        shape_id = _resolve_int_fk(shape_id_by_internal, shape_int)
        # Marking invariant: when is_manuscript is False, shape is required.
        # If no shape resolved, fall back to "SL - Straight Line" (default
        # handstamp form for text-only inscriptions without a bracket cue).
        if not is_ms and shape_id is None:
            _sl = _shapes_src[_shapes_src["name"] == "SL - Straight Line"]
            if len(_sl):
                shape_id = int(_sl.iloc[0]["id"])
        is_irreg_val = r.get("is_irregular")
        if not is_ms and (is_irreg_val is None or (isinstance(is_irreg_val, float) and pd.isna(is_irreg_val))):
            is_irreg_val = False
        # desc: only townmarks carry annotations (Backstamp, See-clause);
        # ratemarks and auxmarks inherit them implicitly via their parent.
        desc_val = desc_by_listing.get(src_idx) if kind == "TM" else None
        marking_rows.append({
            "id": mk_id,
            "code": f"{RW_CODE}-{REGION_ABBREV}-{mk_id}",
            "type": type_label,
            "catalog_txt": catalog_txt,
            "inscription_txt": r.get("inscription_text"),
            "desc": desc_val,
            "is_manuscript": is_ms,
            "shape": shape_id,
            "lettering": _resolve_int_fk(lettering_id_by_internal, r.get("lettering_id")),
            "color": _resolve_int_fk(color_id_by_internal, r.get("color_id")),
            "is_irreg": is_irreg_val,
            "width": r.get("width"),
            "height": r.get("height"),
            "date_fmt": date_fmt,
            "impression": r.get("impression"),
            "rate_val": rate_val,
            "post_office": _resolve_int_fk(po_id_by_internal, po_internal),
        })
    markings_out = pd.DataFrame(marking_rows) if marking_rows else pd.DataFrame(columns=[
        "id", "code", "type", "catalog_txt", "inscription_txt", "desc", "is_manuscript",
        "shape", "lettering", "color", "is_irreg", "width", "height", "date_fmt",
        "impression", "rate_val", "post_office",
    ])
    markings_out = _stamp(markings_out)
    _missing_ct = markings_out["catalog_txt"].isna().sum() if len(markings_out) else 0
    if _missing_ct:
        _bad = markings_out[markings_out["catalog_txt"].isna()][["id", "code", "type"]]
        raise ValueError(
            f"{_missing_ct} markings emitted with null catalog_txt; "
            f"first offenders:\n{_bad.head(10).to_string(index=False)}"
        )
    _tm_code_to_mid = {}
    if townmarks_df is not None and len(townmarks_df) and "townmark_id" in townmarks_df.columns and "code" in townmarks_df.columns:
        for _, _r in townmarks_df.iterrows():
            _mid = marking_id_by_tm.get(_r["townmark_id"])
            if _mid is not None:
                _tm_code_to_mid[_r["code"]] = _mid
    if ratemarks_df is not None and len(ratemarks_df) and "ratemark_id" in ratemarks_df.columns and "code" in ratemarks_df.columns:
        for _, _r in ratemarks_df.iterrows():
            _mid = marking_id_by_rm.get(_r["ratemark_id"])
            if _mid is not None:
                _tm_code_to_mid[_r["code"]] = _mid
    if auxmarks_df is not None and len(auxmarks_df) and "auxmark_id" in auxmarks_df.columns and "code" in auxmarks_df.columns:
        for _, _r in auxmarks_df.iterrows():
            _mid = marking_id_by_ax.get(_r["auxmark_id"])
            if _mid is not None:
                _tm_code_to_mid[_r["code"]] = _mid
    _ds = dates_seen_df.copy() if dates_seen_df is not None else pd.DataFrame(columns=["marking_code", "date", "granularity"])
    if len(_ds) and "marking_code" in _ds.columns:
        _ds["subject_id"] = _ds["marking_code"].map(_tm_code_to_mid)
        _ds = _ds[_ds["subject_id"].notna()].reset_index(drop=True)
        _ds["subject_id"] = _ds["subject_id"].astype(int)
        dates_seen_out = pd.DataFrame({
            "id": range(1, len(_ds) + 1),
            "subject_type": "MARKING",
            "subject_id": _ds["subject_id"],
            "date": _ds["date"],
            "granularity": _ds["granularity"],
        })
    else:
        dates_seen_out = pd.DataFrame(columns=["id", "subject_type", "subject_id", "date", "granularity"])
    dates_seen_out = _stamp(dates_seen_out)
    cit_rows = []
    for kind, src_id, mk_id in emit_order:
        if kind == "TM":
            r = _src_row_by(townmarks_df, "townmark_id", src_id)
        elif kind == "RM":
            r = _src_row_by(ratemarks_df, "ratemark_id", src_id)
        else:
            r = _src_row_by(auxmarks_df, "auxmark_id", src_id)
        if r is None:
            continue
        src_idx = r.get("source_listing_idx")
        page = listings.loc[int(src_idx), "Page"] if src_idx is not None else None
        if page is None or (isinstance(page, float) and pd.isna(page)):
            page_str = ""
        else:
            try:
                page_str = str(int(page))
            except (TypeError, ValueError):
                page_str = str(page).strip()
        cit_rows.append({
            "reference_work": RW_ID,
            "subject_type": "MARKING",
            "subject_id": mk_id,
            "citation_detail": page_str,
        })
    citations_out = pd.DataFrame(cit_rows) if cit_rows else pd.DataFrame(
        columns=["reference_work", "subject_type", "subject_id", "citation_detail"]
    )
    citations_out.insert(0, "id", range(1, len(citations_out) + 1))
    citations_out = _stamp(citations_out)
    GENERATED = [
        ("colors",           colors_out,           ["id", "name", "hex_val", "pantone_code"]),
        ("letterings",       letterings_out,       ["id", "name"]),
        ("shapes",           shapes_out,           ["id", "name", "code"]),
        ("post_offices",         post_offices_out,         ["id", "name"]),
        ("post_office_regions",  post_office_regions_out,  ["id", "post_office", "region"]),
        ("markings",         markings_out,         [
                                "id", "code", "type", "catalog_txt", "inscription_txt",
                                "desc", "is_manuscript", "shape", "lettering", "color",
                                "is_irreg", "width", "height", "date_fmt", "impression",
                                "rate_val", "post_office",
                             ]),
        ("dates_seen",       dates_seen_out,       ["id", "subject_type", "subject_id", "date", "granularity"]),
        ("citations",        citations_out,        [
                                "id", "reference_work", "subject_type", "subject_id",
                                "citation_detail",
                             ]),
    ]
    for stem, frame, base_cols in GENERATED:
        cols = base_cols + AUDIT_TAIL
        out = frame[cols] if len(frame) else pd.DataFrame(columns=cols)
        out = _cast_int_columns(out, INT_COLS.get(stem, []))
        path = os.path.join(OUT_DIR, f"{stem}.csv")
        out.to_csv(path, index=False)
        print(f"  {stem + '.csv':<22s} {len(out):>5d} rows  ->  {path}")
    for stem in ("regions", "reference_works"):
        src = os.path.join(INPUT_DIR, f"{stem}.csv")
        dst = os.path.join(OUT_DIR, f"{stem}.csv")
        shutil.copyfile(src, dst)
        _row_count = sum(1 for _ in open(dst, "r", encoding="utf-8")) - 1
        print(f"  {stem + '.csv':<22s} {_row_count:>5d} rows  ->  {dst}  (passthrough)")
    print(f"Wrote {len(GENERATED) + 3} tables to {OUT_DIR}")
    print(f"Load via: ./woco import_ascc_bundle {OUT_DIR}")

    # ======================================================================
    # Step 11: Images Table Assembly
    # ======================================================================
    IMAGES_SUBDIR = region_media_slug(REGION_ABBREV)  # e.g. 'va'
    pm_to_final_id = marking_id_by_tm
    image_rows = []
    next_image_id = 1
    # Driven by per-source-listing image_file_refs computed in Step 8.25.
    # Every townmark (including each color-fanout sibling) gets its own
    # image rows pointing at the same on-disk files: same original_filename
    # and storage_filename, different subject_id, new image_id per row.
    for _, pm in townmarks_df.sort_values('townmark_id').iterrows():
        src_idx = int(pm['source_listing_idx'])
        refs = listings.loc[src_idx, 'image_file_refs']
        if not refs:
            continue
        final_marking_id = pm_to_final_id.get(pm['townmark_id'])
        if final_marking_id is None:
            print(f'WARNING: no marking_id for townmark_id={pm["townmark_id"]}; skipping.')
            continue
        for display_order, (page, chunk, counter) in enumerate(refs, start=1):
            fname = image_filename(IMAGES_SUBDIR, page, chunk, counter)
            disk_path = MEDIA_ROOT / IMAGES_SUBDIR / fname
            if not disk_path.exists():
                raise FileNotFoundError(
                    f'Missing image: {disk_path}  '
                    f'(townmark_id={pm["townmark_id"]}, page={page}, chunk={chunk})'
                )
            data = disk_path.read_bytes()
            with PILImage.open(disk_path) as im:
                img_w, img_h = im.size
            image_rows.append({
                'image_id': next_image_id,
                'subject_type': 'MARKING',
                'subject_id': int(final_marking_id),
                'original_filename': fname,
                'storage_filename': f'{IMAGES_SUBDIR}/{fname}',
                'file_checksum': hashlib.sha256(data).hexdigest(),
                'mime_type': mimetypes.guess_type(fname)[0] or 'image/png',
                'image_width': img_w,
                'image_height': img_h,
                'file_size_bytes': len(data),
                # is_tracing is the canonical boolean on Image (see
                # backend/common/models.py and migration 0063_image_is_tracing).
                # The munger only ever emits trace/diagram extracts, so every
                # row is_tracing=True; image_view defaults to FULL since
                # COMPARISON was dropped from the choices in 0063.
                'image_view': 'FULL',
                'image_description': '',
                'is_tracing': True,
                'display_order': display_order,
                'uploaded_by': AUDIT_USER_ID,
            })
            next_image_id += 1
    _img_cols = [
        'image_id', 'subject_type', 'subject_id', 'original_filename',
        'storage_filename', 'file_checksum', 'mime_type', 'image_width',
        'image_height', 'file_size_bytes', 'image_view', 'image_description',
        'is_tracing', 'display_order', 'uploaded_by',
    ]
    images_out = (
        pd.DataFrame(image_rows, columns=_img_cols)
        if image_rows
        else pd.DataFrame(columns=_img_cols)
    )
    images_out = _stamp(images_out)
    _img_counts = images_out.groupby('subject_id')['image_id'].count()
    # Per-townmark validation: every townmark must have one image row per
    # ref in its source listing's image_file_refs (post flow-down). This
    # asserts both the flow-down arithmetic (counts match the flowed
    # totals) and the color-fanout duplication (every color child has its
    # own rows).
    for _, pm in townmarks_df.iterrows():
        src_idx = int(pm['source_listing_idx'])
        refs = listings.loc[src_idx, 'image_file_refs']
        expected = len(refs) if refs else 0
        if expected == 0:
            continue
        mid = pm_to_final_id.get(pm['townmark_id'])
        if mid is None:
            continue
        actual = int(_img_counts.get(mid, 0))
        if expected != actual:
            raise AssertionError(
                f'Image count mismatch for marking_id={mid} '
                f'(townmark_id={pm["townmark_id"]}, '
                f'source_listing_idx={src_idx}): '
                f'expected {expected}, emitted {actual}'
            )
    _img_int_cols = [
        'image_id', 'subject_id', 'image_width', 'image_height',
        'file_size_bytes', 'display_order', 'uploaded_by',
        'created_by', 'modified_by',
    ]
    images_out = _cast_int_columns(images_out, _img_int_cols)
    _img_path = os.path.join(OUT_DIR, 'images.csv')
    _img_emit_cols = _img_cols + ['created_date', 'modified_date', 'created_by', 'modified_by']
    images_out[_img_emit_cols].to_csv(_img_path, index=False)
    print(f'  {"images.csv":<22s} {len(images_out):>5d} rows  ->  {_img_path}')
    print(f'  (tracing images: all, is_tracing=True, image_view=FULL)')


if __name__ == "__main__":
    main()
