"""ascc_image_extract.py -- two-stage marking-illustration extractor.

Reads chunk PNGs at tools/wip/cache/<basename>/page-NNNN-MMMM.png plus the
reviewed CSV at tools/wip/cache/<basename>.csv. For each chunk where Images
Above >= 1:

  Stage 1 (deterministic, no API calls):
      Find row-blocks via find_blocks (BLANK_RUN=5 from the processor).
      Cut at the midpoint of the LARGEST inter-block gap -- the
      marking-to-text transition is always the widest vertical blank
      in a chunk built by the upstream chunker. When find_blocks
      returns a single merged block (marking and text packed too
      tightly to separate at BLANK_RUN=5), re-scan inside it for any
      2+ row blank gap and cut there.

      An earlier draft classified each block with a vision model. That
      approach kept misclassifying graphically-text-like markings
      (cursive postmarks, the lower "VA" line of a stacked marking,
      etc.). The gap rule sidesteps the entire class of failure and
      runs offline.

  Stage 2 (deterministic, noise-aware, no API calls):
      Detect substantial dark regions in the sub-chunk on both axes:
        - Within the Y span returned by find_blocks, scan dark-per-col;
          form raw column-runs (>=2 dark pixels per col);
        - Merge column-runs separated by gaps <= MERGE_GAP_MAX px (so
          letter-internal gaps inside a single marking do not split it);
        - Drop runs narrower than MIN_MARKING_WIDTH px (kills edge-bar
          and column-rule artifacts).
      Then match the CSV's expected count N:
        - N == 1: union all surviving runs into one bbox (works even
          when a marking has wide internal blanks);
        - N == len(surviving runs): one bbox per run (side-by-side row);
        - N == len(row_blocks): one bbox per row-block (stacked);
        - Else: log marking_count_mismatch and emit nothing.
      Y bounds per bbox are tightened to the actual dark rows inside the
      bbox X range.

Outputs:
  tools/wip/cache/<basename>_subchunks/<region>-<page>-<chunk>.png
  tools/wip/cache/<basename>_images/<region>-<page>-<chunk>-<counter>.png
  tools/wip/cache/<basename>_subchunks_report.csv

Pipeline position: downstream of tools/ascc_page_extract.py (which
produces the authoritative CSV with Images Above counts).

Usage:

    uv run python tools/ascc_image_extract.py VA_ASCC_CTLG
    uv run python tools/ascc_image_extract.py VA_ASCC_CTLG --pages 419-425
    uv run python tools/ascc_image_extract.py VA_ASCC_CTLG -v

No caches: both stages are deterministic and re-run quickly.

Report:
    Status codes: ok, not_in_csv, no_blocks, marking_count_mismatch,
    csv_parse_error.
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from PIL import Image

# Script-root paths let this tool run from any cwd while preserving the
# historical tools/wip layout.
TOOLS_DIR = Path(__file__).resolve().parent
WIP_DIR = TOOLS_DIR / "wip"

# Repo-root .env. Importing
# ascc_page_processor also calls load_dotenv at module init; calling it
# here too is harmless and keeps this file self-evident.
load_dotenv(TOOLS_DIR.parent / ".env")

# Make sibling tools importable when this script is run as a module.
sys.path.insert(0, str(TOOLS_DIR))
from ascc_page_processor import (   # noqa: E402
    find_blocks,
)
from ascc_page_extract import (     # noqa: E402
    discover_chunks,
    parse_pages_arg,
)
from munger.images import (         # noqa: E402
    catalog_image_slug,
    image_filename,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPORT_COLUMNS = [
    "Page", "Chunk", "Images Above", "Markings Found", "Status", "Notes",
]

# Stage 2 noise + gap parameters (300 DPI scans). Both are deterministic.
# A column "has content" if it has >= COL_DARK_MIN_PIXELS dark pixels.
# Adjacent column-runs are merged if separated by <= MERGE_GAP_MAX
# blank columns (single-marking internal gaps such as the space between
# the place name and the date inside a postmark). Surviving column-runs
# narrower than MIN_MARKING_WIDTH are dropped as noise (the column-rule
# bleed-through, page-edge artifacts).
COL_DARK_BRIGHTNESS_MAX = 180
COL_DARK_MIN_PIXELS     = 2
# Bridge intra-marking gaps wider than typical letter spacing -- e.g. a
# small punctuation cluster (19 px wide) separated from the main marking
# by a 28 px gap should still be one marking. Side-by-side catalog
# markings sit 100+ px apart, so this stays safely below the inter-
# marking threshold.
MERGE_GAP_MAX           = 40
MIN_MARKING_WIDTH       = 30
# Page margins (left edge bleed-through, column rule artifacts on the
# right edge) live within EDGE_ZONE_PX of the image edges. A narrow run
# here is dropped BEFORE merging, otherwise two adjacent edge-artifact
# fragments could merge into one run wide enough to bypass the noise
# filter. Real markings in this catalog start at x >= ~100, so this
# zone never overlaps actual content.
EDGE_ZONE_PX            = 50

# Visual breathing room around each emitted marking. Tight crops at the
# first/last dark pixel look razor-thin; pad each bbox by this many px
# on all sides (clamped to image bounds).
MARKING_PADDING_PX      = 8


# ---------------------------------------------------------------------------
# Paths and tee
# ---------------------------------------------------------------------------

class Paths:
    """Per-run filesystem layout, derived from the basename."""

    def __init__(self, basename):
        self.basename       = basename
        self.images_dir     = WIP_DIR / "cache" / basename
        self.input_csv      = WIP_DIR / "cache" / f"{basename}.csv"
        self.subchunks_dir  = WIP_DIR / "cache" / f"{basename}_subchunks"
        self.markings_dir   = WIP_DIR / "cache" / f"{basename}_images"
        self.report_csv     = WIP_DIR / "cache" / f"{basename}_subchunks_report.csv"
        self.run_log        = WIP_DIR / "cache" / f"{basename}_subchunks.log"


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


def _legacy_image_slug_for_cleanup(basename):
    """Return the pre-helper image prefix for stale-output cleanup only."""
    return Path(str(basename)).stem.split("_", 1)[0].lower()


def _cleanup_chunk_outputs(paths, image_slug, page, chunk_seq):
    """Remove current and legacy output files for one page/chunk pair."""
    prefixes = {image_slug, _legacy_image_slug_for_cleanup(paths.basename)}
    for prefix in prefixes:
        subchunk_path = paths.subchunks_dir / f"{prefix}-{page}-{chunk_seq}.png"
        if subchunk_path.exists():
            subchunk_path.unlink()
        for old in paths.markings_dir.glob(
            f"{prefix}-{page}-{chunk_seq}-*.png"
        ):
            old.unlink()


# ---------------------------------------------------------------------------
# CSV ingest
# ---------------------------------------------------------------------------

def load_csv_counts(csv_path):
    """Read tools/wip/cache/<basename>.csv. Returns:
        counts:       {(page, chunk): images_above_sum}
        parse_errors: list of (page, chunk, raw_value) for rows whose
                      Images Above column was not parseable as int.

    The extract tool stores Images Above on the FIRST surviving row of
    each chunk; later rows of the same chunk have 0. Summing per (page,
    chunk) recovers the count even if that convention later changes.
    """
    if not csv_path.exists():
        raise SystemExit(
            f"missing {csv_path}; run ascc_page_extract.py first"
        )
    counts = {}
    parse_errors = []
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                page = int(row["Page"])
                chunk = int(row["Chunk"])
            except (ValueError, KeyError):
                continue
            raw_ia = row.get("Images Above", "")
            try:
                ia = int(float(raw_ia or 0))
            except (ValueError, TypeError):
                parse_errors.append((page, chunk, raw_ia))
                continue
            counts[(page, chunk)] = counts.get((page, chunk), 0) + ia
    return counts, parse_errors


# ---------------------------------------------------------------------------
# Per-chunk split
# ---------------------------------------------------------------------------

def _find_fine_blank_gap(im, y0, y1, min_gap_rows=2, skip_top_rows=20):
    """Scan rows in [y0, y1] of `im` and return the first blank-row run
    of length >= min_gap_rows whose start sits at least skip_top_rows
    below y0. Used to recover the marking-to-text boundary inside a
    row-block that find_blocks couldn't separate (when the gap is
    shorter than the upstream BLANK_RUN=5 threshold).

    Returns (a, b) -- inclusive start and exclusive end of the blank run
    in image-local Y coordinates, or None if no qualifying gap exists.
    """
    arr = np.array(im.crop((0, y0, im.size[0], y1 + 1)).convert("L"))
    H, W = arr.shape
    # Mirror find_blocks: center 90% of width, dark < 180, row 'dark' if >=2.
    left = int(W * 0.05)
    right = int(W * 0.95)
    center = arr[:, left:right]
    dark_per_row = (center < COL_DARK_BRIGHTNESS_MAX).sum(axis=1)
    is_blank = dark_per_row < COL_DARK_MIN_PIXELS

    run_start = None
    for y in range(H):
        if is_blank[y]:
            if run_start is None:
                run_start = y
        else:
            if run_start is not None:
                length = y - run_start
                if length >= min_gap_rows and run_start >= skip_top_rows:
                    return (y0 + run_start, y0 + y)
                run_start = None
    return None


def split_chunk(im, expected, verbose, label):
    """Find the marking-vs-text boundary in a chunk via the LARGEST
    inter-block gap.

    Rationale: every chunk produced upstream is [marking(s) at top] +
    [catalog text at bottom]. The marking-to-text transition is always
    the largest vertical gap between row-blocks (intra-marking blank
    runs are smaller; inter-text-row blank runs are smaller too). We
    used to classify each block with a vision model, but multiple real
    chunks had the model misclassify a block (script postmark called
    'text', the lower 'VA' line of a marking called 'text'). The
    deterministic gap rule sidesteps that entire class of failure.

    Single-block fallback: when find_blocks returns one merged block
    (marking and text packed too tightly to be separated at the
    upstream BLANK_RUN=5 threshold), do a fine-grained scan inside the
    block for any 2+ row blank gap and cut there.

    Returns (cut_y, illus_count, status, notes).
    """
    gray = im.convert("L")
    W, H = im.size
    blocks = find_blocks(gray)

    if not blocks:
        return None, 0, "no_blocks", "find_blocks returned 0 blocks"

    if len(blocks) == 1:
        y0, y1 = blocks[0]
        gap = _find_fine_blank_gap(im, y0, y1)
        if gap is not None:
            a, b = gap
            cut_y = (a + b) // 2
            return (cut_y, 1, "ok",
                    f"cut at y={cut_y}; single-block chunk -- recovered "
                    f"via fine blank-gap scan at rows {a}..{b - 1}")
        cut_y = H
        return (cut_y, 1, "ok",
                f"single-block chunk with no internal gap; using full "
                f"chunk height {H}")

    # Multi-block: cut at the largest inter-block gap.
    best_gap = -1
    best_idx = 0
    for i in range(len(blocks) - 1):
        gap = blocks[i + 1][0] - blocks[i][1] - 1
        if gap > best_gap:
            best_gap = gap
            best_idx = i

    last_illus_y1 = blocks[best_idx][1]
    first_text_y0 = blocks[best_idx + 1][0]
    cut_y = (last_illus_y1 + first_text_y0) // 2
    illus_count = best_idx + 1
    notes = (f"cut at y={cut_y}; largest inter-block gap = {best_gap} px "
             f"between blocks {best_idx + 1} and {best_idx + 2} of "
             f"{len(blocks)}")
    return cut_y, illus_count, "ok", notes


# ---------------------------------------------------------------------------
# Stage 2: deterministic per-marking split with noise filter + gap merge
# ---------------------------------------------------------------------------

def _column_candidates(is_dark, y_start, y_stop, W):
    """Within the Y strip [y_start:y_stop) of the boolean dark mask,
    find substantial column-runs after merging close gaps and dropping
    noise. Returns a list of [x_left, x_right] inclusive ranges in
    image-local X coordinates.
    """
    strip = is_dark[y_start:y_stop, :]
    dark_per_col = strip.sum(axis=0)
    has_content = dark_per_col >= COL_DARK_MIN_PIXELS

    raw_runs = []
    in_run = False
    start = None
    for x in range(W):
        if has_content[x]:
            if not in_run:
                in_run = True
                start = x
        else:
            if in_run:
                raw_runs.append([start, x - 1])
                in_run = False
    if in_run:
        raw_runs.append([start, W - 1])

    # Pre-filter: drop narrow runs in the page-edge zone BEFORE merging.
    # Two adjacent edge-artifact fragments (e.g. a 1-px bar at x=0 and a
    # 6-px column-rule at x=33..38) would otherwise merge into a single
    # 39-px-wide run that bypasses the post-merge noise filter.
    pre_filtered = []
    for r in raw_runs:
        width = r[1] - r[0] + 1
        at_edge = (r[0] < EDGE_ZONE_PX) or (r[1] >= W - EDGE_ZONE_PX)
        if at_edge and width < MIN_MARKING_WIDTH:
            continue
        pre_filtered.append(r)

    # Merge runs separated by gaps <= MERGE_GAP_MAX (bridges letter-internal
    # blanks inside a single marking).
    merged = []
    for r in pre_filtered:
        if merged and r[0] - merged[-1][1] - 1 <= MERGE_GAP_MAX:
            merged[-1][1] = r[1]
        else:
            merged.append([r[0], r[1]])

    # Final safety: drop any remaining narrow runs (e.g. an isolated
    # mid-image speck below MIN_MARKING_WIDTH that was kept by the edge
    # filter).
    return [r for r in merged if r[1] - r[0] + 1 >= MIN_MARKING_WIDTH]


def _tight_y(is_dark, x_left, x_right_inclusive, y_fallback):
    """Within the X range [x_left, x_right_inclusive], find the
    topmost/bottommost rows that contain a dark pixel. Returns
    (y_top, y_bottom_inclusive). Falls back to y_fallback when the
    strip is empty (shouldn't happen if x range came from a real
    candidate)."""
    strip = is_dark[:, x_left:x_right_inclusive + 1]
    rows = np.where(strip.any(axis=1))[0]
    if not len(rows):
        return y_fallback
    return (int(rows[0]), int(rows[-1]))


def _pad_bbox(bbox, W, H, padding=MARKING_PADDING_PX):
    """Expand a (x0, y0, x1, y1) PIL.crop bbox by `padding` px on all
    sides, clamped to (0, W) and (0, H)."""
    x0, y0, x1, y1 = bbox
    return (
        max(0, x0 - padding),
        max(0, y0 - padding),
        min(W, x1 + padding),
        min(H, y1 + padding),
    )


def split_subchunk_into_markings(sub_im, expected):
    """Deterministically segment a sub-chunk into exactly `expected`
    marking bboxes. The CSV count is authoritative.

    Returns a list of (x0, y0, x1, y1) tuples suitable for PIL.crop --
    rows top-to-bottom, within each row left-to-right -- or None when
    the structure cannot satisfy the count.

    The algorithm rejects two failure modes seen in earlier attempts:
      - thin edge bars / column-rule artifacts (dropped via
        MIN_MARKING_WIDTH);
      - single markings with wide internal letter gaps (merged via
        MERGE_GAP_MAX, and for expected=1 the whole content collapses
        to one bbox regardless of internal blanks).
    """
    gray = sub_im.convert("L")
    W, H = sub_im.size
    arr = np.array(gray)
    is_dark = arr < COL_DARK_BRIGHTNESS_MAX

    row_blocks = find_blocks(gray)
    if not row_blocks:
        return None

    y_top = row_blocks[0][0]
    y_bot = row_blocks[-1][1]
    candidates = _column_candidates(is_dark, y_top, y_bot + 1, W)

    if not candidates:
        return None

    if expected == 1:
        x0 = candidates[0][0]
        x1 = candidates[-1][1]
        ty0, ty1 = _tight_y(is_dark, x0, x1, (y_top, y_bot))
        return [_pad_bbox((x0, ty0, x1 + 1, ty1 + 1), W, H)]

    # Side-by-side row case: the count of column candidates matches, OR
    # we have MORE candidates than expected and can collapse extras by
    # iteratively merging adjacent pairs.
    #
    # Merge metric: smallest Y-height difference (tie-broken by smallest
    # X-gap). Y-height is the strongest signal that two adjacent column
    # candidates belong to the same marking: pieces of one marking sit
    # in a similar vertical band, while a separate marking (e.g. a tall
    # circle next to a short town stamp) has a very different height
    # profile. Falling back to X-gap when heights tie reproduces the
    # earlier behavior for cases where all candidates are similarly
    # sized.
    if len(candidates) >= expected:
        cands = [list(c) for c in candidates]
        yranges = []
        for c in cands:
            yranges.append(_tight_y(is_dark, c[0], c[1], (y_top, y_bot)))
        heights = [y1 - y0 + 1 for (y0, y1) in yranges]

        while len(cands) > expected:
            best_idx = None
            best_key = None
            for i in range(len(cands) - 1):
                h_diff = abs(heights[i] - heights[i + 1])
                x_gap = cands[i + 1][0] - cands[i][1] - 1
                key = (h_diff, x_gap)
                if best_key is None or key < best_key:
                    best_key = key
                    best_idx = i
            cands[best_idx][1] = cands[best_idx + 1][1]
            ny0 = min(yranges[best_idx][0], yranges[best_idx + 1][0])
            ny1 = max(yranges[best_idx][1], yranges[best_idx + 1][1])
            yranges[best_idx] = (ny0, ny1)
            heights[best_idx] = ny1 - ny0 + 1
            del cands[best_idx + 1]
            del yranges[best_idx + 1]
            del heights[best_idx + 1]

        result = []
        for i, c in enumerate(cands):
            ty0, ty1 = yranges[i]
            result.append(_pad_bbox((c[0], ty0, c[1] + 1, ty1 + 1), W, H))
        return result

    # Stacked case: one row-block per marking. Each row gets its own
    # noise-filtered column candidates, joined into one X span.
    if len(row_blocks) == expected:
        result = []
        for (y0, y1) in row_blocks:
            row_cands = _column_candidates(is_dark, y0, y1 + 1, W)
            if not row_cands:
                return None
            rx0 = row_cands[0][0]
            rx1 = row_cands[-1][1]
            ty0, ty1 = _tight_y(is_dark, rx0, rx1, (y0, y1))
            # Constrain tight Y to this row-block, otherwise it could
            # spill into another row-block's content.
            ty0 = max(ty0, y0)
            ty1 = min(ty1, y1)
            result.append(_pad_bbox((rx0, ty0, rx1 + 1, ty1 + 1), W, H))
        return result

    return None


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_report(report_csv, rows):
    """Write the per-chunk report. QUOTE_MINIMAL, LF endings (matches
    ascc_page_extract.write_csv style)."""
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=REPORT_COLUMNS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_extract(paths, page_filter, verbose):
    """Walk every chunk on disk. For each chunk with CSV Images Above >= 1
    (and inside the --pages filter), classify its row-blocks, cut above
    the first text block, and save the top portion as a sub-chunk PNG.

    Always writes the report CSV at the end.
    """
    counts, parse_errors = load_csv_counts(paths.input_csv)
    print(
        f"csv map: {len(counts)} (page, chunk) entries; "
        f"{len(parse_errors)} parse error(s)"
    )

    image_slug = catalog_image_slug(paths.basename)
    paths.subchunks_dir.mkdir(parents=True, exist_ok=True)
    paths.markings_dir.mkdir(parents=True, exist_ok=True)


    chunks = discover_chunks(paths.images_dir)
    if not chunks:
        raise SystemExit(f"no PNGs matched in {paths.images_dir}")
    print(f"chunks: {len(chunks)} on disk")

    if page_filter is not None:
        in_scope = [c for c in chunks if c[0] in page_filter]
        print(f"  --pages filter: {len(in_scope)} chunks in scope")

    report_rows = []
    subchunks_emitted = 0
    markings_emitted = 0

    for page, chunk_seq, path in chunks:
        if (page, chunk_seq) not in counts:
            report_rows.append({
                "Page":           page,
                "Chunk":          chunk_seq,
                "Images Above":   "",
                "Markings Found": "",
                "Status":         "not_in_csv",
                "Notes":          "no row in extract CSV",
            })
            continue

        expected = counts[(page, chunk_seq)]
        if expected == 0:
            continue   # CSV says no images above; nothing to extract.

        in_filter = (page_filter is None) or (page in page_filter)
        if not in_filter:
            continue

        label = f"[{page:04d}-{chunk_seq:04d}]"
        found = ""
        with Image.open(path) as im:
            cut_y, illus_count, status, notes = split_chunk(
                im, expected, verbose=verbose, label=label,
            )

            if status == "ok":
                W, _H = im.size
                subchunk = im.crop((0, 0, W, cut_y))
                sub_name = f"{image_slug}-{page}-{chunk_seq}.png"
                _cleanup_chunk_outputs(paths, image_slug, page, chunk_seq)
                # Explicit lossless PNG: format=PNG, no optimize pass,
                # compress_level=0 (stored -- no DEFLATE at all). PNG is
                # already lossless at any level, but compress_level=0
                # removes any doubt that pixel data is identical to the
                # in-memory source.
                subchunk.save(
                    paths.subchunks_dir / sub_name,
                    format="PNG",
                    optimize=False,
                    compress_level=0,
                )
                subchunks_emitted += 1

                # Stage 2: deterministic split (no API calls).
                markings = split_subchunk_into_markings(subchunk, expected)
                if markings is None:
                    status = "marking_count_mismatch"
                    found = 0
                    notes = (
                        f"{notes}; sub-chunk structure cannot satisfy "
                        f"expected={expected} after noise-filter and "
                        f"gap-merge"
                    )
                else:
                    found = len(markings)
                    for i, (x0, y0, x1, y1) in enumerate(markings, 1):
                        marking_im = subchunk.crop((x0, y0, x1, y1))
                        m_name = image_filename(
                            image_slug, page, chunk_seq, i
                        )
                        marking_im.save(
                            paths.markings_dir / m_name,
                            format="PNG",
                            optimize=False,
                            compress_level=1,
                        )
                        markings_emitted += 1
                    notes = f"{notes}; emitted {found} marking(s)"

        report_rows.append({
            "Page":           page,
            "Chunk":          chunk_seq,
            "Images Above":   expected,
            "Markings Found": found,
            "Status":         status,
            "Notes":          notes,
        })

    for page, chunk_seq, raw_ia in parse_errors:
        report_rows.append({
            "Page":           page,
            "Chunk":          chunk_seq,
            "Images Above":   "" if raw_ia is None else raw_ia,
            "Markings Found": "",
            "Status":         "csv_parse_error",
            "Notes":          "Images Above unparseable",
        })

    def _sort_key(r):
        p = r["Page"] if isinstance(r["Page"], int) else 0
        c = r["Chunk"] if isinstance(r["Chunk"], int) else 0
        return (p, c)

    report_rows.sort(key=_sort_key)
    write_report(paths.report_csv, report_rows)
    _print_summary(subchunks_emitted, markings_emitted, report_rows)


def _print_summary(subchunks, markings, rows):
    print()
    print(f"subchunks emitted: {subchunks}")
    print(f"markings emitted:  {markings}")
    print(f"report rows:       {len(rows)}")
    print()
    print("status value counts:")
    status_counts = Counter(r["Status"] for r in rows)
    for status in sorted(status_counts):
        print(f"  {status:<24s} {status_counts[status]:>5d}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Two-stage marking-illustration extractor (fully "
            "deterministic). Stage 1: find row-blocks and cut at the "
            "largest inter-block gap to produce a text-free sub-chunk "
            "PNG. Stage 2: split the sub-chunk into individual markings "
            "via row-block + column-segment analysis with noise "
            "filtering and Y-aware merging; emit one PNG per marking "
            "when the count satisfies the CSV's Images Above, otherwise "
            "log the mismatch and skip marking emission for that chunk."
        ),
    )
    parser.add_argument(
        "basename",
        help=(
            "base name of the catalog (e.g. VA_ASCC_CTLG). Drives input "
            "dir tools/wip/cache/<basename>/, input CSV "
            "tools/wip/cache/<basename>.csv, subchunks dir "
            "tools/wip/cache/<basename>_subchunks/, markings dir "
            "tools/wip/cache/<basename>_images/, report CSV "
            "tools/wip/cache/<basename>_subchunks_report.csv."
        ),
    )
    parser.add_argument(
        "--pages",
        type=parse_pages_arg,
        default=None,
        help=(
            "restrict processing to a page range. Forms: '419', "
            "'419-435', '419,422,430'. Default: no filter."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "print per-chunk progress to stdout and tee to "
            "tools/wip/cache/<basename>_subchunks.log."
        ),
    )
    args = parser.parse_args(argv)

    paths = Paths(args.basename)

    log_fh = None
    saved_stdout = sys.stdout
    if args.verbose:
        paths.run_log.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(paths.run_log, "a")
        ts = datetime.now().isoformat(timespec="seconds")
        argv_str = " ".join(sys.argv[1:])
        log_fh.write(
            f"\n========== {ts}  argv: {argv_str} ==========\n"
        )
        log_fh.flush()
        sys.stdout = _Tee(saved_stdout, log_fh)

    try:
        if args.verbose:
            print(f"verbose log: tee-ing to {paths.run_log}")
        print(f"basename:        {paths.basename}")
        print(f"stage1:          deterministic (largest inter-block gap)")
        print(f"stage2:          deterministic "
              f"(min_width={MIN_MARKING_WIDTH}, merge_gap={MERGE_GAP_MAX})")
        if args.pages is not None:
            ids = args.pages
            sample = ",".join(str(x) for x in sorted(ids)[:6])
            more = "..." if len(ids) > 6 else ""
            print(f"pages:    count={len(ids)} ({sample}{more})")
        else:
            print("pages:    (no filter)")
        print()
        run_extract(paths, args.pages, args.verbose)
    finally:
        if log_fh is not None:
            sys.stdout = saved_stdout
            log_fh.close()


if __name__ == "__main__":
    main()
