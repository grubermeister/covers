import re

import pandas as pd


REQUIRED_COLS = ['Listing', 'Page', 'Chunk', 'Images Above', 'Type']

OPTIONAL_COLS = ['Manuscript', 'Default Shape', 'Institutional Ownership']

_WS = re.compile(r'\s+')

# Unmatched-banner report: all-caps META lines that look like section
# headings (so a misread banner shows up in the verify step instead of
# silently leaving its listings on the catalog default region).
_BANNER_CANDIDATE = re.compile(r"[A-Z][A-Z .,'&-]{3,40}")


def _norm_banner(text) -> str:
    return _WS.sub(' ', str(text)).strip().upper()


def assign_section_regions(df, region_seed, default_region_id: int) -> pd.Series:
    """Per-listing region id derived from the catalog section each row
    sits under, walking rows in CSV (reading) order and tracking the
    active section across META banner rows.

    Only TERRITORY-tier region names switch the active section -- the
    catalog's section banners ("MICHIGAN TERRITORY", optionally prefixed
    "AS NORTHWEST TERRITORY") name the territory in force, while town
    strings only carry abbreviations like "M.T." that the ASCC header
    explicitly calls ambiguous (Michigan / Minnesota / Mississippi
    Territory). A banner containing STATEHOOD resets to
    default_region_id. Bare state names never match: the page running
    head (e.g. "MICHIGAN") survives extraction as a META row on every
    page and must not reset the section.

    Returns an int64 Series aligned to df.index. Catalogs without
    territory banners (e.g. VA) come back all default_region_id.
    """
    territory_by_name = {
        _norm_banner(row['name']): int(row['id'])
        for _, row in region_seed.iterrows()
        if str(row.get('region_tier', '')).strip().upper() == 'TERRITORY'
    }
    region_name_by_id = {
        int(row['id']): str(row['name']) for _, row in region_seed.iterrows()
    }

    current = int(default_region_id)
    assigned = []
    switches = []
    unmatched = {}
    for _, row in df.iterrows():
        if row['Type'] == 'META':
            banner = _norm_banner(row['Listing'])
            key = banner[3:] if banner.startswith('AS ') else banner
            if key in territory_by_name:
                current = territory_by_name[key]
                switches.append((banner, current))
            elif 'STATEHOOD' in banner:
                current = int(default_region_id)
                switches.append((banner, current))
            elif _BANNER_CANDIDATE.fullmatch(banner):
                unmatched[banner] = unmatched.get(banner, 0) + 1
        assigned.append(current)

    out = pd.Series(assigned, index=df.index, dtype='int64')

    listing_mask = df['Type'] == 'LISTING'
    counts = out[listing_mask].value_counts().sort_index()
    print('Section-region assignment:')
    for region_id, n in counts.items():
        name = region_name_by_id.get(int(region_id), '?')
        print(f'  region {int(region_id)} ({name}): {int(n)} listings')
    if switches:
        print(f'  Section switches ({len(switches)}):')
        for banner, region_id in switches:
            print(f'    {banner!r} -> region {region_id}')
    if unmatched:
        print('  Unmatched banner-like META rows (verify none is a real section):')
        for banner, n in sorted(unmatched.items(), key=lambda kv: -kv[1])[:15]:
            print(f'    {banner!r} x{n}')
    return out


def process_meta_rows(meta_df):
    # TODO: parse META rows for column headers and cross-reference
    # targets. Inputs: meta_df with columns Listing, Page, Chunk,
    # Images Above, Type. Section/state-heading context is handled by
    # assign_section_regions(). Currently a no-op.
    return None
