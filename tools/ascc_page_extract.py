"""ascc_page_extract.py -- single-pass ASCC chunk extraction.

Reads chunk PNGs at tools/wip/in/<basename>/page-NNNN-MMMM.png, sends each to
Claude Sonnet through the selected provider, and writes a 4-column CSV
(Listing, Page, Images Above, Type) to tools/wip/out/<basename>.csv for
apmc_data_munger.ipynb to consume downstream.

Pipeline position: downstream of tools/ascc_page_processor.py (which
produces the chunk PNGs); upstream of tools/apmc_data_munger.ipynb.

Usage:

    uv run python tools/ascc_page_extract.py VA_ASCC_CTLG
    uv run python tools/ascc_page_extract.py VA_ASCC_CTLG --pages 419-420
    uv run python tools/ascc_page_extract.py VA_ASCC_CTLG --pages 419 --force
    uv run python tools/ascc_page_extract.py VA_ASCC_CTLG -v

Cache:
    tools/wip/cache/<basename>_extract.json -- one entry per chunk; tagged with
    model id and EXTRACT_PROMPT_VERSION; invalidated on either change.

Post-filter (cosmetic; the downstream munger reclassifies from scratch):
    1. Bare integers up to 4 digits  -- printed page numbers and their
       sliced tails (e.g. "19" for "419").
    2. Substrings of the auto-derived state-header word (e.g. VIRG,
       INIA, GINIA when the catalog is VA_ASCC_CTLG). Exact-match of
       the full word is left alone (could be the legitimate state
       heading on its first appearance).
    3. Single all-caps display-type tokens at chunk position 0 of a
       chunk that contains LISTINGs -- postmark illustrations of city
       names that the model misread as section headings (the chunker
       guarantees markings sit at chunk top, so a lone city name there
       is structurally an illustration even when the model reported
       images_above=0).
"""

import argparse
import base64
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from pipeline_llm import (
    DEFAULT_OPENROUTER_MODEL,
    PROVIDERS,
    make_pipeline_llm,
    resolve_model,
    resolve_provider,
)


# Script-root paths let this tool run from any cwd while preserving the
# historical tools/wip layout.
TOOLS_DIR = Path(__file__).resolve().parent
WIP_DIR = TOOLS_DIR / "wip"

# Repo-root .env.
load_dotenv(TOOLS_DIR.parent / ".env")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_MODEL          = DEFAULT_OPENROUTER_MODEL
EXTRACT_PROMPT_VERSION = "v9"
# Claude Sonnet 4.6 advertises a 200K input window and 64K output cap on
# OpenRouter. 16000 is plenty for a single-chunk extraction (the densest
# observed chunk has under 30 entries at ~120 tokens each = ~3600
# tokens). Cap kept well below the model max to avoid runaway costs on
# a degenerate response. Cache is tagged with provider, model id, and
# prompt version.
EXTRACT_MAX_TOKENS     = 16000

# Reference table for state-header derivation (USPS abbrev -> region name).
# Loaded rows have region_tier in {STATE, DISTRICT}; COUNTRY rows are
# skipped.
REGIONS_CSV = WIP_DIR / "in" / "regions.csv"


