#!/usr/bin/env python3
"""catalog_edition_diff -- VA-only fuzzy diff between two catalog editions.

Both inputs are catalog OCR in the v2 pipeline CSV shape:

    Listing,Page,Chunk,Images Above,Type,Manuscript,Default Shape

We compare the BASE edition (tools/wip/in/VA_ASCC_CTLG.csv, the fresh v2 OCR)
against the COMPARE edition (tools/wip/in/v1_VA_ocr.csv, the older manual
entry data) and report two separate facts:

    row_disposition  where the source row went: matched, moved, added,
                     removed, base_duplicate, compare_duplicate,
                     duplicate_pair
    content_change   what changed in a matched representative: none, cosmetic,
                     marker_only, material, ambiguous

Matching is fuzzy because the two OCR passes differ in cosmetic ways only:
leader dots ("... 1500.00" vs " 1,500"), price formatting, smart vs straight
quotes, en/em vs hyphen dashes, comma-vs-period confusion, comma spacing in
dates ("May 21,1772" vs "May 21, 1772"), case, and stray whitespace.

The trailing price/value token is parsed and shown, but per the project
decision, a changed price is NOT treated as a material catalog amendment.

Algorithm (see the plan):

  1. Two normalization levels per listing:
       akey(text)  -- maximal fold (alphanumerics only) used to ALIGN entries
                      so cosmetic-identical listings produce identical keys.
       canon(text) -- parser-independent fold used only to score fuzzy matches.
     Continuation rows ("Same(...)", "(L)(...)", lowercase-leading) inherit the
     most recent town header as a prefix so generic repeats become distinctive.
  2. Exact duplicate source rows are grouped before matching. The first row in
     each group is the representative used for comparison; duplicate rows are
     still emitted in the report as explicit accountability rows.
  3. Ordered alignment via difflib.SequenceMatcher.get_opcodes() over the
     representative akeys (autojunk=False). Matched representatives are
     compared with parsed munger listing components, not canon equality.
  4. Global reconciliation pass cross-matches provisional added vs removed
     (exact akey, then gated fuzzy) to recover moves and mis-alignments.

Outputs:
  report-dir/va_edition_diff_report.csv  -- one row per source LISTING row
  report-dir/va_edition_diff_summary.txt -- counts, thresholds, score histogram
  out-dir/v2_VA_new_modified.csv         -- COMPARE representatives that are
                                            added against the v2 base or
                                            materially changed, in the 7 v2
                                            columns, values verbatim,
                                            COMPARE-edition order

Usage (from repo root or tools/; exit code 0 on success):

    python3 tools/catalog_edition_diff.py
    python3 tools/catalog_edition_diff.py \
    --base tools/wip/in/VA_ASCC_CTLG.csv \
        --compare tools/wip/in/v1_VA_ocr.csv \
        --modified-threshold 0.92 --pair-threshold 0.55

ASCII note: this source is pure 7-bit ASCII. The cosmetic unicode characters we
fold (curly quotes, en/em dashes, ellipsis) are built via chr(0xNNNN) in
_COSMETIC_FOLD, never written literally. The v2 OCR pipeline emits pure ASCII;
only the v1-derived COMPARE export carries unicode, so load_listings runs a
load-time to_ascii() pass over all Listing text -- the report and out file are
therefore pure ASCII too. The ASCII rule governs the source code; this tool
additionally normalizes the catalog data it emits.
"""
import argparse
import csv
import difflib
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from munger.listing_parser import parse_listing_text

# v1/v2 cells (HTML blobs, manuscript memos, multiline META) can exceed Python's
# default CSV field cap. Raise it well past anything in the exports.
csv.field_size_limit(10 ** 9)

# The 7 v2 pipeline columns, exact order -- do not reorder. The out file uses
# exactly these, copied verbatim from the COMPARE edition rows.
V2_COLUMNS = [
    "Listing",
    "Page",
    "Chunk",
    "Images Above",
    "Type",
    "Manuscript",
    "Default Shape",
]

# Cosmetic unicode -> ASCII. Keys are built with chr(codepoint) so this source
# stays pure 7-bit ASCII (no literal glyph appears in the file). Codepoints:
#   0x2018 0x2019 single curly quotes      -> '
#   0x201C 0x201D double curly quotes      -> "
#   0x2013 0x2014 0x2212 en/em dash, minus -> -
#   0x2026 horizontal ellipsis             -> ...
_COSMETIC_FOLD = {
    chr(0x2018): "'",
    chr(0x2019): "'",
    chr(0x201C): '"',
    chr(0x201D): '"',
    chr(0x2013): "-",
    chr(0x2014): "-",
    chr(0x2212): "-",
    chr(0x2026): "...",
}
_COSMETIC_RE = re.compile("[" + "".join(_COSMETIC_FOLD) + "]")

# Trailing price/value token, with any leader-dot run in front of it. Price is
# always terminal in this data. value = digits (optional thousands separators /
# decimals), a bare "--", or slash-joined composites such as "--/20" and
# "95/75.00". Examples stripped: "... 1500.00", ". 20.00", " 1,500",
# " 1500", "... --", " --/20.00". Two alternatives: with a leader-dot run, or
# with just space.
_PRICE_UNIT = r"(?:\d[\d,]*(?:\.\d+)?|--)"
_PRICE_COMPOSITE = _PRICE_UNIT + r"(?:/" + _PRICE_UNIT + r")*"
_PRICE_TAIL = re.compile(r"\s*\.{1,}\s*" + _PRICE_COMPOSITE + r"\s*$"
                         r"|\s+" + _PRICE_COMPOSITE + r"\s*$")

