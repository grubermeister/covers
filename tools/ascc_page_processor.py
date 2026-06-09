"""ascc_page_processor.py -- merged ASCC catalog page processor.

Replaces apmc_page_split.ipynb + halfpage_image_cutter.ipynb with one
script that runs end-to-end through the selected LLM provider.

Pipeline (three stages, gateable from the CLI):

    A. render  -- pdftoppm renders the PDF into tools/wip/cache/<BASE>_full/
                  page-NNNN.png (NNNN is the PDF page index).
    B. halves  -- per page: deterministic vertical-rule detection +
                  vision page-number call (header+footer strip) +
                  vision single-column-confirm fallback when no rule;
                  crop to tools/wip/cache/<BASE>_halves/page-NNNN-{L,R}.png
                  (or page-NNNN.png for single-column pages, where NNNN
                  is the catalog page number).
    C. chunks  -- per half: deterministic row-by-row dark/blank block
                  detector inside the half + vision per-block classify
                  (illustration vs text) + cut at the top of every
                  illustration block; write slices into tools/wip/out/<BASE>/
                  as page-NNNN-MMMM.png with MMMM running 1..N across
                  L then R per catalog page (matches what
                  apmc_page_extract.ipynb already consumes).

Usage:

    uv run python tools/ascc_page_processor.py <BASE> [--stages STAGES]
                                                        [--pages RANGE]
                                                        [--force STAGES]

See main() for argument details.
"""

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
from dotenv import load_dotenv