EXTRACT_SYSTEM_PROMPT = """OUTPUT (MANDATORY): one line of minified JSON, no fences, no whitespace
outside string values.
Shape: {"images_above":0,"entries":[{"text":"...","type":"LISTING"},...]}

COMPRESSION (MANDATORY): collapse runs of 3+ dots to "...", 3+ dashes to
"--". Example: "Mt. Pleasant ............ 1845-51 ............ 15.00"
becomes "Mt. Pleasant ... 1845-51 ... 15.00".

You are reading chunks of an old American Stampless Cover (ASCC)
catalog. A chunk is either pure catalog text, or a marking-image
section at the top followed by catalog text. Emit every visible TEXT
entry in top-to-bottom order; classify each LISTING or META; return
ONE chunk-level integer count of distinct marking images at the top.

TYPOGRAPHY IS THE BOUNDARY (MANDATORY READ FIRST)
-------------------------------------------------
Marking images are GRAPHICS: handwritten script, cursive ink,
display-letter postmark designs, drawn frames, oval/box cancels.
Catalog rows are TYPEWRITTEN/PRINTED body text in a uniform serif or
sans-serif font with leader dots ("....") and a trailing number or
"--". These two typographic styles never belong to the same element.
A script "RICH^D Jan 14" at top and a typewritten "RICHD.(...)" line
beneath it are TWO SEPARATE THINGS even when they share a name: the
script is the image, the typewritten row is a LISTING. Emit the
LISTING. Count the script in images_above. NEVER fold a typewritten
row into the image.

EXHAUSTIVENESS (MANDATORY)
--------------------------
Every typewritten/printed line of body text visible in the chunk MUST
appear as an entry in the output. Before finalizing, mentally re-scan
the chunk top to bottom and verify each printed row is present. If
you see N printed rows in the image, your entries array must contain
N items (LISTING + META combined). Missing a printed row is a hard
failure.

LISTING signals (any one suffices)
----------------------------------
- trailing valuation: number like 1500.00, or "--", or "---"
- semicolon-parenthetical: (...; ...; ...)
- 4-digit year matching 17xx or 18xx
- relationship prefix: literal "Same", "(L)", or "(E)"

Examples (verbatim):
  Alexa.(Alexandria)(E)(May 21,1772;Ms;Black) ......... 1500.00
  Fredg.(Aug.2,1772;Ms;Black) .. 1500.00
  (L)(Sept.15,1774) . . .. 1000.00
  Same(Aug.2,1772;Ms;Black) .. 1500.00

LISTING-SIGNAL OVERRIDE (HARD RULE)
-----------------------------------
If a line carries ANY LISTING signal above, it is a LISTING. Period.
Never demote it to "text inside a marking image" or drop it as page
furniture, even when the marking image directly above shares the same
place name. The marking image is the script/handwritten/display-type
graphic only; the typewritten catalog row beneath it -- with its
parenthetical(date;size;color) and leader-dotted price -- is ALWAYS a
separate LISTING and MUST be emitted.

Counter-example (the model has gotten this wrong):
  Chunk shows a script postmark "RICH^D Jan 14" at top, then below:
    RICHD.("D"high)(E)(Oct.24,1786;SL-23x5,MDD;Black) ... 500.00
    (L)(March 6,1787) ... 400.00
  CORRECT: images_above=1, entries=[both LISTING lines].
  WRONG:   dropping the "RICHD.(...)..." LISTING because its name
           echoes the marking's "RICH^D" -- this is a forbidden
           demotion. Emit it.

A wrapped continuation line beginning with "(" but NOT with "Same" is
part of the SAME LISTING above -- join into one string. A line
beginning with literal "Same(" is a SEPARATE LISTING -- emit each
Same(...) as its own entry.

META is everything else that is not a LISTING and not page furniture.
Legitimate META: state/section headings on first appearance ("VIRGINIA"
at top of state's coverage, "BRITISH COLONIAL PERIOD", "STATEHOOD
PERIOD", "MANUSCRIPT TOWN MARKS"); column headers ("Town Postmark
Dates Seen Size Color Value"); editorial paragraphs; cross-references
("See Alexandria") without semicolon-paren; isolated text fragments.

DO NOT EMIT -- not as LISTING, not as META, not at all
------------------------------------------------------

1. Page running heads and their fragments. The state name "VIRGINIA"
   sits at the very top edge of every page. Sliced fragments like
   "VIRG", "INIA", "GINIA", "...GINIA", "CINIA" (OCR-confused) are
   page furniture -- OMIT.

2. Bare page numbers at the very bottom edge -- "419", "427", and
   sliced tails ("19" for "419"). OMIT. Never confuse a page number
   for a year, price, or section heading.

3. Text rendered INSIDE a marking image: place names in postmark
   designs ("LEWISBURGH VA."), date annotations ("MAY 7"), rate
   numerals ("5"), framed words ("PAID", "FREE", "DUE"), manuscript
   samples ("Salem Bot(etourt Co)"). These are part of the
   illustration -- count in images_above; never emit as text.

   TYPOGRAPHY TEST: "inside a marking image" means rendered in the
   SAME hand-drawn / script / display-letter style as the rest of the
   image. A row rendered in uniform typewritten/printed body-text
   font, with leader dots and a trailing price or "--", is NOT inside
   the image -- it is a separate LISTING beneath the image. Emit it.

4. Isolated city/town names in BIG DISPLAY LETTERING at chunk top --
   "SUFFOLK", "NORFOLK", "NEWCASTLE", "FREDERICKSBURG". These are
   POSTMARK ILLUSTRATIONS, not section headings. Diagnostic: if the
   LISTING immediately below begins with the same place name (chunk
   shows "SUFFOLK" then
   "SUFFOLK(April 12,1775;SL-30x5,...) ... 1500.00"), the top
   display-type name IS the postmark for that listing. Count in
   images_above; never emit as text. A bare single city name at chunk
   top is NEVER a section heading in this catalog.

   SCOPE LIMIT: this "do not emit" rule applies ONLY to the bare
   display-type name itself (the standalone "SUFFOLK", the script
   "RICH^D Jan 14"). It does NOT extend to the typewritten LISTING line
   beneath it. The LISTING-SIGNAL OVERRIDE wins: if the line below
   carries any LISTING signal, emit it as a LISTING. Never collapse a
   marking image AND the catalog row beneath it into a single image.

Text fidelity
-------------

Reproduce text EXACTLY: case, spacing, punctuation, semicolons,
slashes, parens, leader dots, trailing prices. Do NOT parse,
normalize, expand abbreviations, or clean up.

ASCII only -- straight " and ', use -/-- not en/em-dash, three dots
... not ellipsis char, no accented letters or Unicode bullets/arrows.

If text is fully illegible, OMIT. Do not fabricate. If partly
illegible, emit what you can read and use ? for unreadable glyphs.

Counting images_above
---------------------

ONE integer per chunk: 0 if pure text; else count distinct
marking-image reproductions in the top section. ONE stamp = ONE image
(a small adjacent date or rate annotation is part of the same image).
Two genuinely separate stamp reproductions placed close together = TWO
images. If unsure between 1 and 2, prefer 1.

If a chunk is pure illustration with no catalog text below, return
{"images_above":N,"entries":[]}.
"""