# Same trailing token, but CAPTURING the value, so we can normalize and append
# it to the alignment key (akey). Price is ignored for the significance score
# (canon) but USED to disambiguate alignment: it is the only thing telling apart
# child records like "Same(Green) 20" vs "Same(Green) 35".
_PRICE_VALUE = re.compile(r"(?:\.{1,}\s*|\s+)(" + _PRICE_COMPOSITE + r")\s*$")

# Non-alphanumeric run (for akey: everything not [a-z0-9] after the fold).
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Whitespace run.
_WS_RUN = re.compile(r"\s+")

# Order within a list is presentation, not content: "Black,Red" == "Red,Black",
# "PAID,5,10" == "5,10,PAID". We treat each list as an unordered SET by sorting
# its tokens. Comma AND semicolon both count as separators: the OCR routinely
# swaps ";" for "," (e.g. "...1787;SL-29x3" vs "...1787,SL-29x3"), so a field
# boundary cannot be trusted to distinguish editions. A run is therefore a
# maximal sequence of 2+ tokens joined by "," or ";", where a token is any text
# without a comma, semicolon, or parenthesis -- so parentheticals still bound the
# run and are never crossed. Pure reordering / delimiter-swap is neutralized;
# adding or dropping a token still changes the set, so the entry stays modified.
_LIST_RUN = re.compile(r"[^,;()]+(?:\s*[,;]\s*[^,;()]+)+")
_LIST_SPLIT = re.compile(r"[,;]")

# OCR/manual entry sometimes writes a color combination as "Brown black" or
# "Brown-black" where another source writes "Black,Brown". Treat adjacent color
# words joined only by a space or hyphen as an unordered color list.
_COLOR_WORDS = (
    "brownish",
    "black",
    "blue",
    "brown",
    "claret",
    "green",
    "orange",
    "red",
)
_COLOR_ALT = "|".join(_COLOR_WORDS)
_COLOR_COMPOUND = re.compile(
    r"\b(?:" + _COLOR_ALT + r")(?:[\s-]+(?:" + _COLOR_ALT + r"))+\b",
    re.IGNORECASE,
)
_COLOR_SPLIT = re.compile(r"[\s-]+")


def sort_list_runs(text):
    """Sort tokens within each comma/semicolon list run so order and ;-vs-,
    delimiter choice are canonical."""
    def repl(m):
        toks = [t.strip() for t in _LIST_SPLIT.split(m.group(0))]
        return ",".join(sorted(toks))
    return _LIST_RUN.sub(repl, text)


def sort_color_compounds(text):
    """Canonicalize space/hyphen joined color compounds as unordered lists."""
    def repl(m):
        toks = [t.strip().lower() for t in _COLOR_SPLIT.split(m.group(0))]
        return ",".join(sorted(toks))
    return _COLOR_COMPOUND.sub(repl, text)

# A row is a "continuation" of the previous town header if its listing text
# starts with one of these (so it inherits the carried-forward town prefix):
#   Same(...), (L)(...), an "(x)(" continuation marker, or a lowercase letter.
_CONTINUATION_RE = re.compile(r"^\s*(same\b|\(l\)|\(.\)\s*\(|[a-z])")

# A fresh town header: starts with a letter, not a continuation. Capture the
# leading word run (letters, spaces, dots, apostrophes, hyphens) up to first "(".
_TOWN_HEAD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z .'\-]*?)\s*\(")

# Leading source markers used by the catalog, not part of the town/listing name.
_LEADING_MARKERS = re.compile(r"^\s*(?:\*+\s*)?(?:\(\s*1\s*\)\s*)?")

# First date-ish token in a manuscript-list entry. Text before this is the
# listing name; text after this is date/value detail.
_DATEISH_TOKEN = re.compile(r"\s+(?:c\.?\s*)?\*?\d{3,4}\b|\s+--\b")

# Final state abbreviations in postmark names. Drop these for fuzzy name gates
# so "BOYDTON/Va." and "BOYDTONVa." compare on the town name.
_STATE_SUFFIX = re.compile(r"(?:/|\s)*(?:va|na|virginia)\.?$")


def fold_cosmetic(text):
    """Fold cosmetic unicode (curly quotes, dashes, ellipsis) to ASCII."""
    return _COSMETIC_RE.sub(lambda m: _COSMETIC_FOLD[m.group(0)], text)


def to_ascii(text):
    """Transliterate any unicode in catalog text to its ASCII equivalent.

    The v2 OCR pipeline emits pure ASCII; only the v1-derived OLD export carries
    unicode (curly quotes etc.). This pass makes both inputs uniformly ASCII.
    Steps:
      1. fold_cosmetic: explicit punctuation map -- curly quotes -> ' / ", en/em
         dash and minus -> -, ellipsis -> "..." (NFKD would not do these).
      2. NFKD normalize, then encode ascii/ignore -- decomposes accented letters
         to base + combining mark and drops the mark (e.g. e-acute -> "e"); any
         remaining non-ASCII byte is dropped.
    Already-ASCII text is returned unchanged.
    """
    t = fold_cosmetic(text)
    if t.isascii():
        return t
    t = unicodedata.normalize("NFKD", t)
    return t.encode("ascii", "ignore").decode("ascii")


def strip_price(text):
    """Remove the trailing price/value token (and any leader-dot run)."""
    return _PRICE_TAIL.sub("", text)


