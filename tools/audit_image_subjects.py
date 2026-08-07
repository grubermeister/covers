#!/usr/bin/env python3
"""Find images filed under the wrong subject and produce an editor review list.

Issue #78. The v1 -> v2 importer attached every legacy image to the marking,
including the ones v1 recorded as scans of a whole cover (issue #75 fixes the
importer; this cleans up what already landed). Measured 2026-08-05: of 374
cover-shaped images in marking slots on prod, 368 came from that import.

Read-only by construction. It talks to the public API over HTTPS and writes
CSVs; it never mutates the catalog. Repairs are applied by an editor through
the crop (#77) and move (#48) endpoints, which is Ian's stated preference on
issue #50 -- the machine detects, humans fix.

Three passes, cheapest first:

  A  deterministic -- image_description holds v1's own txtView ('Front',
     'Back', 'Details'), because the importer wrote it there instead of
     routing on it. No guessing needed. 44 rows on prod, 20 on staging.
  B  heuristic     -- rank the remainder by aspect ratio and megapixels.
     Triage ordering only, never an auto-apply.
  C  vision        -- opt-in (--vision), costs money. Classifies the ranked
     list so a human reviews a sorted, pre-labelled CSV instead of raw
     thumbnails.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python tools/audit_image_subjects.py \\
        --host hellowoco.app --out-dir tools/wip/out/image-audit

Expected exit code: 0.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

# Shape thresholds, mirrored from frontend/src/lib/imageShape.ts so the audit
# and the upload-time warning agree on what "cover-shaped" means. Derived by
# measuring both live sites: marking closeups cluster at 0.01-0.05 MP and are
# roughly square, cover scans at 1.0-1.75 MP and landscape.
COVER_MIN_ASPECT = 1.25
COVER_MIN_PIXELS = 600_000
MARKING_MAX_ASPECT = 1.25
MARKING_MAX_PIXELS = 300_000

# v1 view labels the importer stranded in image_description. Front/Back are
# COVER views in v2's vocabulary (common.models.IMAGE_COVER_VIEW_CHOICES).
V1_COVER_VIEWS = {"front", "back"}
V1_MARKING_VIEWS = {"details"}

# Legacy filenames look like 'Marking-<rawStateDataId>-<townmarkImageId>.jpg';
# the contribution flow stores '<uuid4hex>.<ext>'. Distinguishes an inherited
# defect from live user error.
V1_FILENAME_RE = re.compile(r"^Marking-\d+-\d+\.", re.IGNORECASE)

VISION_SYSTEM_PROMPT = (
    "You classify philatelic images for a postal-history catalog. Answer with "
    "JSON only."
)
VISION_USER_TEXT = (
    "Does this image show an ENTIRE COVER (a whole envelope, folded letter, or "
    "postal card -- typically showing an address panel, and often several "
    "markings at once), or a CLOSE-UP OF A SINGLE MARKING (a postmark, "
    "handstamp, or manuscript marking filling most of the frame)?\n\n"
    'Reply with exactly: {"kind": "cover"} or {"kind": "marking"} or '
    '{"kind": "unclear"}.'
)

REVIEW_COLUMNS = [
    "image_id",
    "subject_type",
    "subject_id",
    "verdict",
    "confidence",
    "reason",
    "origin",
    "image_width",
    "image_height",
    "aspect",
    "megapixels",
    "v1_view",
    "original_filename",
    "image_url",
]


def fetch_all(host: str, path: str, page_size: int = 200) -> list[dict]:
    """Page through a DRF list endpoint and return every result row."""
    rows: list[dict] = []
    url = f"https://{host}{path}?page_size={page_size}"
    while url:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.load(response)
        rows.extend(payload.get("results", []))
        url = payload.get("next")
    return rows


def aspect_of(row: dict) -> float:
    height = row.get("image_height") or 0
    width = row.get("image_width") or 0
    return (width / height) if height else 0.0


def megapixels_of(row: dict) -> float:
    return ((row.get("image_width") or 0) * (row.get("image_height") or 0)) / 1e6


def shape_of(row: dict) -> str:
    """'cover-like', 'marking-like', or 'indeterminate'.

    Three-way on purpose, matching classifyImageShape in imageShape.ts. The
    middle band is genuinely ambiguous -- a wide marking crop and a small cover
    photo overlap there -- so it produces no verdict in either direction rather
    than filling the review list with rows a human cannot decide either.
    """
    aspect = aspect_of(row)
    pixels = megapixels_of(row) * 1e6
    if aspect <= 0 or pixels <= 0:
        return "indeterminate"
    if aspect >= COVER_MIN_ASPECT and pixels >= COVER_MIN_PIXELS:
        return "cover-like"
    if aspect < MARKING_MAX_ASPECT and pixels <= MARKING_MAX_PIXELS:
        return "marking-like"
    return "indeterminate"


def origin_of(row: dict) -> str:
    """Whether this row came from the v1 import or a live contributor upload."""
    filename = (row.get("original_filename") or "").strip()
    return "v1-legacy" if V1_FILENAME_RE.match(filename) else "user-upload"


def classify_row(row: dict) -> tuple[str, str, str] | None:
    """Return (verdict, confidence, reason), or None when there is no signal.

    Verdict is what the image looks like it should be attached to, NOT an
    instruction: 'COVER' means the pixels look like a whole cover. Only rows
    where that disagrees with subject_type end up needing an editor.
    """
    v1_view = (row.get("image_description") or "").strip().lower()
    if v1_view in V1_COVER_VIEWS:
        return "COVER", "certain", f"v1 recorded this as '{v1_view}'"
    if v1_view in V1_MARKING_VIEWS:
        return "MARKING", "certain", f"v1 recorded this as '{v1_view}'"
    shape = shape_of(row)
    if shape == "cover-like":
        return (
            "COVER",
            "likely",
            f"landscape {aspect_of(row):.2f}:1 at {megapixels_of(row):.2f} MP",
        )
    if shape == "marking-like":
        return (
            "MARKING",
            "likely",
            f"square-ish {aspect_of(row):.2f}:1 at {megapixels_of(row):.2f} MP",
        )
    return None


def mismatched(row: dict, verdict: str) -> bool:
    return verdict != (row.get("subject_type") or "").upper()


def review_record(row: dict, verdict: str, confidence: str, reason: str) -> dict:
    return {
        "image_id": row.get("image_id"),
        "subject_type": row.get("subject_type"),
        "subject_id": row.get("subject_id"),
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
        "origin": origin_of(row),
        "image_width": row.get("image_width"),
        "image_height": row.get("image_height"),
        "aspect": f"{aspect_of(row):.2f}",
        "megapixels": f"{megapixels_of(row):.2f}",
        "v1_view": (row.get("image_description") or "").strip(),
        "original_filename": row.get("original_filename"),
        "image_url": row.get("image_url"),
    }


def state_of(row: dict) -> str:
    """State slug from storage_filename ('va/<file>' -> 'va').

    Editors are region-scoped (IsResponsibleForRegion), so a single global list
    is not actionable -- each editor can only act on their own state.
    """
    storage = (row.get("storage_filename") or "").strip("/")
    return storage.split("/")[0].lower() if "/" in storage else "unknown"


# pipeline_llm.vision_text hardcodes an image/png media type (it was written for
# the PDF pipeline, which renders PNG pages). Catalog images are JPEG, and the
# providers reject a JPEG declared as PNG, so convert before sending rather than
# changing the shared module the whole state pipeline depends on.
VISION_MAX_EDGE = 1024


def to_png_bytes(image_bytes: bytes) -> bytes:
    """Decode any allowed upload and re-encode as a downscaled PNG.

    Downscaling to VISION_MAX_EDGE is not just thrift: cover-vs-marking is a
    whole-frame judgment, and a 1.7 MP scan costs far more per call without
    telling the model anything a 1024 px view does not.
    """
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))
        out = io.BytesIO()
        img.save(out, format="PNG")
    return out.getvalue()


def vision_verdict(llm, model, image_bytes: bytes) -> tuple[str, str]:
    """Ask the vision model what this image shows. Returns (verdict, raw)."""
    image_b64 = base64.b64encode(to_png_bytes(image_bytes)).decode("ascii")
    raw = llm.vision_text(
        model=model,
        max_tokens=64,
        system_prompt=VISION_SYSTEM_PROMPT,
        user_text=VISION_USER_TEXT,
        image_b64=image_b64,
    )
    text = (raw or "").strip()
    try:
        kind = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))["kind"]
    except (AttributeError, ValueError, KeyError, TypeError):
        # Substring fallback matches ascc_page_processor.classify_block, which
        # hit the same "model ignored the JSON instruction" case.
        lowered = text.lower()
        if "cover" in lowered and "marking" not in lowered:
            kind = "cover"
        elif "marking" in lowered and "cover" not in lowered:
            kind = "marking"
        else:
            kind = "unclear"
    mapping = {"cover": "COVER", "marking": "MARKING"}
    return mapping.get(str(kind).lower(), "UNCLEAR"), text


def run_vision_pass(records: list[dict], provider: str | None, model_arg: str | None,
                    limit: int) -> None:
    """Overwrite verdicts on `records` in place using a vision model."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pipeline_llm import make_pipeline_llm, resolve_model, resolve_provider

    resolved_provider = resolve_provider(provider)
    model = resolve_model(resolved_provider, model_arg)
    llm = make_pipeline_llm(resolved_provider)
    print(f"vision pass: {resolved_provider} / {model} over {min(limit, len(records))} images")

    for index, record in enumerate(records[:limit], start=1):
        url = record.get("image_url")
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                image_bytes = response.read()
            verdict, _raw = vision_verdict(llm, model, image_bytes)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # One unreachable image must not abandon the rest of the run.
            print(f"  WARNING: image {record['image_id']}: {exc}")
            continue
        if verdict == "UNCLEAR":
            record["confidence"] = "unclear"
            record["reason"] = f"{record['reason']}; vision was unsure"
            continue
        agrees = verdict == record["verdict"]
        record["verdict"] = verdict
        record["confidence"] = "confirmed" if agrees else "vision-disagrees"
        record["reason"] = f"{record['reason']}; vision says {verdict.lower()}"
        if index % 25 == 0:
            print(f"  {index}/{min(limit, len(records))}")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--host", default="hellowoco.app",
                        help="API host to audit (default: %(default)s)")
    parser.add_argument("--out-dir", default="tools/wip/out/image-audit",
                        help="directory for the per-state review CSVs")
    parser.add_argument("--images-json", default=None,
                        help="read rows from this file instead of the API")
    parser.add_argument("--vision", action="store_true",
                        help="run pass C (costs money; needs an LLM key)")
    parser.add_argument("--vision-limit", type=int, default=1000,
                        help="cap vision calls (default: %(default)s)")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    if args.images_json:
        rows = json.loads(Path(args.images_json).read_text(encoding="utf-8"))
        print(f"loaded {len(rows)} images from {args.images_json}")
    else:
        print(f"fetching images from {args.host} ...")
        rows = fetch_all(args.host, "/api/v2/images/")
        print(f"fetched {len(rows)} images")

    needs_review: list[dict] = []
    no_signal = 0
    for row in rows:
        classified = classify_row(row)
        if classified is None:
            no_signal += 1
            continue
        verdict, confidence, reason = classified
        if mismatched(row, verdict):
            needs_review.append(review_record(row, verdict, confidence, reason))

    # Certain rows first, then the heuristic's strongest candidates -- so a
    # reviewer working top-down spends their attention where it pays.
    needs_review.sort(
        key=lambda r: (
            0 if r["confidence"] == "certain" else 1,
            -float(r["megapixels"]),
        )
    )

    print(f"\npass A (v1 view label, deterministic): "
          f"{sum(1 for r in needs_review if r['confidence'] == 'certain')} mismatched")
    print(f"pass B (shape heuristic): "
          f"{sum(1 for r in needs_review if r['confidence'] == 'likely')} candidates")
    print(f"total needing review: {len(needs_review)} of {len(rows)} images")
    print(f"by origin: {dict(Counter(r['origin'] for r in needs_review))}")
    print(f"by direction: {dict(Counter(r['subject_type'] + ' -> ' + r['verdict'] for r in needs_review))}")
    # Stated explicitly so the run is not read as full coverage: these images
    # sit in the ambiguous middle band and no pass reached a verdict on them.
    print(f"no signal either way (not in any CSV): {no_signal}")

    if args.vision:
        run_vision_pass(needs_review, args.provider, args.model, args.vision_limit)
        print(f"after vision: {dict(Counter(r['confidence'] for r in needs_review))}")
    elif len(needs_review) > args.vision_limit:
        print(f"NOTE: {len(needs_review)} rows exceeds --vision-limit "
              f"({args.vision_limit}); a vision pass would cover only the first "
              f"{args.vision_limit}.")

    out_dir = Path(args.out_dir)
    by_state = defaultdict(list)
    row_by_id = {r.get("image_id"): r for r in rows}
    for record in needs_review:
        by_state[state_of(row_by_id.get(record["image_id"], {}))].append(record)

    write_csv(out_dir / "all-states.csv", needs_review)
    for state, records in sorted(by_state.items()):
        write_csv(out_dir / f"{state}.csv", records)
        print(f"  {state}: {len(records)} -> {out_dir / f'{state}.csv'}")
    print(f"\nwrote {len(needs_review)} rows to {out_dir}")
    print("Nothing was modified. Repairs go through the crop (#77) and move "
          "(#48) endpoints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