# ---------------------------------------------------------------------------
# Paths and tee
# ---------------------------------------------------------------------------

class Paths:
    """Per-run filesystem layout, derived from the basename."""
    def __init__(self, basename):
        self.basename   = basename
        self.images_dir = WIP_DIR / "in" / basename
        self.output_csv = WIP_DIR / "out" / f"{basename}.csv"
        self.cache_file = WIP_DIR / "cache" / f"{basename}_extract.json"
        self.run_log    = WIP_DIR / "cache" / f"{basename}_extract.log"


class _Tee:
    """Minimal stdout duplicator: writes to multiple streams."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


# Verbose log file handle, set by main() when --verbose is given. log_only()
# writes a line here and nowhere else, so model-id detail stays in the log
# without printing to the console. None (and a no-op) outside verbose runs.
_LOG_FH = None


def log_only(msg):
    """Write one line to the verbose log file only -- never the console.

    No-op when no log file is open (non-verbose runs), so the model id is
    recorded in the log but kept off the console.
    """
    if _LOG_FH is not None:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()


# ---------------------------------------------------------------------------
# State-header derivation from regions.csv
# ---------------------------------------------------------------------------

def load_region_map(csv_path):
    """Return {USPS_ABBREV_UPPER: REGION_NAME_UPPER} from the regions
    reference CSV. Skip rows whose region_tier is not STATE or DISTRICT
    (e.g. the COUNTRY row for USA). Returns {} and prints a one-line
    warning if csv_path is missing."""
    if not csv_path.exists():
        print(f"warning: {csv_path} not found; "
              f"state-header derivation disabled")
        return {}
    out = {}
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("region_tier") not in ("STATE", "DISTRICT"):
                continue
            abbrev = (row.get("abbrev") or "").strip().upper()
            name = (row.get("name") or "").strip().upper()
            if abbrev and name:
                out[abbrev] = name
    return out


def derive_state_header(basename, region_map):
    """Map basename like 'VA_ASCC_CTLG' to 'VIRGINIA'. Returns None if
    the basename's first '_'-delimited token is not in region_map."""
    prefix = basename.split("_", 1)[0].upper()
    return region_map.get(prefix)


# ---------------------------------------------------------------------------
# --pages parser (subset of ascc_page_processor's: no :pdf branch since
# chunk filenames already use the catalog page number)
# ---------------------------------------------------------------------------