def norm_price(text):
    """Canonical price value for alignment, format-insensitive across editions.

    "1,500" -> "1500"   "1500.00" -> "1500"   "20" -> "20"
    "--/20.00" -> "na/20"   "--" / none -> "".
    A bare "--" means "no price seen", which is not a distinguishing value.
    """
    m = _PRICE_VALUE.search(fold_cosmetic(text))
    if not m:
        return ""
    v = m.group(1)
    if v == "--":
        return ""
    parts = []
    for part in v.split("/"):
        if part == "--":
            parts.append("na")
            continue
        part = part.replace(",", "")
        if "." in part:               # drop trailing-zero cents (".00", ".50"->".5")
            part = part.rstrip("0").rstrip(".")
        parts.append(part)
    return "/".join(parts)


def akey(text):
    """Maximal alignment key: alphanumerics only, lowercase, then a normalized
    price suffix.

    Cosmetic-identical listings produce byte-identical keys, so OCR
    punctuation/spacing noise can never break ordered alignment. The price
    suffix is appended (after a "p" separator) ONLY to disambiguate otherwise
    identical child listings that differ solely by price; it is format-
    normalized so "1,500" and "1500.00" still match across editions.
    """
    base = _textkey(text)
    price = norm_price(text)
    return (base + "p" + price) if price else base


def _textkey(text):
    """Listing TEXT identity: alphanumerics only, lowercase, price stripped,
    comma-list order canonicalized, no price suffix and no carried-town prefix.
    Two rows with the same _textkey have identical descriptive text up to list
    ordering (used for alignment and the repeat/child-record stats)."""
    t = strip_price(fold_cosmetic(text)).lower()
    t = sort_color_compounds(t)
    t = sort_list_runs(t)
    return _NON_ALNUM.sub("", t)


def canon(text):
    """Semantic-preserving form for scoring significance.

    Folds cosmetic unicode, strips the price, lowercases, neutralizes spacing
    around parens/commas, canonicalizes comma-list order (not significant), then
    removes remaining whitespace. Preserves words and dates -- so a real wording
    change still scores below 1.0 while a pure punctuation/spacing/list-order
    diff scores ~1.0.
    """
    t = fold_cosmetic(text)
    t = strip_price(t)
    t = t.lower()
    # Drop runs of 2+ dots: the NEW edition uses " ... " as a column filler
    # (e.g. "Town ... 1845 ... 20.00") where the OLD edition uses plain spaces.
    t = re.sub(r"\.{2,}", " ", t)
    t = _WS_RUN.sub(" ", t)
    # "c1777" and "c.1777" both mean circa 1777. Normalize only before a
    # date-looking token so other periods remain available for scoring.
    t = re.sub(r"\bc\.\s*(?=\d{3,4})", "c", t)
    # Hyphen before a measurement is layout punctuation in phrases like
    # "box-54x30" and "arc-25x5-7"; date ranges keep their digit-digit hyphen.
    t = re.sub(r"(?<=[a-z])-(?=\d)", "", t)
    # Abbreviation punctuation is not significant here: "C.H." vs "CH",
    # "VA." vs "VA", and "Wm." vs "Wm" should not create modifications.
    # Keep decimal points between digits for sizes like 31.5 and 47x3.5.
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", "", t)
    # Possessive and decade apostrophes are inconsistent OCR/manual entry:
    # "Soldier's" vs "Soldiers", "1850's" vs "1850s".
    t = t.replace("'", "")
    # Quote marks around individual lettering notes are punctuation, not the
    # note itself. A row still changes if "a high" or "b ms" text is added.
    t = t.replace('"', "")
    t = t.replace("`", "")
    # "b in ms", "bms", '"Va." in ms', and '"Va."in ms' are spacing variants
    # of the same manuscript-lettering note.
    t = re.sub(r"\b([a-z]{1,3})\s*in\s+ms\b", r"\1ms", t)
    t = re.sub(r"\bin\s+ms\b", "ms", t)
    # Drop spaces adjacent to parens and around commas (date/paren spacing diffs).
    t = re.sub(r"\s*([(),])\s*", r"\1", t)
    # Space/hyphen joined color compounds are OCR/manual-entry variants:
    # "brown black", "brown-black", and "black-brown" are the same color set.
    t = sort_color_compounds(t)
    # List ordering is not significant: sort comma runs like "paid,5,10" or
    # "black,red" so a pure reorder scores identical.
    t = sort_list_runs(t)
    # After list runs are sorted, these punctuation marks are separators only.
    # Keep slashes and hyphens because they can carry rate/size/date meaning.
    t = re.sub(r"[][(),]", "", t)
    # Drop all remaining whitespace for the score.
    return t.replace(" ", "")


def town_header(text):
    """Return the leading town name if `text` looks like a fresh header, else None."""
    if _CONTINUATION_RE.match(text):
        return None
    m = _TOWN_HEAD_RE.match(text)
    if not m:
        return None
    return m.group(1).strip()


def source_id_base(edition, row, idx):
    """Stable source id base before repeated page/chunk suffixes are applied."""
    chunk = (row.get("Chunk") or "").strip()
    page = (row.get("Page") or "").strip()
    if edition == "BASE":
        if page and chunk:
            return "BASE:%s:%s" % (page, chunk)
        if chunk:
            return "BASE:%s" % chunk
        return "BASE:row%d" % (idx + 1)
    if edition == "COMPARE":
        return "COMPARE:%s" % (chunk or ("row%d" % (idx + 1)))
    if page and chunk:
        return "%s:%s:%s" % (edition, page, chunk)
    if chunk:
        return "%s:%s" % (edition, chunk)
    return "%s:row%d" % (edition, idx + 1)