from pipeline_llm import (
    DEFAULT_OPENROUTER_MODEL,
    PROVIDERS,
    make_pipeline_llm,
    resolve_model,
    resolve_provider,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Script-root paths let this tool run from any cwd while preserving the
# historical tools/wip layout.
TOOLS_DIR = Path(__file__).resolve().parent
WIP_DIR = TOOLS_DIR / "wip"

# Repo-root .env.
load_dotenv(TOOLS_DIR.parent / ".env")

# Per-run paths are derived from --basename in main(); see Paths dataclass below.

DPI = 300

# Pass-1 deterministic rule-detector parameters. The printed rule sits very
# near width//2; search a generous band but assert the result lands within
# RULE_CENTER_TOLERANCE of true centre so drift gets noticed.
RULE_SEARCH_FRACTION   = 0.20   # search +/- 10% of width around centre
RULE_BAND_TOP_FRAC     = 0.20   # ignore top 20% of page (header)
RULE_BAND_BOTTOM_FRAC  = 0.85   # ignore bottom 15% of page (footer)
RULE_DARK_THRESHOLD    = 128    # pixel value below which we count as 'dark'
RULE_MIN_DARK_FRACTION = 0.70   # column must be dark for >=70% of band height
# Used only after vision has confirmed a page is two-column. Some scans have a
# real printed rule that is broken/faint enough to miss the strict pass, but
# still clearly dominates the center search band.
RULE_WEAK_MIN_DARK_FRACTION = 0.50
RULE_CENTER_TOLERANCE  = 0.05   # rule must lie within +/- 5% of width//2

# Pass-1 vision strip sizes (300 DPI -> 1 inch = 300 px).
HEADER_STRIP_HEIGHT  = 250
FOOTER_STRIP_HEIGHT  = 250
HEADER_FOOTER_SEP_PX = 6

# Pass-2 (block detector) parameters -- lifted from halfpage_image_cutter.
DARK_BRIGHTNESS_MAX = 180   # pixel < this counts as dark
ROW_DARK_MIN_PIXELS = 2     # row counts as non-blank if it has >= this many dark pixels
BLANK_RUN           = 5     # rows of blank to start/end a block
CENTER_FRACTION     = 0.90  # scan only the center 90% of width

# Minimum slice height. Anything thinner than this gets merged into the
# previous slice rather than emitted as its own chunk; protects the
# extract step from receiving a 12-pixel-tall sliver.
MIN_SLICE_HEIGHT_PX = 60

# Backward-compatible OpenRouter model id. Override per run with --model.
# Cache files invalidate automatically whenever provider, model id, or prompt
# version changes.
DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL

# Per-call prompt versions. Bump to invalidate the corresponding disk cache
# without changing the model id.
HALVES_PROMPT_VER = "g1"  # g1 = first OpenRouter revision
# A reasoning model can spend the entire token budget on hidden reasoning
# before producing any visible output; if max_tokens is too tight the visible
# response comes back empty with finish_reason='stop'. The extract notebook
# uses 64000 to dodge this. Stay generous.
HALVES_MAX_TOKENS = 4096

BLOCKS_PROMPT_VER = "g1"
BLOCKS_MAX_TOKENS = 1024

# Per-slice review pass: after the deterministic block detector + per-block
# classifier produce a first cut of slices, we send each slice back to the
# model with an entry-aware prompt and ask whether it should be split further.
# This catches cases where a marking and its own listing got merged into one
# block (and so the classifier saw it as text), and also correctly leaves
# multi-marking-shared-listing entries intact.
REVIEW_PROMPT_VER = "g2"
REVIEW_MAX_TOKENS = 1024

# Whenever the model returns a review cut, snap it to the middle of the
# nearest BLANK_RUN+ blank-row run within +/- SNAP_TOLERANCE_PX. If no such
# gap exists inside the search window, reject the cut. This guarantees:
#   - cuts always land in real white space (no slicing through text rows);
#   - false positives (model says split but there is no real entry boundary
#     inside the slice) get rejected because pure-text blocks have no blank
#     run >= BLANK_RUN inside them.
SNAP_TOLERANCE_PX = 80


PAGE_NUMBER_SYSTEM_PROMPT = """You receive a stitched image showing the TOP margin and BOTTOM margin of one page of an old American Stampless Cover (ASCC) catalog. The two margins are stacked vertically with a black separator bar between them.

Your job is to read the printed catalog page number. It appears as a plain integer somewhere in either margin -- usually in the footer, occasionally in the header.

Return STRICT JSON only -- no markdown, no prose, no code fences:

  {"page_number": 419}

Rules:
- page_number is an integer >= 1.
- Never return null. If you cannot read it confidently, return your best integer guess.
- Output JSON only."""

SINGLE_COL_SYSTEM_PROMPT = """You receive one page from an old American Stampless Cover (ASCC) catalog.

A typical page has TWO text columns separated by a thin printed vertical rule running most of the page height. Some pages do NOT have that two-column layout -- they may be a full-page plate of postmark illustrations, a section divider, a blank page, or a single-column body of text with no rule.

Your job is to decide whether this page is laid out as TWO columns separated by a printed vertical rule.

Return STRICT JSON only -- no markdown, no prose, no code fences:

  {"has_two_columns": true}

or

  {"has_two_columns": false}

Output JSON only."""

BLOCK_CLASSIFY_SYSTEM_PROMPT = """You receive a horizontal strip from a scanned philatelic catalogue page (one half-page wide). It contains EXACTLY ONE of the following:

  illustration -- a visual reproduction of a postal marking (postmark, handstamp, or manuscript marking).
  text -- listing rows, section banners, column headers, or running headers.

Return STRICT JSON on one line, no markdown, no prose, no code fences:

  {"kind": "illustration"}

or

  {"kind": "text"}

Output JSON only."""

REVIEW_SLICE_SYSTEM_PROMPT = """You are reviewing one chunk image cut from a single column of an old American Stampless Cover (ASCC) catalog. Decide whether this chunk should be split into MORE THAN ONE catalog entry, and if so, return the y-pixel offsets where additional horizontal cuts go.

This is a STAMPLESS COVER catalog. The images on these pages are postal markings -- postmarks, handstamps, and manuscript markings. They are NEVER adhesive postage stamps.

== ONE RULE ==

A cut goes ONLY at a row where a NEW MARKING IMAGE begins, and the row immediately above that marking is the END of a previous listing (text). In other words, a cut separates [end of entry N's listing text] from [start of entry N+1's first marking IMAGE].

The presence of a new TEXT LINE -- no matter what the line says -- is NEVER by itself a reason to cut. Listings can span many text lines. The ONLY visual signal that justifies a cut is the appearance of a new postal MARKING IMAGE inside this chunk.

== Therefore ==

- If the chunk contains zero or one marking IMAGE, return {"cuts": []}.
- If the chunk contains multiple marking IMAGES stacked vertically with no listing text between them, they share one listing -> ONE entry -> return {"cuts": []}.
- If the chunk contains a listing followed by a NEW marking IMAGE followed by ITS listing, return one cut placed between [end of previous listing] and [start of new marking image].

== Lines that are NEVER cut points ==

ALL of these are continuation listing lines that ride with the preceding marking. None of them is an entry boundary on its own:

- "Same(...) ... PRICE"
- "Same/Va.(...) ... PRICE"
- "Same C.H./Va.(...) ... PRICE"
- "(L)(...) ... PRICE" or "(L) See State"
- "(E)(...) ... PRICE"
- "ABINGDON/VA(...) ... PRICE"            (place-name listing, no leading "Same")
- "ACCOMACK/Va.(1843;29;Red) ... PRICE"   (place-name listing, no leading "Same")
- any other line starting with a place name and a parenthesised description, even if it differs from the marking's place name

A new place-name listing line WITHOUT a marking image above it is still a continuation of the preceding entry. DO NOT cut before it.

== Section headings ==

Typeset banners like "VIRGINIA" or "AMERICAN CONGRESS AND CONFEDERATION POSTMARKS" are not entry boundaries. They ride with whichever chunk they happen to land in.

== Edge handling ==

- TOP-FLUSH: do not emit a cut at y=0 even if the chunk starts with a marking.
- BOTTOM-FLUSH: do not emit a cut near the bottom edge.
- WHEN IN DOUBT: prefer {"cuts": []}. False positives waste much more downstream work than false negatives.

== Output format ==

Return STRICT JSON only -- no markdown, no prose, no code fences:

  {"cuts": [820, 1640]}

or, for an already-correct chunk:

  {"cuts": []}

Coordinates are integers in the LOCAL coordinate system of THIS chunk image, with origin top-left. All cuts must be strictly inside (0, image_height) and strictly ascending.

Output JSON only."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _img_to_b64_png(im):
    """PIL.Image -> base64-encoded PNG bytes (as ascii str)."""
    buf = BytesIO()
    im.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _strip_fences(text):
    """Drop ```json ... ``` fences if the model added them."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        last_fence = t.rfind("\n```")
        if last_fence != -1:
            t = t[:last_fence]
    return t.strip()


def _parse_strict_json(text):
    """Strip fences, try json.loads. If the model leaks preamble, fall back to
    extracting the first {...} object."""
    t = _strip_fences(text)
    if not t.startswith("{"):
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if m:
            t = m.group(0)
    return json.loads(t)


def _vision_call(llm, model, system_prompt, user_text, image_b64, max_tokens):
    """Common provider vision call; returns the raw assistant text.

    Retries once on empty content, which a reasoning model occasionally
    returns with finish_reason='stop' when reasoning tokens consume the
    budget before any visible output is emitted.
    """
    last_finish = None
    for attempt in range(2):
        content = llm.vision_text(
            model=model,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            user_text=user_text,
            image_b64=image_b64,
        )
        if content:
            return content
        last_finish = "empty"
        print(f"    WARNING: empty content (finish_reason={last_finish!r}), "
              f"retrying ({attempt + 1}/2)")
    raise ValueError(
        f"model returned empty content on both attempts; "
        f"finish_reason={last_finish!r}; "
        f"consider raising max_tokens (current call: {max_tokens})"
    )


def _idx(p):
    """Numeric suffix on page-NNNN.png (used to sort raw pdftoppm output)."""
    m = re.search(r"-(\d+)\.png$", p.name)
    return int(m.group(1)) if m else 0


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


class Paths:
    """Per-run filesystem layout, derived from --basename."""
    def __init__(self, basename):
        self.basename     = basename
        self.pdf          = WIP_DIR / "in" / f"{basename}.pdf"
        self.full_dir     = WIP_DIR / "cache" / f"{basename}_full"
        self.halves_dir   = WIP_DIR / "cache" / f"{basename}_halves"
        self.halves_cache = WIP_DIR / "cache" / f"{basename}_split_halves.json"
        self.blocks_cache = WIP_DIR / "cache" / f"{basename}_blocks.json"
        self.review_cache = WIP_DIR / "cache" / f"{basename}_review.json"
        self.run_log      = WIP_DIR / "cache" / f"{basename}_run.log"
        self.output_dir   = WIP_DIR / "out" / basename


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
# Stage A -- render
# ---------------------------------------------------------------------------

def render_pdf(pdf_path, full_dir, dpi):
    """Render PDF to full_dir/page-NNNN.png. NNNN is PDF page index, 4 digits.

    pdftoppm writes <prefix>-N.png with un-padded N for low pages, so we
    render to a sentinel prefix then rename in numeric order.
    """
    full_dir.mkdir(parents=True, exist_ok=True)
    for old in full_dir.glob("_render-*.png"):
        old.unlink()
    prefix = full_dir / "_render"
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), str(prefix)],
        check=True,
    )
    rendered = sorted(full_dir.glob("_render-*.png"), key=_idx)
    pages = []
    for i, src in enumerate(rendered, 1):
        dst = full_dir / f"page-{i:04d}.png"
        if dst.exists():
            dst.unlink()
        src.rename(dst)
        pages.append(dst)
    return pages


def stage_render(paths, force):
    """Run stage A. Returns the sorted list of full-page PNGs."""
    assert paths.pdf.is_file(), f"missing {paths.pdf}"
    assert shutil.which("pdftoppm"), "pdftoppm not on PATH (install poppler)"
    paths.full_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(paths.full_dir.glob("page-*.png"), key=_idx)
    if existing and not force:
        print(f"render: {len(existing)} pages already in {paths.full_dir}, skipping pdftoppm")
        return existing

    if force and existing:
        print(f"render: --force render set, deleting {len(existing)} existing pages")
        for old in existing:
            old.unlink()

    print(f"render: pdftoppm -r {DPI} {paths.pdf.name} -> {paths.full_dir}")
    pages = render_pdf(paths.pdf, paths.full_dir, DPI)
    print(f"render: wrote {len(pages)} pages")
    assert pages, "no rendered pages -- check PDF and pdftoppm output"
    return pages


# ---------------------------------------------------------------------------
# Stage B -- halves (rule detection + page number + L/R crop)
# ---------------------------------------------------------------------------

def detect_rule_x(im, min_dark_fraction=RULE_MIN_DARK_FRACTION):
    """Locate the printed vertical rule between the two text columns.

    Returns x in full-page coords, or None if no column qualifies.
    """
    arr = np.asarray(im.convert("L"))
    h, w = arr.shape

    band_top    = int(h * RULE_BAND_TOP_FRAC)
    band_bottom = int(h * RULE_BAND_BOTTOM_FRAC)
    band = arr[band_top:band_bottom, :]
    band_h = band.shape[0]

    cx = w // 2
    half_search = int(w * RULE_SEARCH_FRACTION / 2)
    x_lo = max(0, cx - half_search)
    x_hi = min(w, cx + half_search)

    col_dark = (band[:, x_lo:x_hi] < RULE_DARK_THRESHOLD).sum(axis=0)
    min_dark = int(band_h * min_dark_fraction)
    qualifying = np.where(col_dark >= min_dark)[0]

    if qualifying.size == 0:
        return None

    rule_x = x_lo + int(np.median(qualifying))

    if abs(rule_x - cx) > w * RULE_CENTER_TOLERANCE:
        return None

    return rule_x


def build_header_footer_strip(im):
    w, h = im.size
    head = im.crop((0, 0, w, min(h, HEADER_STRIP_HEIGHT)))
    foot = im.crop((0, max(0, h - FOOTER_STRIP_HEIGHT), w, h))
    out_h = head.size[1] + HEADER_FOOTER_SEP_PX + foot.size[1]
    out = Image.new("RGB", (w, out_h), (0, 0, 0))
    out.paste(head, (0, 0))
    out.paste(foot, (0, head.size[1] + HEADER_FOOTER_SEP_PX))
    return out


def detect_page_number(strip_im, llm, model):
    """Vision call: read the printed catalog page number from the strip."""
    img_b64 = _img_to_b64_png(strip_im)
    raw = _vision_call(
        llm,
        model,
        PAGE_NUMBER_SYSTEM_PROMPT,
        "Read the printed catalog page number. Return JSON only.",
        img_b64,
        HALVES_MAX_TOKENS,
    )
    data = _parse_strict_json(raw)
    pn = data["page_number"]
    assert isinstance(pn, int) and pn >= 1, f"bad page_number {pn!r}"
    return pn


def confirm_single_column(im, llm, model):
    """Vision call: confirm whether the page has two columns (used as a
    fallback when the deterministic rule detector returns None)."""
    img_b64 = _img_to_b64_png(im)
    raw = _vision_call(
        llm,
        model,
        SINGLE_COL_SYSTEM_PROMPT,
        "Is this page laid out as two columns with a printed vertical rule? Return JSON only.",
        img_b64,
        HALVES_MAX_TOKENS,
    )
    data = _parse_strict_json(raw)
    htc = data["has_two_columns"]
    assert isinstance(htc, bool), f"bad has_two_columns {htc!r}"
    return htc


def stage_halves(paths, provider, model, llm, full_pages, force, page_filter,
                 verbose=False):
    """Run stage B. page_filter, if not None, is a (kind, set_of_ints) tuple
    where kind is 'pdf' (PDF page indices) or 'catalog' (catalog page nums).
    Filtering applies to which halves get WRITTEN; page-number detection
    runs for every page in full_pages so the catalog<->pdf mapping stays
    complete (cached anyway after the first run)."""
    paths.halves_dir.mkdir(parents=True, exist_ok=True)
    paths.halves_cache.parent.mkdir(parents=True, exist_ok=True)

    halves_cache = load_cache(
        paths.halves_cache,
        provider,
        model,
        HALVES_PROMPT_VER,
    )
    responses = halves_cache["responses"]

    # If --force halves was set, drop in-scope cache entries up front so the
    # loop re-queries them. Without --pages, every PDF page is in scope.
    if force:
        if page_filter is None:
            print(f"halves: --force halves set, clearing all {len(responses)} cache entries")
            responses.clear()
        else:
            kind, ids = page_filter
            to_drop = []
            for key, rec in responses.items():
                m = re.match(r"pdf-page-(\d+)$", key)
                if not m:
                    continue
                pdf_idx = int(m.group(1))
                if kind == "pdf" and pdf_idx in ids:
                    to_drop.append(key)
                elif kind == "catalog" and rec.get("page_number") in ids:
                    to_drop.append(key)
            for key in to_drop:
                del responses[key]
            print(f"halves: --force halves set, cleared {len(to_drop)} cache entries in scope")
        save_cache(paths.halves_cache, halves_cache)

    calls = 0
    rule_failures = []
    for pdf_idx, full_png in enumerate(full_pages, 1):
        key = f"pdf-page-{pdf_idx:04d}"
        with Image.open(full_png) as im:
            iw, ih = im.size
            if key in responses:
                rec = responses[key]
                if (
                    rec.get("has_two_columns")
                    and rec.get("rule_source") == "vision_single_col_failed"
                    and rec.get("rule_x") == rec.get("image_width", iw) // 2
                ):
                    weak_rule_x = detect_rule_x(
                        im,
                        min_dark_fraction=RULE_WEAK_MIN_DARK_FRACTION,
                    )
                    if weak_rule_x is not None:
                        rec = dict(rec)
                        rec["rule_x"] = weak_rule_x
                        rec["rule_source"] = "vision_confirmed_weak_rule"
                        responses[key] = rec
                        save_cache(paths.halves_cache, halves_cache)
            else:
                rule_x = detect_rule_x(im)

                hf_strip = build_header_footer_strip(im)
                if verbose:
                    log_only(f"  {key}: calling {model} for page-number...")
                t0 = time.time()
                pn = detect_page_number(hf_strip, llm, model)
                if verbose:
                    print(f"  {key}:   ... {time.time() - t0:.1f}s -> pn={pn}",
                          flush=True)
                calls += 1

                if rule_x is not None:
                    htc = True
                    rule_source = "deterministic"
                else:
                    if verbose:
                        log_only(f"  {key}: no rule found, calling {model} "
                                 f"for single-col confirm...")
                    t0 = time.time()
                    htc = confirm_single_column(im, llm, model)
                    if verbose:
                        print(f"  {key}:   ... {time.time() - t0:.1f}s -> "
                              f"has_two_columns={htc}", flush=True)
                    calls += 1
                    if htc:
                        weak_rule_x = detect_rule_x(
                            im,
                            min_dark_fraction=RULE_WEAK_MIN_DARK_FRACTION,
                        )
                        if weak_rule_x is not None:
                            rule_x = weak_rule_x
                            rule_source = "vision_confirmed_weak_rule"
                        else:
                            rule_x = iw // 2
                            rule_source = "vision_single_col_failed"
                            rule_failures.append((key, pn))
                    else:
                        rule_x = -1
                        rule_source = "vision_single_col"

                rec = {
                    "page_number":     pn,
                    "has_two_columns": htc,
                    "rule_x":          rule_x,
                    "rule_source":     rule_source,
                    "image_width":     iw,
                    "image_height":    ih,
                }
                responses[key] = rec
                save_cache(paths.halves_cache, halves_cache)

        pn = rec["page_number"]
        if rec["has_two_columns"]:
            print(f"  {key} -> catalog {pn:>4d}  rule_x={rec['rule_x']:>5d}  "
                  f"source={rec['rule_source']:<28s}  size {iw}x{ih}")
        else:
            print(f"  {key} -> catalog {pn:>4d}  SINGLE-COLUMN  ({rec['rule_source']})  size {iw}x{ih}")

    print(f"halves: vision calls made = {calls}")
    if rule_failures:
        print()
        print("=== WARNING: deterministic rule detector failed on these two-column pages ===")
        for key, pn in rule_failures:
            print(f"  {key} catalog {pn}")
        print(f"no weak rule candidate was found; rule_x has been set to")
        print(f"image_width//2 as a fallback. Inspect the")
        print(f"halves output and hand-edit {paths.halves_cache} if the split is off.")

    # Duplicate-page-number guard runs BEFORE any writes.
    by_pn = {}
    for pdf_idx, _ in enumerate(full_pages, 1):
        key = f"pdf-page-{pdf_idx:04d}"
        pn = responses[key]["page_number"]
        by_pn.setdefault(pn, []).append(key)
    dups = {pn: keys for pn, keys in by_pn.items() if len(keys) > 1}
    if dups:
        raise RuntimeError(
            f"duplicate catalog page_number(s) detected in halves cache: {dups}.\n"
            f"Edit {paths.halves_cache} to fix the misread page_number(s) and re-run."
        )

    # Decide which pages to (re)write.
    selected = set()  # set of pdf_idx
    for pdf_idx, _ in enumerate(full_pages, 1):
        key = f"pdf-page-{pdf_idx:04d}"
        rec = responses[key]
        if page_filter is None:
            selected.add(pdf_idx)
        else:
            kind, ids = page_filter
            if kind == "pdf" and pdf_idx in ids:
                selected.add(pdf_idx)
            elif kind == "catalog" and rec["page_number"] in ids:
                selected.add(pdf_idx)

    # Wipe halves files for the in-scope pages only.
    for pdf_idx, _ in enumerate(full_pages, 1):
        if pdf_idx not in selected:
            continue
        pn = responses[f"pdf-page-{pdf_idx:04d}"]["page_number"]
        for old in paths.halves_dir.glob(f"page-{pn:04d}*.png"):
            old.unlink()

    halves_written = 0
    for pdf_idx, full_png in enumerate(full_pages, 1):
        if pdf_idx not in selected:
            continue
        rec = responses[f"pdf-page-{pdf_idx:04d}"]
        pn = rec["page_number"]
        with Image.open(full_png) as im:
            w, h = im.size
            if rec["has_two_columns"]:
                rx = rec["rule_x"]
                assert 0 < rx < w, f"rule_x {rx} out of (0, {w}) for pdf-page-{pdf_idx:04d}"
                im.crop((0,  0, rx, h)).save(paths.halves_dir / f"page-{pn:04d}-L.png")
                im.crop((rx, 0, w,  h)).save(paths.halves_dir / f"page-{pn:04d}-R.png")
                halves_written += 2
            else:
                im.crop((0, 0, w, h)).save(paths.halves_dir / f"page-{pn:04d}.png")
                halves_written += 1

    print(f"halves: wrote {halves_written} half images to {paths.halves_dir}")
    if page_filter is None:
        print("halves: --- INSPECT THESE BEFORE PROCEEDING TO STAGE chunks ---")
        print("halves: open a sampling of halves and confirm no listing text or")
        print("halves: marking is clipped at the L/R seam.")


# ---------------------------------------------------------------------------
# Stage C -- chunks (block detection + classify + cut)
# ---------------------------------------------------------------------------

def find_blocks(img_gray):
    """Scan rows, count dark pixels in the center band, group into blocks.

    A block starts when a non-blank row appears after BLANK_RUN+ blank rows
    and ends when BLANK_RUN+ blank rows follow.

    Returns a list of (y_top, y_bottom) inclusive tuples.
    """
    arr = np.array(img_gray)
    H, W = arr.shape

    margin = (1.0 - CENTER_FRACTION) / 2.0
    left   = int(W * margin)
    right  = int(W * (1.0 - margin))
    center = arr[:, left:right]

    dark_per_row = (center < DARK_BRIGHTNESS_MAX).sum(axis=1)
    is_dark = dark_per_row >= ROW_DARK_MIN_PIXELS

    blocks = []
    in_block = False
    block_start = None
    last_dark = None
    blank_run = BLANK_RUN  # primed so the first dark row opens a block

    for y in range(H):
        if is_dark[y]:
            if not in_block:
                if blank_run >= BLANK_RUN:
                    in_block = True
                    block_start = y
                    last_dark = y
            else:
                last_dark = y
            blank_run = 0
        else:
            blank_run += 1
            if in_block and blank_run >= BLANK_RUN:
                blocks.append((block_start, last_dark))
                in_block = False
                block_start = None

    if in_block:
        blocks.append((block_start, last_dark))

    return blocks


def classify_block(block_im, blocks_cache, llm, model, verbose=False, label=""):
    """Classify a single block crop as 'illustration' or 'text'.

    Cache key: SHA-256 of the block PNG bytes. Coordinate-keyed caching
    would needlessly miss whenever a block-detector constant gets tweaked.

    If verbose, prints per-call progress (cache hit/miss, elapsed seconds,
    raw response length). label is a short prefix like '419-L block 3/12'.
    """
    buf = BytesIO()
    block_im.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    key = hashlib.sha256(png_bytes).hexdigest()

    responses = blocks_cache["responses"]
    if key in responses:
        if verbose:
            print(f"    {label} CACHE HIT -> {responses[key]['kind']}")
        return responses[key]["kind"], False  # cache hit

    img_b64 = base64.standard_b64encode(png_bytes).decode()
    if verbose:
        log_only(f"    {label} calling {model} "
                 f"({len(png_bytes):,} bytes png)...")
    t0 = time.time()
    raw = _vision_call(
        llm,
        model,
        BLOCK_CLASSIFY_SYSTEM_PROMPT,
        "Classify this strip. Return JSON only.",
        img_b64,
        BLOCKS_MAX_TOKENS,
    )
    if verbose:
        print(f"    {label}   ... {time.time() - t0:.1f}s, "
              f"raw={len(raw)} chars", flush=True)
    try:
        data = _parse_strict_json(raw)
        kind = data["kind"]
        assert kind in ("illustration", "text"), f"bad kind {kind!r}"
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        # Fallback: word-search on the raw text. The original notebook took
        # this path always; we use it only when JSON parse fails so a single
        # malformed response does not abort the whole run.
        low = raw.lower()
        kind = "illustration" if "illustration" in low else "text"
        print(f"    WARNING: classify_block fallback used "
              f"({type(e).__name__}: {e}); raw={raw[:80]!r} -> {kind}")

    responses[key] = {"kind": kind}
    return kind, True  # cache miss -> caller should save


def drop_orphan_illustration_cuts(cut_ys, kinds, H, label_prefix=""):
    """Apply the rule: every chunk must contain at least one text block.

    A chunk that contains only illustration block(s) and no text is, by
    definition, a fragment of a larger entry. Either:
      - the deterministic detector split a single tall marking (e.g. an arc
        postmark like 'DOVER' on top and 'MILLS' below) into two blocks
        because the internal blank gap exceeded BLANK_RUN; both blocks got
        classified illustration; both got their own cut; the top half ended
        up alone as its own chunk.
      - or two markings sharing one listing got cut between them.

    Both cases want the same fix: merge the orphan illustration-only chunk
    FORWARD into the next chunk that contains text. We do that by dropping
    the cut that would have ended the orphan chunk.

    Edge case: if the trailing chunk [last_kept_cut, H] is illustration-only,
    drop the last kept cut to merge it BACKWARD into the previous chunk.
    """
    text_ys = [y0 for (y0, _y1, k) in kinds if k == "text"]

    def slice_has_text(start, end):
        return any(start <= ty < end for ty in text_ys)

    new_cuts = []
    slice_start = 0
    for c in cut_ys:
        if slice_has_text(slice_start, c):
            new_cuts.append(c)
            slice_start = c
        else:
            print(f"    {label_prefix}drop orphan-illustration cut y={c}: "
                  f"chunk [{slice_start}, {c}] has no text below the marking")
            # do not advance slice_start -- merge forward into next slice

    # Trailing-orphan check.
    if new_cuts and not slice_has_text(new_cuts[-1], H):
        dropped = new_cuts.pop()
        print(f"    {label_prefix}drop trailing orphan-illustration cut "
              f"y={dropped}: final chunk [{dropped}, {H}] has no text "
              f"below the marking; merging backward")

    return new_cuts


def find_blank_runs(slice_im):
    """Return [(start, end_exclusive), ...] for runs of >= BLANK_RUN
    consecutive blank rows in the slice, using the same row-darkness rule
    as find_blocks (DARK_BRIGHTNESS_MAX, ROW_DARK_MIN_PIXELS, CENTER_FRACTION).
    """
    arr = np.array(slice_im.convert("L"))
    H, W = arr.shape

    margin = (1.0 - CENTER_FRACTION) / 2.0
    left   = int(W * margin)
    right  = int(W * (1.0 - margin))
    center = arr[:, left:right]

    dark_per_row = (center < DARK_BRIGHTNESS_MAX).sum(axis=1)
    is_blank = dark_per_row < ROW_DARK_MIN_PIXELS

    runs = []
    run_start = None
    for y in range(H):
        if is_blank[y]:
            if run_start is None:
                run_start = y
        else:
            if run_start is not None:
                if y - run_start >= BLANK_RUN:
                    runs.append((run_start, y))
                run_start = None
    if run_start is not None and H - run_start >= BLANK_RUN:
        runs.append((run_start, H))
    return runs


def snap_cut_to_blank_run(cut_y, blank_runs, tolerance=SNAP_TOLERANCE_PX):
    """Snap a model-returned cut to the middle of the nearest blank-row run
    within +/- tolerance. Returns the snapped y, or None if no run qualifies.
    """
    best_dist = None
    best_mid = None
    for (a, b) in blank_runs:
        mid = (a + b) // 2
        d = abs(mid - cut_y)
        if d <= tolerance and (best_dist is None or d < best_dist):
            best_dist = d
            best_mid = mid
    return best_mid


def review_slice(slice_im, review_cache, llm, model, verbose=False, label=""):
    """Per-slice entry-aware review.

    Returns (cuts, was_call). cuts is a list of LOCAL y-offsets where the
    chunk should be split further (empty list = chunk is already a single
    entry and should not be re-cut). Cached by SHA-256 of slice PNG bytes
    so re-runs do not re-query.
    """
    buf = BytesIO()
    slice_im.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    key = hashlib.sha256(png_bytes).hexdigest()

    responses = review_cache["responses"]
    if key in responses:
        cuts = responses[key]["cuts"]
        if verbose:
            tag = "no-split" if not cuts else f"SPLIT at {cuts}"
            print(f"    {label} REVIEW CACHE HIT -> {tag}")
        return cuts, False

    img_b64 = base64.standard_b64encode(png_bytes).decode()
    h = slice_im.size[1]
    if verbose:
        log_only(f"    {label} review: calling {model} "
                 f"({len(png_bytes):,} bytes png, h={h})...")
    t0 = time.time()
    raw = _vision_call(
        llm,
        model,
        REVIEW_SLICE_SYSTEM_PROMPT,
        f"Image height: {h} px. Decide if this chunk is one entry or "
        f"multiple. Return JSON only.",
        img_b64,
        REVIEW_MAX_TOKENS,
    )
    if verbose:
        print(f"    {label}   ... {time.time() - t0:.1f}s, raw={len(raw)} chars",
              flush=True)

    cuts = []
    try:
        data = _parse_strict_json(raw)
        cuts = data["cuts"]
        assert isinstance(cuts, list), f"cuts not a list: {cuts!r}"
        # Validate and sanitize: ints, in (0, h), strictly ascending.
        clean = []
        for c in cuts:
            if not isinstance(c, int):
                raise ValueError(f"cut not int: {c!r}")
            if not (0 < c < h):
                raise ValueError(f"cut {c} out of (0, {h})")
            clean.append(c)
        clean = sorted(set(clean))
        cuts = clean
    except (json.JSONDecodeError, KeyError, AssertionError, ValueError) as e:
        # Failure mode: assume the chunk is fine, do not split. Log loudly so
        # a bad batch is visible. Cache the empty result so we do not retry
        # forever on a stubbornly malformed response.
        print(f"    {label} WARNING: review parse failed "
              f"({type(e).__name__}: {e}); raw={raw[:120]!r}; "
              f"treating as no-split")
        cuts = []

    responses[key] = {"cuts": cuts}
    return cuts, True


def stage_chunks(paths, provider, model, llm, force, page_filter, verbose=False,
                 skip_review=False):
    """Run stage C. page_filter, if not None, is a (kind, set_of_ints).
    'kind' for chunks is always interpreted as catalog page numbers
    because halves are named by catalog page; a 'pdf' filter raises."""
    paths.blocks_cache.parent.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    halves = sorted(paths.halves_dir.glob("page-*.png"))
    if not halves:
        raise RuntimeError(
            f"no halves under {paths.halves_dir} -- run --stages halves first"
        )

    # Group halves by catalog page number. Filename forms:
    #   page-NNNN-L.png / page-NNNN-R.png   (two-column page)
    #   page-NNNN.png                        (single-column page)
    pat = re.compile(r"^page-(\d{4})(?:-([LR]))?$")
    pages = {}  # pn -> { 'L': Path, 'R': Path }  or { '_': Path }
    for p in halves:
        m = pat.match(p.stem)
        if not m:
            print(f"  skip (unrecognised name): {p.name}")
            continue
        pn = int(m.group(1))
        side = m.group(2) or "_"
        pages.setdefault(pn, {})[side] = p

    # Apply page filter.
    if page_filter is not None:
        kind, ids = page_filter
        if kind == "pdf":
            raise RuntimeError(
                "stage 'chunks' does not accept a :pdf page filter "
                "(halves are named by catalog page number)"
            )
        pages = {pn: v for pn, v in pages.items() if pn in ids}

    blocks_cache = load_cache(
        paths.blocks_cache,
        provider,
        model,
        BLOCKS_PROMPT_VER,
    )
    review_cache = load_cache(
        paths.review_cache,
        provider,
        model,
        REVIEW_PROMPT_VER,
    )

    # --force chunks: wipes BOTH the per-block classifier cache AND the
    # per-slice review cache. Both are content-hash keyed (no page field), so
    # there is no honest way to scope the wipe to a page subset; the user
    # signalled they want everything reclassified.
    if force:
        nb = len(blocks_cache["responses"])
        nr = len(review_cache["responses"])
        print(f"chunks: --force chunks set, clearing {nb} block cache entries "
              f"and {nr} review cache entries")
        blocks_cache["responses"].clear()
        review_cache["responses"].clear()
        save_cache(paths.blocks_cache, blocks_cache)
        save_cache(paths.review_cache, review_cache)

    # Wipe in-scope output files first so stale chunks do not linger.
    for pn in pages:
        for old in paths.output_dir.glob(f"page-{pn:04d}-*.png"):
            old.unlink()

    total_pages = 0
    total_blocks = 0
    total_illus = 0
    total_slices = 0
    total_review_splits = 0
    calls = 0
    review_calls = 0

    for pn in sorted(pages):
        halves_for_page = pages[pn]
        print(f"--- page {pn:04d} ---")
        counter = 1

        # Process L then R (or '_' for single-column).
        if "_" in halves_for_page:
            order = ["_"]
        else:
            order = ["L", "R"]

        for side in order:
            if side not in halves_for_page:
                print(f"  missing {side} half")
                continue
            img_path = halves_for_page[side]
            with Image.open(img_path) as im:
                gray = im.convert("L")
                W, H = im.size
                blocks = find_blocks(gray)
                if verbose:
                    print(f"  {side}: {img_path.name} {W}x{H}, "
                          f"{len(blocks)} blocks detected", flush=True)

                cut_ys = []
                kinds = []
                for i, (y0, y1) in enumerate(blocks, 1):
                    block_im = im.crop((0, y0, W, y1 + 1))
                    label = (f"[{pn:04d}-{side} block {i}/{len(blocks)} "
                             f"y={y0}-{y1} h={y1 - y0 + 1}]")
                    kind, was_call = classify_block(
                        block_im, blocks_cache, llm, model,
                        verbose=verbose, label=label,
                    )
                    if was_call:
                        save_cache(paths.blocks_cache, blocks_cache)
                        calls += 1
                    kinds.append((y0, y1, kind))
                    if kind == "illustration":
                        cut_ys.append(y0)

                # Drop cuts that would create chunks containing only an
                # illustration (no text below the marking). This catches
                # tall-marking-split-by-blank-gap cases (e.g. arc postmarks
                # where the top arc text and bottom arc text are detected as
                # two separate illustration blocks) and stacked-markings-
                # sharing-one-listing cases.
                cut_ys = drop_orphan_illustration_cuts(
                    cut_ys, kinds, H, label_prefix=f"[{pn:04d}-{side}] ",
                )

                # Filter cuts that would produce slivers thinner than
                # MIN_SLICE_HEIGHT_PX.
                kept_cuts = []
                last = 0
                for c in cut_ys:
                    if c - last < MIN_SLICE_HEIGHT_PX:
                        continue
                    if H - c < MIN_SLICE_HEIGHT_PX:
                        continue
                    kept_cuts.append(c)
                    last = c

                ys = [0] + kept_cuts + [H]
                slices = []
                for y0, y1 in zip(ys[:-1], ys[1:]):
                    if y1 - y0 < MIN_SLICE_HEIGHT_PX:
                        continue
                    slices.append(im.crop((0, y0, W, y1)))

                n_blocks = len(blocks)
                n_illus = sum(1 for _, _, k in kinds if k == "illustration")
                n_slices = len(slices)
                total_blocks += n_blocks
                total_illus += n_illus
                label = side if side != "_" else "single"
                print(f"  {label}: blocks={n_blocks} illustrations={n_illus} "
                      f"slices(pre-review)={n_slices}")
                for (y0, y1, kind) in kinds:
                    print(f"    y=[{y0:5d}..{y1:5d}]  {kind}")

                # Per-slice entry-aware review pass. Each slice goes back to
                # the model with the entry-aware prompt; if the model returns
                # extra cuts in local coords, we re-slice and emit the pieces.
                # MIN_SLICE_HEIGHT_PX still applies to the pieces.
                final_pieces = []
                splits_this_half = 0
                for si, sl in enumerate(slices, 1):
                    if skip_review:
                        final_pieces.append(sl)
                        continue
                    sw, sh = sl.size
                    rlabel = (f"[{pn:04d}-{side} slice {si}/{n_slices} h={sh}]")
                    extra_cuts, was_call = review_slice(
                        sl, review_cache, llm, model,
                        verbose=verbose, label=rlabel,
                    )
                    if was_call:
                        save_cache(paths.review_cache, review_cache)
                        review_calls += 1
                    if not extra_cuts:
                        final_pieces.append(sl)
                        continue

                    # Snap each model-returned cut to the middle of the nearest
                    # BLANK_RUN+ blank-row run inside the slice. Cuts that
                    # cannot be snapped (no blank run within tolerance) are
                    # rejected -- this filters out false positives where the
                    # model thought there was an entry boundary inside a
                    # solid-text listing.
                    blank_runs = find_blank_runs(sl)
                    snapped = []
                    for c in extra_cuts:
                        s = snap_cut_to_blank_run(c, blank_runs)
                        if s is None:
                            print(f"    {rlabel} REJECT review cut y={c}: "
                                  f"no blank-run gap within "
                                  f"+/-{SNAP_TOLERANCE_PX}px")
                            continue
                        if s != c:
                            print(f"    {rlabel} snap cut y={c} -> y={s} "
                                  f"(nearest blank-run middle)")
                        snapped.append(s)
                    snapped = sorted(set(snapped))

                    # Filter: keep only cuts that produce >= MIN_SLICE_HEIGHT_PX
                    # on both sides of the cut.
                    kept = []
                    last = 0
                    for c in snapped:
                        if c - last < MIN_SLICE_HEIGHT_PX:
                            print(f"    {rlabel} drop review cut y={c}: "
                                  f"gap above={c - last} < {MIN_SLICE_HEIGHT_PX}")
                            continue
                        if sh - c < MIN_SLICE_HEIGHT_PX:
                            print(f"    {rlabel} drop review cut y={c}: "
                                  f"gap below={sh - c} < {MIN_SLICE_HEIGHT_PX}")
                            continue
                        kept.append(c)
                        last = c
                    if not kept:
                        final_pieces.append(sl)
                        continue
                    splits_this_half += len(kept)
                    print(f"    {rlabel} REVIEW SPLIT into "
                          f"{len(kept) + 1} pieces at {kept}")
                    sub_ys = [0] + kept + [sh]
                    for a, b in zip(sub_ys[:-1], sub_ys[1:]):
                        final_pieces.append(sl.crop((0, a, sw, b)))

                total_review_splits += splits_this_half
                total_slices += len(final_pieces)
                if splits_this_half:
                    print(f"  {label}: review added {splits_this_half} cut(s); "
                          f"final pieces = {len(final_pieces)}")

                for piece in final_pieces:
                    out_path = paths.output_dir / f"page-{pn:04d}-{counter:04d}.png"
                    piece.save(out_path)
                    counter += 1
        total_pages += 1

    print()
    print(f"chunks: pages processed     = {total_pages}")
    print(f"chunks: blocks detected     = {total_blocks}")
    print(f"chunks: illustrations       = {total_illus}")
    print(f"chunks: pieces written      = {total_slices}")
    print(f"chunks: review splits added = {total_review_splits}")
    print(f"chunks: classify calls made = {calls}")
    print(f"chunks: review calls made   = {review_calls}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

VALID_STAGES = ("render", "halves", "chunks")


def parse_stages_arg(s):
    if s == "all":
        return list(VALID_STAGES)
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in VALID_STAGES:
            raise argparse.ArgumentTypeError(
                f"unknown stage {part!r}; valid: {','.join(VALID_STAGES)},all"
            )
        out.append(part)
    if not out:
        raise argparse.ArgumentTypeError("--stages must list at least one stage")
    return out


def parse_force_arg(s):
    if not s:
        return set()
    out = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in VALID_STAGES:
            raise argparse.ArgumentTypeError(
                f"unknown --force stage {part!r}; valid: {','.join(VALID_STAGES)}"
            )
        out.add(part)
    return out


def parse_pages_arg(s):
    """Parse the --pages argument.

    Returns either None (no filter) or a (kind, set_of_ints) tuple where
    kind is 'pdf' or 'catalog'. Forms accepted:

        419            single catalog page
        419-435        inclusive range (catalog)
        419,422,430    explicit list (catalog)
        0001-0017:pdf  inclusive range, PDF page indices

    A trailing ':pdf' on any form makes the whole expression refer to PDF
    page indices instead of catalog page numbers.
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None

    kind = "catalog"
    if s.endswith(":pdf"):
        kind = "pdf"
        s = s[:-len(":pdf")]

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
            f"--pages part {part!r} not understood; expected NNN, NNN-NNN, or list"
        )

    if not ids:
        raise argparse.ArgumentTypeError("--pages produced an empty set")
    return (kind, ids)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render an ASCC catalog PDF, split each page into halves, "
                    "and chunk each half into per-entry PNGs. Stages can be run "
                    "independently so intermediate output can be reviewed.",
    )
    parser.add_argument(
        "basename",
        help=("base name shared by the input PDF, the cache directories, and "
              "the output directory. The PDF is read from "
              "tools/wip/in/<basename>.pdf; halves land in "
              "tools/wip/cache/<basename>_halves/; chunks land in "
              "tools/wip/out/<basename>/."),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=("model id used for every vision call (page-number read, "
              "single-column confirm, per-block classify). Default: "
              "PIPELINE_LLM_MODEL if set, otherwise provider-specific "
              f"default ({DEFAULT_MODEL} for OpenRouter). Cache files are "
              "tagged with the provider and model id and invalidate "
              "automatically on change."),
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=("LLM provider for vision calls. Default: "
              "PIPELINE_LLM_PROVIDER if set, otherwise openrouter."),
    )
    parser.add_argument(
        "--stages",
        type=parse_stages_arg,
        default=parse_stages_arg("all"),
        help=("comma-separated stages to run, in order. Choices: "
              "render,halves,chunks,all. Default: all."),
    )
    parser.add_argument(
        "--pages",
        type=parse_pages_arg,
        default=None,
        help=("restrict halves and chunks stages to a page range. Forms: "
              "'419', '419-435', '419,422,430', or any of the above with a "
              "':pdf' suffix to treat numbers as PDF page indices. "
              "Default: no filter."),
    )
    parser.add_argument(
        "--force",
        type=parse_force_arg,
        default=set(),
        help=("comma-separated stages whose caches should be invalidated. "
              "Choices: render,halves,chunks. Scoped to --pages where "
              "applicable. Default: empty."),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=("print per-block / per-page progress: which call is in flight, "
              "elapsed seconds, cache hit vs model call, response size. "
              "Useful when chunk processing seems stuck."),
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help=("skip the per-slice entry-aware review pass in the chunks "
              "stage. The review pass sends each first-pass slice back to the "
              "model with an entry-aware prompt and re-cuts the slice if the "
              "model finds multiple distinct entries inside it. Skipping is "
              "faster and cheaper but may leave merged-entry chunks behind."),
    )
    args = parser.parse_args(argv)

    paths = Paths(args.basename)
    provider = resolve_provider(args.provider)
    model = resolve_model(provider, args.model)

    # In verbose mode, tee everything to a per-basename log file in the cache
    # dir so re-running just to re-read the log is unnecessary.
    global _LOG_FH
    log_fh = None
    saved_stdout = sys.stdout
    if args.verbose:
        paths.run_log.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(paths.run_log, "a")
        from datetime import datetime
        ts = datetime.now().isoformat(timespec="seconds")
        argv_str = " ".join(sys.argv[1:])
        log_fh.write(f"\n========== {ts}  argv: {argv_str} ==========\n")
        log_fh.flush()
        sys.stdout = _Tee(saved_stdout, log_fh)
        _LOG_FH = log_fh

    try:
        if args.verbose:
            print(f"verbose log: tee-ing to {paths.run_log}")
        print(f"basename: {paths.basename}")
        print(f"provider: {provider}")
        log_only(f"provider: {provider}")
        print(f"model:    {model}")
        log_only(f"model:    {model}")
        print(f"stages:   {','.join(args.stages)}")
        if args.pages is not None:
            kind, ids = args.pages
            sample = ",".join(str(x) for x in sorted(ids)[:6])
            more = "..." if len(ids) > 6 else ""
            print(f"pages:    kind={kind} count={len(ids)} ({sample}{more})")
        else:
            print("pages:    (no filter)")
        if args.force:
            print(f"force:    {','.join(sorted(args.force))}")
        print()

        full_pages = None
        llm = None

        for stage in args.stages:
            print(f"=== stage: {stage} ===")
            if stage == "render":
                full_pages = stage_render(paths, force=("render" in args.force))
            elif stage == "halves":
                if llm is None:
                    llm = make_pipeline_llm(provider)
                if full_pages is None:
                    full_pages = sorted(paths.full_dir.glob("page-*.png"), key=_idx)
                    if not full_pages:
                        raise RuntimeError(
                            f"--stages halves requires {paths.full_dir}/page-*.png; "
                            f"run --stages render first"
                        )
                stage_halves(
                    paths,
                    provider,
                    model,
                    llm,
                    full_pages,
                    force=("halves" in args.force),
                    page_filter=args.pages,
                    verbose=args.verbose,
                )
            elif stage == "chunks":
                if llm is None:
                    llm = make_pipeline_llm(provider)
                stage_chunks(
                    paths,
                    provider,
                    model,
                    llm,
                    force=("chunks" in args.force),
                    page_filter=args.pages,
                    verbose=args.verbose,
                    skip_review=args.skip_review,
                )
            print()
    finally:
        if log_fh is not None:
            sys.stdout = saved_stdout
            log_fh.close()
            _LOG_FH = None


if __name__ == "__main__":
    main()