def parse_pages_arg(s):
    """Parse the --pages argument. Returns either None (no filter) or a
    set[int] of catalog page numbers. Forms accepted:

        419            single page
        419-435        inclusive range
        419,422,430    explicit list
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                raise argparse.ArgumentTypeError(
                    f"--pages range {part}: lo > hi"
                )
            ids.update(range(lo, hi + 1))
            continue
        m = re.match(r"^(\d+)$", part)
        if m:
            ids.add(int(m.group(1)))
            continue
        raise argparse.ArgumentTypeError(
            f"--pages part {part!r} not understood; "
            f"expected NNN, NNN-NNN, or list"
        )
    if not ids:
        raise argparse.ArgumentTypeError("--pages produced an empty set")
    return ids


# ---------------------------------------------------------------------------
# Extraction helpers (ports from apmc_page_extract.ipynb cell 35c726a3)
# ---------------------------------------------------------------------------

_DOT_RE  = re.compile(r"\.{3,}")
_DASH_RE = re.compile(r"-{3,}")
_SPC_RE  = re.compile(r" {2,}")


def _compress_leaders(text):
    """Collapse 3+ runs of dots, 3+ runs of dashes, and 2+ runs of
    spaces. Mirrors the in-prompt TEXT COMPRESSION instruction so the
    same canonicalisation runs on partial-parse output too."""
    text = _DOT_RE.sub("...", text)
    text = _DASH_RE.sub("--", text)
    text = _SPC_RE.sub(" ", text)
    return text.strip()


def _strip_fences(text):
    """Drop ```json ... ``` fences if the model added them."""
    t = text.strip()
    if t.startswith("```"):
        # drop opening fence line (e.g. "```json")
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        # drop trailing fence and any text after it
        last_fence = t.rfind("\n```")
        if last_fence != -1:
            t = t[:last_fence]
    return t.strip()


def _parse_partial(text):
    """Recover complete entries from a truncated JSON response. Returns
    a valid result dict or None if nothing recoverable."""
    ia_m = re.search(r'"images_above"\s*:\s*(\d+)', text)
    images_above = int(ia_m.group(1)) if ia_m else 0
    entries = []
    pattern = re.compile(
        r'\{"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"type"\s*:\s*"(LISTING|META)"\s*\}'
    )
    for m in pattern.finditer(text):
        entries.append(
            {"text": _compress_leaders(m.group(1)), "type": m.group(2)}
        )
    if not entries and not ia_m:
        return None
    return {"images_above": images_above, "entries": entries}


def _call_model(llm, model, image_b64, user_prompt):
    content = llm.vision_text(
        model=model,
        max_tokens=EXTRACT_MAX_TOKENS,
        system_prompt=EXTRACT_SYSTEM_PROMPT,
        user_text=user_prompt,
        image_b64=image_b64,
    )
    if not content:
        raise ValueError("model returned empty content")
    return content


def extract_chunk(llm, model, image_path, page, chunk_seq):
    """Send one chunk PNG to the vision model and return the parsed
    {images_above, entries} dict. Retries once on JSON parse failure;
    falls back to _parse_partial on a second failure."""
    user_prompt = (
        f"Chunk {chunk_seq} of ASCC catalog page {page}. "
        "Reply with MINIFIED JSON on ONE LINE, no fences, no extra whitespace. "
        "Compress dot/dash runs to ... or --. "
        'Shape: {"images_above":0,"entries":[{"text":"...","type":"LISTING|META"},...]}\n'
        "No preamble. No explanation. Just the JSON."
    )
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()

    last_raw = ""
    for _attempt in range(2):
        raw = _call_model(llm, model, image_b64, user_prompt)
        last_raw = raw
        cleaned = _strip_fences(raw)
        if not cleaned:
            continue
        try:
            parsed = json.loads(cleaned)
            break
        except json.JSONDecodeError:
            continue
    else:
        partial = _parse_partial(last_raw)
        if partial is not None:
            print(
                f"    WARNING: partial parse used "
                f"({len(partial['entries'])} entries recovered)"
            )
            parsed = partial
        else:
            head = last_raw[:200]
            tail = last_raw[-200:] if len(last_raw) > 400 else ""
            snippet = head + ("..." if tail else "") + tail
            raise ValueError(
                f"model returned non-JSON after 2 attempts; raw={snippet!r}"
            )

    assert isinstance(parsed, dict), f"top-level not dict: {type(parsed)}"
    images_above = parsed.get("images_above")
    assert isinstance(images_above, int) and images_above >= 0, \
        f"images_above bad: {images_above!r}"
    entries = parsed.get("entries")
    assert isinstance(entries, list), f"entries not list: {type(entries)}"
    for i, r in enumerate(entries):
        assert isinstance(r, dict), f"entry[{i}] not dict"
        assert isinstance(r.get("text"), str), f"entry[{i}].text not str"
        assert r.get("type") in ("LISTING", "META"), \
            f"entry[{i}].type bad: {r.get('type')!r}"
        r["text"] = _compress_leaders(r["text"])
    return {"images_above": images_above, "entries": entries}


# ---------------------------------------------------------------------------
# Cache (mirrors ascc_page_processor.py)
# ---------------------------------------------------------------------------

def load_cache(path, provider, model, version):
    """Load a cache file, invalidating it if provider/model/prompt changed."""
    if not path.exists():
        return {
            "provider": provider,
            "model": model,
            "prompt_version": version,
            "responses": {},
        }
    cache = json.loads(path.read_text())
    cache_provider = cache.get("provider", "openrouter")
    if (
        cache_provider != provider
        or cache.get("model") != model
        or cache.get("prompt_version") != version
    ):
        print(
            f"cache invalidated at {path.name} "
            f"(was provider={cache_provider!r}, "
            f"model={cache.get('model')!r}, "
            f"prompt={cache.get('prompt_version')!r})"
        )
        return {
            "provider": provider,
            "model": model,
            "prompt_version": version,
            "responses": {},
        }
    cache["provider"] = cache_provider
    return cache


def save_cache(path, cache):
    path.write_text(json.dumps(cache, indent=2))


# ---------------------------------------------------------------------------
# Post-filter (ports from apmc_page_extract.ipynb cell d9483af0)
# ---------------------------------------------------------------------------

_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_LEADER_STRIP_RE = re.compile(r"^[.\s]+|[.\s]+$")


def _is_garbage_meta(text, state_header):
    """Drop bare page numbers (1-4 digit integers) and substrings of
    state_header (running-head fragments). Exact-match state_header is
    left alone (could be the legitimate state heading on first
    appearance). When state_header is None, only the page-number rule
    applies."""
    core = _LEADER_STRIP_RE.sub("", text).strip()
    if _PAGE_NUMBER_RE.match(core):
        return True
    if state_header:
        upper = core.upper()
        if (2 <= len(upper) < len(state_header)
                and upper.isalpha()
                and upper in state_header):
            return True
    return False


def _is_city_illustration_leak(text, position, chunk_listing_count):
    """Structural rule from the chunker: markings sit at the TOP of a
    chunk, listing(s) below. So an isolated all-caps display-type place
    name at chunk position 0 of a chunk that contains LISTINGs is the
    postmark illustration for those listings, even if the model
    reported images_above=0. Whitelist short, single uppercase
    alphabetic tokens; the legitimate state heading "VIRGINIA" is not
    caught because its chunk contains no LISTINGs (the state-section
    opener is a pure text chunk of intro paragraphs)."""
    if position != 0 or chunk_listing_count == 0:
        return False
    t = text.strip()
    return 3 <= len(t) <= 20 and t.isalpha() and t.isupper()


# ---------------------------------------------------------------------------
# Chunk discovery + run loop
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^page-(\d{4})-(\d{4})\.png$")


def discover_chunks(images_dir):
    """Return list of (page, chunk_seq, path) tuples sorted in catalog
    reading order (ascending page, then ascending chunk_seq)."""
    chunks = []
    for p in sorted(images_dir.glob("page-*-*.png")):
        m = NAME_RE.match(p.name)
        if not m:
            continue
        chunks.append((int(m.group(1)), int(m.group(2)), p))
    chunks.sort(key=lambda t: (t[0], t[1]))
    return chunks


def run_extract(paths, provider, model, page_filter, force, llm):
    """Loop through chunks, query the model where needed, save cache
    after each call (so a crash mid-loop only loses the in-flight
    response). Returns (cache, calls_made)."""
    cache = load_cache(
        paths.cache_file,
        provider,
        model,
        EXTRACT_PROMPT_VERSION,
    )
    responses = cache["responses"]
    chunks = discover_chunks(paths.images_dir)
    if not chunks:
        raise SystemExit(f"no PNGs matched in {paths.images_dir}")

    total = len(chunks)
    in_scope = (
        chunks if page_filter is None
        else [c for c in chunks if c[0] in page_filter]
    )
    if page_filter is not None:
        print(f"chunks: {total} total, {len(in_scope)} in scope after --pages")
    else:
        print(f"chunks: {total} total")

    calls_made = 0
    for page, chunk_seq, img_path in in_scope:
        key = f"page-{page:04d}-{chunk_seq:04d}"
        if key in responses and not force:
            continue
        if not img_path.exists():
            print(f"missing image, skipping: {img_path}")
            continue
        try:
            result = extract_chunk(llm, model, img_path, page, chunk_seq)
        except Exception as e:
            print(f"  {key}: FAILED ({type(e).__name__}: {e})")
            save_cache(paths.cache_file, cache)
            raise
        responses[key] = result
        save_cache(paths.cache_file, cache)
        calls_made += 1
        n = len(result["entries"])
        print(
            f"  {key}: {n:3d} entries, images_above={result['images_above']}"
        )

    print()
    print(f"calls made: {calls_made}")
    print(f"cached responses: {len(responses)}")
    return cache, calls_made


# ---------------------------------------------------------------------------
# DataFrame assembly + CSV write
# ---------------------------------------------------------------------------

CSV_COLUMNS = ["Listing", "Page", "Chunk", "Images Above", "Type"]


def assemble_rows(chunks, responses, state_header):
    """Walk chunks in reading order, apply both META post-filters,
    build the row list. The Images Above count attaches to the FIRST
    surviving entry of each chunk (so dropping the first entry as
    garbage does not lose the count). Returns (rows, dropped_meta)
    where dropped_meta is a list of (key, text) tuples."""
    rows = []
    dropped_meta = []
    for page, chunk_seq, _ in chunks:
        key = f"page-{page:04d}-{chunk_seq:04d}"
        entry = responses.get(key)
        if entry is None:
            print(f"WARNING: no cached response for {key}; skipping")
            continue
        chunk_images = int(entry["images_above"])
        chunk_listing_count = sum(
            1 for e in entry["entries"] if e["type"] == "LISTING"
        )
        emitted_in_chunk = 0
        for pos, r in enumerate(entry["entries"]):
            if r["type"] == "META" and (
                _is_garbage_meta(r["text"], state_header)
                or _is_city_illustration_leak(
                    r["text"], pos, chunk_listing_count
                )
            ):
                dropped_meta.append((key, r["text"]))
                continue
            rows.append({
                "Listing": r["text"],
                "Page": int(page),
                "Chunk": int(chunk_seq),
                "Images Above": chunk_images if emitted_in_chunk == 0 else 0,
                "Type": r["type"],
            })
            emitted_in_chunk += 1
    return rows, dropped_meta


def write_csv(rows, output_csv):
    """RFC-4180 CSV: UTF-8, header row, double-quoted only when needed
    (quoting=QUOTE_MINIMAL), LF line endings (matches pandas default and
    git-friendly diffs)."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=CSV_COLUMNS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _print_summary(rows):
    """Print the per-page / per-Images-Above / per-Type counts that the
    notebook used to print via pandas. Sorted by key for stable output."""
    if not rows:
        return
    pages = [r["Page"] for r in rows]
    print(f"pages: {min(pages)}-{max(pages)}")
    print()
    print("rows per page:")
    page_counts = Counter(pages)
    for page in sorted(page_counts):
        print(f"  {page:>4d}  {page_counts[page]:>5d}")
    print()
    print("Images Above value counts:")
    ia_counts = Counter(r["Images Above"] for r in rows)
    for v in sorted(ia_counts):
        print(f"  {v:>2d}  {ia_counts[v]:>5d}")
    print()
    print("Type value counts:")
    type_counts = Counter(r["Type"] for r in rows)
    for t in sorted(type_counts):
        print(f"  {t:<8s}  {type_counts[t]:>5d}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=("Single-pass ASCC chunk extraction. Reads chunk PNGs "
                     "from tools/wip/in/<basename>/page-NNNN-MMMM.png, sends each "
                     "to Claude Sonnet through the selected provider, and writes "
                     "tools/wip/out/<basename>.csv."),
    )
    parser.add_argument(
        "basename",
        help=("base name of the catalog (e.g. VA_ASCC_CTLG). Drives the "
              "input dir tools/wip/in/<basename>/, output CSV "
              "tools/wip/out/<basename>.csv, and cache "
              "tools/wip/cache/<basename>_extract.json."),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=("model id used for every chunk vision call. Default: "
              "PIPELINE_LLM_MODEL if set, otherwise provider-specific "
              f"default ({DEFAULT_MODEL} for OpenRouter). The cache is "
              "tagged with the provider and model id and invalidates "
              "automatically on change."),
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=("LLM provider for chunk vision calls. Default: "
              "PIPELINE_LLM_PROVIDER if set, otherwise openrouter."),
    )
    parser.add_argument(
        "--pages",
        type=parse_pages_arg,
        default=None,
        help=("restrict the API loop to a page range. Forms: '419', "
              "'419-435', '419,422,430'. Default: no filter (every "
              "chunk is processed). The output CSV always reflects the "
              "FULL cache regardless of this filter -- --pages only "
              "scopes which chunks are queried."),
    )
    parser.add_argument(
        "--state-header",
        default=None,
        help=("override the running-head word used by the META post-filter "
              "(e.g. VIRGINIA). Default: derived from the basename's "
              "USPS prefix via tools/wip/in/regions.csv (VA -> VIRGINIA, "
              "MA -> MASSACHUSETTS, ...)."),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=("re-query every in-scope chunk, ignoring the cache. Scoped "
              "to --pages when set. Default: cached chunks are skipped."),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=("tee stdout to tools/wip/cache/<basename>_extract.log so re-running "
              "just to re-read the log is unnecessary."),
    )
    args = parser.parse_args(argv)

    paths = Paths(args.basename)
    provider = resolve_provider(args.provider)
    model = resolve_model(provider, args.model)

    paths.cache_file.parent.mkdir(parents=True, exist_ok=True)
    paths.output_csv.parent.mkdir(parents=True, exist_ok=True)

    global _LOG_FH
    log_fh = None
    saved_stdout = sys.stdout
    if args.verbose:
        log_fh = open(paths.run_log, "a")
        ts = datetime.now().isoformat(timespec="seconds")
        argv_str = " ".join(sys.argv[1:])
        log_fh.write(f"\n========== {ts}  argv: {argv_str} ==========\n")
        log_fh.flush()
        sys.stdout = _Tee(saved_stdout, log_fh)
        _LOG_FH = log_fh

    try:
        if args.verbose:
            print(f"verbose log: tee-ing to {paths.run_log}")

        # Resolve state_header.
        if args.state_header:
            state_header = args.state_header.upper()
            print(f"state header: {state_header} (from --state-header)")
        else:
            region_map = load_region_map(REGIONS_CSV)
            state_header = derive_state_header(args.basename, region_map)
            if state_header:
                prefix = args.basename.split("_", 1)[0].upper()
                print(
                    f"state header: {state_header} "
                    f"({prefix} via {REGIONS_CSV})"
                )
            else:
                print(
                    "state header: not derived; running-head fragment "
                    "filter disabled. Pass --state-header to enable."
                )

        print(f"basename: {paths.basename}")
        print(f"provider: {provider}")
        log_only(f"provider: {provider}")
        print(f"model:    {model}")
        log_only(f"model:    {model}")
        if args.pages is not None:
            sample = ",".join(str(x) for x in sorted(args.pages)[:6])
            more = "..." if len(args.pages) > 6 else ""
            print(f"pages:    count={len(args.pages)} ({sample}{more})")
        else:
            print("pages:    (no filter)")
        if args.force:
            print("force:    yes (re-query in-scope chunks)")
        print(f"images:   {paths.images_dir}")
        print(f"output:   {paths.output_csv}")
        print(f"cache:    {paths.cache_file}")
        print()

        llm = make_pipeline_llm(provider)
        cache, _calls_made = run_extract(
            paths,
            provider=provider,
            model=model,
            page_filter=args.pages,
            force=args.force,
            llm=llm,
        )

        # Assemble the full CSV from every cached response (not scoped to
        # --pages -- partial runs must not clobber complete CSVs with a
        # subset).
        chunks = discover_chunks(paths.images_dir)
        rows, dropped_meta = assemble_rows(
            chunks, cache["responses"], state_header
        )

        print()
        print(f"rows: {len(rows):,}")
        _print_summary(rows)
        print()
        print(f"meta rows dropped as page furniture: {len(dropped_meta)}")
        if dropped_meta:
            print("  sample:")
            for key, text in dropped_meta[:10]:
                print(f"    [{key}] {text!r}")

        write_csv(rows, paths.output_csv)
        print()
        print(f"wrote: {paths.output_csv} ({len(rows):,} rows)")
    finally:
        if log_fh is not None:
            sys.stdout = saved_stdout
            log_fh.close()
            _LOG_FH = None


if __name__ == "__main__":
    main()