def normalize_entry_name(text):
    """Return an alphanumeric-only key for town/listing-name comparisons."""
    t = fold_cosmetic(text).lower().strip()
    t = _LEADING_MARKERS.sub("", t)
    t = _STATE_SUFFIX.sub("", t)
    return _NON_ALNUM.sub("", t)


def entry_name_text(listing, carried_town):
    """Extract the town/listing name used to gate fuzzy matching.

    Examples of the intended shape:
      "Brentsville ... 1845-48 ... 15.00" -> "Brentsville"
      "BOYDTON/Va.(1835-55;30;PAID;Black,Red)" -> "BOYDTON"
      "Same(Green)" with carried town "Aldie" -> "Aldie"
    """
    text = strip_price(fold_cosmetic(listing)).strip()
    text = re.sub(r"\.{2,}", " ... ", text)
    text = _WS_RUN.sub(" ", text)
    lowered = text.lower()
    if carried_town and (
        lowered.startswith("same")
        or lowered.startswith("(l)")
        or re.match(r"^\(.\)\s*\(", lowered)
    ):
        return carried_town

    cleaned = _LEADING_MARKERS.sub("", text).strip()
    m = _TOWN_HEAD_RE.match(cleaned)
    if m:
        name = m.group(1).strip()
    else:
        name = re.split(r"\s+\.\.\.\s+|\s+-{2,}\s+", cleaned, maxsplit=1)[0].strip()
        m = _DATEISH_TOKEN.search(name)
        if m:
            name = name[:m.start()].strip()
    name = _STATE_SUFFIX.sub("", name).strip(" ./")
    return name or carried_town or cleaned


def can_fuzzy_pair(old_entry, new_entry):
    """Return True when two non-exact rows are plausible same-listing variants."""
    old_name = old_entry.name_key
    new_name = new_entry.name_key
    if not old_name or not new_name:
        return False
    if old_name == new_name:
        return True
    if old_name[0] != new_name[0]:
        return False
    if min(len(old_name), len(new_name)) < 4:
        return False
    return similarity(old_name, new_name) >= 0.90


def number_tokens(text):
    """Numeric tokens used only for summary diagnostics."""
    return re.findall(r"\d+", text)


def similarity(a, b):
    """canon-space character similarity in [0.0, 1.0]."""
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def key_dict(key_tuple):
    """Convert a parser key tuple into a field-name dict."""
    return dict(key_tuple or ())


def changed_key_fields(old_key, new_key):
    """Return sorted component names whose parsed keys differ."""
    old_map = key_dict(old_key)
    new_map = key_dict(new_key)
    names = sorted(set(old_map) | set(new_map))
    return [name for name in names if old_map.get(name) != new_map.get(name)]


def compare_content(old_entry, new_entry):
    """Compare matched representatives with parsed munger components."""
    old_parsed = old_entry.parsed
    new_parsed = new_entry.parsed
    changed = []

    material_fields = changed_key_fields(old_parsed.material_key,
                                         new_parsed.material_key)
    marker_changed = old_parsed.marker_key != new_parsed.marker_key
    unknown_changed = old_parsed.unknown_key != new_parsed.unknown_key
    value_changed = old_parsed.value_key != new_parsed.value_key
    parse_errors = old_parsed.parse_errors or new_parsed.parse_errors

    if material_fields:
        changed.extend(material_fields)
        return "material", ",".join(changed)

    if unknown_changed or parse_errors:
        if unknown_changed:
            changed.append("unknown")
        if parse_errors:
            changed.append("parse_errors")
        return "ambiguous", ",".join(changed)

    if marker_changed:
        changed.append("markers")
        if value_changed:
            changed.append("valuation")
        return "marker_only", ",".join(changed)

    if old_entry.listing == new_entry.listing:
        return "none", ""

    if value_changed:
        changed.append("valuation")
    changed.append("formatting")
    return "cosmetic", ",".join(changed)


class Entry:
    """One LISTING row plus its derived keys and carried-forward town prefix."""

    __slots__ = (
        "idx",
        "row",
        "listing",
        "town",
        "akey",
        "canon",
        "edition",
        "source_id",
        "rep",
        "dup_index",
        "name_label",
        "name_key",
        "parsed",
    )

    def __init__(self, idx, row, edition, source_id):
        self.idx = idx                       # position among LISTING rows (0-based)
        self.row = row                       # original DictReader row (verbatim)
        self.listing = row.get("Listing", "")
        self.town = ""                       # carried-forward town header
        self.akey = ""                       # set in finalize(), after town known
        self.canon = ""
        self.edition = edition
        self.source_id = source_id
        self.rep = self
        self.dup_index = 0
        self.name_label = ""
        self.name_key = ""
        self.parsed = None

    def finalize(self, town):
        self.town = town
        # akey only: prefix the carried-forward town for continuation rows
        # ("Same(...)", "(L)(...)"). Fresh rows must not inherit the previous
        # town, because slash-state names such as "STANARDSVILLE/Va.(...)"
        # are self-contained and can otherwise become false non-duplicates.
        # canon uses the listing text alone for the significance score.
        prefix = (town + " ") if town and _CONTINUATION_RE.match(self.listing) else ""
        self.akey = akey(prefix + self.listing)
        self.canon = canon(self.listing)
        self.parsed = parse_listing_text(self.listing, self.row, town)
        self.name_label = self.parsed.name_text or entry_name_text(self.listing, town)
        self.name_key = self.parsed.name_key or normalize_entry_name(self.name_label)


def load_listings(path, edition):
    """Load a v2 CSV, keep Type==LISTING rows in order, attach carried town.

    Returns (entries, converted) where converted is the number of rows whose
    Listing text contained unicode that was transliterated to ASCII. The
    Listing field is normalized in place, so the report and out file emit ASCII.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    # ASCII-normalization pass over all catalog text, before any matching.
    converted = 0
    for row in rows:
        raw = row.get("Listing", "")
        ascii_text = to_ascii(raw)
        if ascii_text != raw:
            converted += 1
        row["Listing"] = ascii_text
    entries = []
    current_town = ""
    idx = 0
    source_counts = Counter()
    for row in rows:
        if (row.get("Type") or "").strip().upper() != "LISTING":
            # META and anything non-LISTING is excluded from matching. Only
            # LISTING headers seed the carried-forward town, so skip the rest.
            continue
        sid_base = source_id_base(edition, row, idx)
        source_counts[sid_base] += 1
        sid = sid_base
        if source_counts[sid_base] > 1:
            sid = "%s#%d" % (sid_base, source_counts[sid_base])
        entry = Entry(idx, row, edition, sid)
        head = town_header(entry.listing)
        if head:
            current_town = head
        entry.finalize(current_town)
        entries.append(entry)
        idx += 1
    return entries, converted


def representative_groups(entries):
    """Return representative entries and exact-duplicate groups by representative."""
    groups_by_key = {}
    reps = []
    groups = {}
    for entry in entries:
        rep = groups_by_key.get(entry.akey)
        if rep is None:
            rep = entry
            groups_by_key[entry.akey] = rep
            reps.append(rep)
            groups[rep] = []
        entry.rep = rep
        entry.dup_index = len(groups[rep])
        groups[rep].append(entry)
    return reps, groups


def representative_id(entry):
    """Source id of the representative for an entry, or blank for missing side."""
    if entry is None:
        return ""
    return entry.rep.source_id


def classify_pair(old_entry, new_entry, moved):
    """Classify a matched representative pair on both report axes."""
    content_change, changed_fields = compare_content(old_entry, new_entry)
    row_disposition = "moved" if moved else "matched"
    return row_disposition, content_change, changed_fields


def result_row(row_disposition, content_change, changed_fields, score,
               new_entry, old_entry, match_reason, flags=""):
    """Build a result dict shared by representative and duplicate report rows."""
    return {
        "row_disposition": row_disposition,
        "content_change": content_change,
        "changed_fields": changed_fields,
        "score": score,
        "new": new_entry,
        "old": old_entry,
        "match_reason": match_reason,
        "flags": flags,
        "representative_row_disposition": row_disposition,
        "representative_content_change": content_change,
        "old_representative": old_entry.rep if old_entry else None,
        "new_representative": new_entry.rep if new_entry else None,
    }


def classify(old_entries, new_entries, pair_threshold):
    """Align and classify. Returns (results, stats).

    results: list of dicts with row_disposition/content_change/score/new/old.
    """
    old_keys = [e.akey for e in old_entries]
    new_keys = [e.akey for e in new_entries]
    sm = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)

    results = []           # representative result rows
    added = []             # provisional NEW-only Entry list
    removed = []           # provisional OLD-only Entry list
    metrics = Counter()

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                o = old_entries[i1 + k]
                n = new_entries[j1 + k]
                s = similarity(o.canon, n.canon)
                row_disp, change, changed_fields = classify_pair(o, n, moved=False)
                metrics["exact_key_pairs"] += 1
                if change == "material":
                    metrics["material_changed_pairs"] += 1
                    if number_tokens(o.canon) != number_tokens(n.canon):
                        metrics["numeric_changed_pairs"] += 1
                elif change in ("cosmetic", "marker_only", "ambiguous"):
                    metrics["%s_pairs" % change] += 1
                results.append(result_row(row_disp, change, changed_fields,
                                          s, n, o, "exact_key"))
        elif tag == "replace":
            _match_block(old_entries[i1:i2], new_entries[j1:j2],
                         pair_threshold, results, added, removed, metrics)
        elif tag == "insert":
            added.extend(new_entries[j1:j2])
        elif tag == "delete":
            removed.extend(old_entries[i1:i2])

    _reconcile(added, removed, pair_threshold, results, metrics)

    stats = Counter(r["row_disposition"] for r in results)
    return results, stats, metrics


def _match_block(old_block, new_block, pair_threshold,
                 results, added, removed, metrics):
    """Greedy best-match within a replace block; leftovers go to added/removed."""
    used_old = set()
    for n in new_block:
        best_o = None
        best_s = -1.0
        best_oi = None
        for oi, o in enumerate(old_block):
            if oi in used_old:
                continue
            s = similarity(o.canon, n.canon)
            if s >= pair_threshold and not can_fuzzy_pair(o, n):
                metrics["rejected_fuzzy_candidates"] += 1
                continue
            if s > best_s:
                best_s, best_o, best_oi = s, o, oi
        if best_o is not None and best_s >= pair_threshold:
            used_old.add(best_oi)
            row_disp, change, changed_fields = classify_pair(best_o, n, moved=False)
            metrics["gated_fuzzy_pairs"] += 1
            if change == "material":
                metrics["material_changed_pairs"] += 1
                if number_tokens(best_o.canon) != number_tokens(n.canon):
                    metrics["numeric_changed_pairs"] += 1
            elif change in ("cosmetic", "marker_only", "ambiguous"):
                metrics["%s_pairs" % change] += 1
            results.append(result_row(row_disp, change, changed_fields,
                                      best_s, n, best_o, "gated_fuzzy"))
        else:
            added.append(n)
    for oi, o in enumerate(old_block):
        if oi not in used_old:
            removed.append(o)


def _reconcile(added, removed, pair_threshold, results, metrics):
    """Cross-match provisional added vs removed to recover moves/mis-alignments."""
    removed_by_key = defaultdict(list)
    for o in removed:
        removed_by_key[o.akey].append(o)

    leftover_added = []
    matched_removed = set()  # id() of removed Entry objects that got paired

    # Pass 1: exact akey moves (cheap, O(n)).
    for n in added:
        bucket = removed_by_key.get(n.akey)
        partner = None
        while bucket:
            cand = bucket.pop()
            if id(cand) not in matched_removed:
                partner = cand
                break
        if partner is not None:
            matched_removed.add(id(partner))
            s = similarity(partner.canon, n.canon)
            row_disp, change, changed_fields = classify_pair(partner, n, moved=True)
            metrics["exact_key_moves"] += 1
            if change == "material":
                metrics["material_changed_pairs"] += 1
                if number_tokens(partner.canon) != number_tokens(n.canon):
                    metrics["numeric_changed_pairs"] += 1
            elif change in ("cosmetic", "marker_only", "ambiguous"):
                metrics["%s_pairs" % change] += 1
            results.append(result_row(row_disp, change, changed_fields,
                                      s, n, partner, "exact_key_move"))
        else:
            leftover_added.append(n)

    # Pass 2: bounded fuzzy moves over what is still unpaired (small set).
    leftover_removed = [o for o in removed if id(o) not in matched_removed]
    used_rem = set()
    for n in leftover_added:
        best_o = None
        best_s = -1.0
        best_ri = None
        for ri, o in enumerate(leftover_removed):
            if ri in used_rem:
                continue
            s = similarity(o.canon, n.canon)
            if s >= pair_threshold and not can_fuzzy_pair(o, n):
                metrics["rejected_fuzzy_candidates"] += 1
                continue
            if s > best_s:
                best_s, best_o, best_ri = s, o, ri
        if best_o is not None and best_s >= pair_threshold:
            used_rem.add(best_ri)
            row_disp, change, changed_fields = classify_pair(best_o, n, moved=True)
            metrics["gated_fuzzy_moves"] += 1
            if change == "material":
                metrics["material_changed_pairs"] += 1
                if number_tokens(best_o.canon) != number_tokens(n.canon):
                    metrics["numeric_changed_pairs"] += 1
            elif change in ("cosmetic", "marker_only", "ambiguous"):
                metrics["%s_pairs" % change] += 1
            results.append(result_row(row_disp, change, changed_fields,
                                      best_s, n, best_o, "gated_fuzzy_move"))
        else:
            results.append(result_row("added", "", "", 0.0, n, None, "new_only"))

    # Whatever removed is still unpaired is genuinely removed.
    for ri, o in enumerate(leftover_removed):
        if ri not in used_rem:
            results.append(result_row("removed", "", "", 0.0, None, o, "old_only"))


def duplicate_result(row_disposition, entry, rep_result, old_side):
    """Build one duplicate-accountability report row."""
    old_entry = entry if old_side else None
    new_entry = None if old_side else entry
    old_rep = rep_result["old_representative"]
    new_rep = rep_result["new_representative"]
    if old_side:
        new_rep = rep_result["new_representative"]
    else:
        old_rep = rep_result["old_representative"]
    row = result_row(
        row_disposition,
        rep_result["representative_content_change"],
        rep_result.get("changed_fields", ""),
        rep_result["score"],
        new_entry,
        old_entry,
        "duplicate_of_representative",
        "inherits:%s/%s" % (
            rep_result["representative_row_disposition"],
            rep_result["representative_content_change"],
        ),
    )
    row["representative_row_disposition"] = rep_result["representative_row_disposition"]
    row["representative_content_change"] = rep_result["representative_content_change"]
    row["old_representative"] = old_rep
    row["new_representative"] = new_rep
    return row


def duplicate_pair_result(old_entry, new_entry, rep_result):
    """Build one row that accounts for matching OLD and NEW duplicates."""
    row = result_row(
        "duplicate_pair",
        rep_result["representative_content_change"],
        rep_result.get("changed_fields", ""),
        rep_result["score"],
        new_entry,
        old_entry,
        "duplicate_pair_of_representative",
        "inherits:%s/%s" % (
            rep_result["representative_row_disposition"],
            rep_result["representative_content_change"],
        ),
    )
    row["representative_row_disposition"] = rep_result["representative_row_disposition"]
    row["representative_content_change"] = rep_result["representative_content_change"]
    row["old_representative"] = rep_result["old_representative"]
    row["new_representative"] = rep_result["new_representative"]
    return row


def expand_duplicate_accountability(rep_results, old_groups, new_groups):
    """Expand representative comparison rows into a full source-row audit trail."""
    expanded = []
    for r in rep_results:
        expanded.append(r)
        old_rep = r["old"]
        new_rep = r["new"]
        old_dupes = old_groups.get(old_rep, [])[1:] if old_rep else []
        new_dupes = new_groups.get(new_rep, [])[1:] if new_rep else []

        if old_rep and new_rep:
            paired = min(len(old_dupes), len(new_dupes))
            for i in range(paired):
                expanded.append(duplicate_pair_result(old_dupes[i], new_dupes[i], r))
            for entry in old_dupes[paired:]:
                expanded.append(duplicate_result("base_duplicate", entry, r, True))
            for entry in new_dupes[paired:]:
                expanded.append(duplicate_result("compare_duplicate", entry, r, False))
        elif old_rep:
            for entry in old_dupes:
                expanded.append(duplicate_result("base_duplicate", entry, r, True))
        elif new_rep:
            for entry in new_dupes:
                expanded.append(duplicate_result("compare_duplicate", entry, r, False))
    return expanded


def write_report(results, path):
    """Write the detailed per-entry report CSV."""
    cols = [
        "row_disposition",
        "content_change",
        "representative_row_disposition",
        "representative_content_change",
        "changed_fields",
        "score",
        "base_source_id",
        "compare_source_id",
        "base_representative_id",
        "compare_representative_id",
        "match_reason",
        "flags",
        "base_name_key",
        "compare_name_key",
        "compare_page",
        "compare_chunk",
        "base_chunk",
        "base_listing",
        "compare_listing",
        "base_parsed_summary",
        "compare_parsed_summary",
        "base_parse_errors",
        "compare_parse_errors",
        "canon_base",
        "canon_compare",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in results:
            n = r["new"]
            o = r["old"]
            old_rep = r.get("old_representative")
            new_rep = r.get("new_representative")
            w.writerow([
                r["row_disposition"],
                r.get("content_change", ""),
                r.get("representative_row_disposition", r["row_disposition"]),
                r.get("representative_content_change", r.get("content_change", "")),
                r.get("changed_fields", ""),
                "%.4f" % r["score"],
                (o.source_id if o else ""),
                (n.source_id if n else ""),
                (old_rep.source_id if old_rep else ""),
                (new_rep.source_id if new_rep else ""),
                r.get("match_reason", ""),
                r.get("flags", ""),
                (o.name_key if o else ""),
                (n.name_key if n else ""),
                (n.row.get("Page", "") if n else ""),
                (n.row.get("Chunk", "") if n else ""),
                (o.row.get("Chunk", "") if o else ""),
                (o.listing if o else ""),
                (n.listing if n else ""),
                (o.parsed.summary if o else ""),
                (n.parsed.summary if n else ""),
                (";".join(o.parsed.parse_errors) if o else ""),
                (";".join(n.parsed.parse_errors) if n else ""),
                (o.canon if o else ""),
                (n.canon if n else ""),
            ])


def write_summary(rep_results, report_results, stats, path, pair_threshold,
                  old_entries, new_entries, old_reps, new_reps, metrics):
    """Write counts, diagnostics, and source-row accountability totals."""
    # Histogram over pairs that had a real comparison (have both new and old).
    buckets = [0] * 20  # 0.00-0.05 .. 0.95-1.00
    for r in rep_results:
        if r["new"] is not None and r["old"] is not None:
            b = min(int(r["score"] * 20), 19)
            buckets[b] += 1

    report_disp_stats = Counter(r["row_disposition"] for r in report_results)
    report_change_stats = Counter(r.get("content_change", "") for r in report_results)
    rep_change_stats = Counter(r.get("content_change", "") for r in rep_results)
    old_report_rows = sum(1 for r in report_results if r["old"] is not None)
    new_report_rows = sum(1 for r in report_results if r["new"] is not None)

    lines = []
    lines.append("catalog_edition_diff summary")
    lines.append("=" * 40)
    lines.append("pair-threshold:     %.3f" % pair_threshold)
    lines.append("comparison rule:    parsed munger components")
    lines.append("base side:          fresh v2 OCR")
    lines.append("compare side:       older v1 manual entry")
    lines.append("")
    lines.append("source accountability:")
    lines.append("  %-22s %6d rows" % ("BASE source", len(old_entries)))
    lines.append("  %-22s %6d rows" % ("BASE in report", old_report_rows))
    lines.append("  %-22s %6d rows" % ("COMPARE source", len(new_entries)))
    lines.append("  %-22s %6d rows" % ("COMPARE in report", new_report_rows))
    lines.append("  %-22s %6d rows" % ("BASE representatives", len(old_reps)))
    lines.append("  %-22s %6d rows" % ("COMPARE representatives", len(new_reps)))
    lines.append("  %-22s %6d rows" % ("BASE duplicates", len(old_entries) - len(old_reps)))
    lines.append("  %-22s %6d rows" % ("COMPARE duplicates", len(new_entries) - len(new_reps)))
    lines.append("")
    lines.append("representative counts by row_disposition:")
    for row_disp in ["matched", "moved", "added", "removed"]:
        lines.append("  %-15s %6d" % (row_disp, stats.get(row_disp, 0)))
    lines.append("  %-15s %6d" % ("TOTAL", sum(stats.values())))
    lines.append("")
    lines.append("representative counts by content_change:")
    for change in ["none", "cosmetic", "marker_only", "material", "ambiguous", ""]:
        label = change or "not_applicable"
        lines.append("  %-15s %6d" % (label, rep_change_stats.get(change, 0)))
    lines.append("  %-15s %6d" % ("TOTAL", sum(rep_change_stats.values())))
    lines.append("")
    lines.append("report rows by row_disposition:")
    for row_disp in ["matched", "moved", "added", "removed", "duplicate_pair",
                     "base_duplicate", "compare_duplicate"]:
        lines.append("  %-15s %6d" % (row_disp, report_disp_stats.get(row_disp, 0)))
    lines.append("  %-15s %6d" % ("TOTAL", sum(report_disp_stats.values())))
    lines.append("")
    lines.append("report rows by content_change:")
    for change in ["none", "cosmetic", "marker_only", "material", "ambiguous", ""]:
        label = change or "not_applicable"
        lines.append("  %-15s %6d" % (label, report_change_stats.get(change, 0)))
    lines.append("  %-15s %6d" % ("TOTAL", sum(report_change_stats.values())))
    lines.append("")
    lines.append("matching diagnostics:")
    for key in ["exact_key_pairs", "gated_fuzzy_pairs", "exact_key_moves",
                "gated_fuzzy_moves", "rejected_fuzzy_candidates",
                "material_changed_pairs", "cosmetic_pairs",
                "marker_only_pairs", "ambiguous_pairs",
                "numeric_changed_pairs"]:
        lines.append("  %-26s %6d" % (key, metrics.get(key, 0)))
    lines.append("")
    lines.append("score histogram (representative pairs only, 0.05 buckets):")
    for i, count in enumerate(buckets):
        lo = i * 0.05
        hi = lo + 0.05
        bar = "#" * min(count, 60)
        lines.append("  %4.2f-%4.2f %6d %s" % (lo, hi, count, bar))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_out(results, path):
    """Write COMPARE rows whose content is new/changed, in the 7 v2 columns."""
    # COMPARE-edition order (idx) for stability.
    out_rows = [r["new"] for r in results
                if r["new"] is not None
                and (r["row_disposition"] == "added"
                     or r.get("content_change") == "material")]
    out_rows.sort(key=lambda e: e.idx)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=V2_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for e in out_rows:
            # Emit verbatim values for exactly the 7 v2 columns.
            w.writerow({col: e.row.get(col, "") for col in V2_COLUMNS})
    return len(out_rows)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="VA-only fuzzy diff between two catalog editions in v2 CSV shape.")
    p.add_argument("--base", default="tools/wip/in/VA_ASCC_CTLG.csv",
                   help="BASE v2 OCR CSV (default: tools/wip/in/VA_ASCC_CTLG.csv)")
    p.add_argument("--compare", default="tools/wip/in/v1_VA_ocr.csv",
                   help="COMPARE v1 manual CSV (default: tools/wip/in/v1_VA_ocr.csv)")
    p.add_argument("--old", default=None,
                   help="deprecated alias for --compare")
    p.add_argument("--new", default=None,
                   help="deprecated alias for --base")
    p.add_argument("--report-dir", default="tools/wip/cache",
                   help="dir for the report + summary (default: tools/wip/cache)")
    p.add_argument("--out-dir", default="tools/wip/out",
                   help="dir for the new/modified out file (default: tools/wip/out)")
    p.add_argument("--modified-threshold", type=float, default=0.92,
                   help="deprecated; material changes now use parsed components")
    p.add_argument("--pair-threshold", type=float, default=0.55,
                   help="min canon similarity to pair entries in replace/move (default 0.55)")
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    base_arg = args.new if args.new is not None else args.base
    compare_arg = args.old if args.old is not None else args.compare
    old_path = Path(base_arg)
    new_path = Path(compare_arg)
    for path in (old_path, new_path):
        if not path.is_file():
            print("error: input not found: %s" % path, file=sys.stderr)
            return 1

    old_entries, old_conv = load_listings(old_path, "BASE")
    new_entries, new_conv = load_listings(new_path, "COMPARE")

    old_reps, old_groups = representative_groups(old_entries)
    new_reps, new_groups = representative_groups(new_entries)

    rep_results, stats, metrics = classify(old_reps, new_reps,
                                           args.pair_threshold)
    report_results = expand_duplicate_accountability(rep_results,
                                                     old_groups, new_groups)

    report_dir = Path(args.report_dir)
    out_dir = Path(args.out_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "va_edition_diff_report.csv"
    summary_path = report_dir / "va_edition_diff_summary.txt"
    out_path = out_dir / "v2_VA_new_modified.csv"

    write_report(report_results, report_path)
    write_summary(rep_results, report_results, stats, summary_path,
                  args.pair_threshold, old_entries, new_entries,
                  old_reps, new_reps, metrics)
    out_count = write_out(rep_results, out_path)

    print("base: %s" % old_path)
    print("compare: %s" % new_path)
    print("unicode->ASCII normalized rows: BASE %d, COMPARE %d" % (old_conv, new_conv))
    print("BASE listings: %d   COMPARE listings: %d" % (len(old_entries), len(new_entries)))
    print("BASE representatives: %d   COMPARE representatives: %d"
          % (len(old_reps), len(new_reps)))
    rep_changes = Counter(r.get("content_change", "") for r in rep_results)
    print("representative row_disposition:")
    for row_disp in ["matched", "moved", "added", "removed"]:
        print("  %-15s %6d" % (row_disp, stats.get(row_disp, 0)))
    print("representative content_change:")
    for change in ["none", "cosmetic", "marker_only", "material", "ambiguous"]:
        print("  %-15s %6d" % (change, rep_changes.get(change, 0)))
    print("out file (added or material): %d rows -> %s" % (out_count, out_path))
    print("report: %s" % report_path)
    print("summary: %s" % summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
