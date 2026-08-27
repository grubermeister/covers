"""
Apply the reviewed VPHC ledger: markings, their scans, and duplicate archiving.

Phase C of the VPHC ingest. Run *after* import_vphc_reference -- a contribution
cannot name a town that has no post office.

Runbook:
  cwd: repo root
  command: uv run python backend/manage.py apply_vphc_ledger \
               --vphc-dir ../docs/vphc --actor 1 --dry-run
  expected exit code: 0

Markings arrive as **pending contributions**, not catalog rows, so an editor
reviews every one before it enters the catalog (Reese, 2026-08-12). The
alternative -- writing markings directly with is_reviewed=False -- puts them in
no queue at all, because the dashboard reads Contribution and never looks at
the catalog.

Two inputs, and the split is deliberate:

  crossexam/ledger/proposed.jsonl   the authority on *what happens*, reviewed
                                    beforehand and replayed verbatim into
                                    docs/vphc/LEDGER.jsonl
  crossexam/crosswalk.csv           the field values the sheet actually
                                    supplies; approval merges these into the
                                    existing row rather than replacing it

Nothing is invented. Where the source cannot supply a required field -- a
device code the vocabulary does not know, leaving no type and no inscription --
the fallback is recorded as a flag, written into the record's description, and
spelled out in the note the editor reads.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.audit import log_marking_removed
from common.images import read_image_metadata_from_path
from common.models import (
    Citation,
    Collection,
    Color,
    Contribution,
    Marking,
    MarkingRecycleBin,
    PostOfficeRegion,
    ReferenceWork,
    Region,
)

VPHC_REFERENCE_CODE = "VPHC1"
DEFAULT_TYPE = "TOWNMARK"

# Phase 2's outputs. Overridable so Phase 6 can point at its own pair without
# either pass being able to disturb the other's files.
DEFAULT_LEDGER = "crossexam/ledger/proposed.jsonl"
DEFAULT_CROSSWALK = "crossexam/crosswalk.csv"


def read_csv(path):
    if not os.path.exists(path):
        raise CommandError(f"missing input: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def first(value, sep=";"):
    for part in (value or "").split(sep):
        if part.strip():
            return part.strip()
    return ""


def dec(value):
    try:
        return str(round(float(value), 2))
    except (TypeError, ValueError):
        return None


# Rule I2: the rate is derived from the `cancel` device vocabulary, which
# reaches us as the sheet's inscription keys. It is NEVER derived from
# `cancel_no` -- that is the drawing number, the marking's sequence within its
# town, and the key that names its scan (rule E5). _contribution() uses it
# correctly, as a citation page_number: it is a locator, not a rate.
#
# Mirrors sheet_identity() in tools/vphc_crossexam.py, which computes exactly
# this as `sheet_rate` and then drops it on the floor -- CROSSWALK_FIELDS does
# not carry the column, so the value cannot reach us. Deleting this duplication
# means emitting sheet_rate into crosswalk.csv; that is filed separately,
# because regenerating the crosswalk re-runs the whole cross-examination.
ROMAN_RATE = {"X": "10", "V": "5"}
# Production spells the same device both ways round -- "PAID/3" and "3/PAID"
# are one marking -- so both orders must yield the digits, never the word.
RATE_KEY_RE = re.compile(
    r"^(?:(PAID|DUE|WAY)[\s/]+(?P<after>\d+)|(?P<before>\d+)[\s/]+(PAID|DUE|WAY))$",
    re.I)


def rate_from_inscription(sheet_insc, sep=";"):
    """The rate a RATEMARK's device states, or "" if it states none.

    Reads every key rather than first(), because sheet_insc is a sorted set:
    "3/DUE;DUE 3;DUE/3" leads with "3/DUE", and taking only that one is what
    sent 276 listings to the drawing number instead (issue #120).
    """
    for part in (sheet_insc or "").split(sep):
        key = part.strip()
        if not key:
            continue
        if key.isdigit():
            return key
        if key.upper() in ROMAN_RATE:
            return ROMAN_RATE[key.upper()]
        found = RATE_KEY_RE.match(key)
        if found:
            return found.group("after") or found.group("before")
    return ""


class Command(BaseCommand):
    help = "Create VPHC marking contributions, attach scans, archive duplicates."

    def add_arguments(self, parser):
        parser.add_argument("--vphc-dir", required=True)
        parser.add_argument("--actor", type=int, default=1)
        parser.add_argument(
            "--only", default="",
            help="Comma-separated subset of: create,update,images,archive")
        parser.add_argument(
            "--auto-approve", default="",
            help="Comma-separated verdict buckets to approve immediately "
                 "instead of queueing (e.g. create_no_town)")
        # Phase 6 (manuscripts) emits its own ledger and crosswalk into
        # <vphc-dir>/manuscripts/ rather than reusing the Phase 2 files, so the
        # shipped artifacts stay exactly as they were applied. Both default to
        # the Phase 2 paths, which keeps the shipped invocation byte-identical.
        parser.add_argument(
            "--ledger", default=DEFAULT_LEDGER,
            help=f"Ledger path, relative to --vphc-dir (default {DEFAULT_LEDGER})")
        parser.add_argument(
            "--crosswalk", default=DEFAULT_CROSSWALK,
            help=f"Crosswalk path, relative to --vphc-dir "
                 f"(default {DEFAULT_CROSSWALK})")
        parser.add_argument("--dry-run", action="store_true")

    # ------------------------------------------------------------------ main

    def handle(self, *args, **opts):
        csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
        vphc = opts["vphc_dir"]
        dry = opts["dry_run"]
        steps = {s.strip() for s in opts["only"].split(",") if s.strip()} or {
            "create", "update", "images", "archive"}

        try:
            actor = get_user_model().objects.get(pk=opts["actor"])
        except get_user_model().DoesNotExist:
            raise CommandError(f"no user with id {opts['actor']}")
        try:
            self.reference = ReferenceWork.objects.get(code=VPHC_REFERENCE_CODE)
        except ReferenceWork.DoesNotExist:
            raise CommandError(
                f"{VPHC_REFERENCE_CODE} is not in reference_work -- the Virginia "
                "Postal History Catalog must be seeded before its markings can "
                "cite it")

        ledger = [json.loads(line) for line in
                  open(os.path.join(vphc, opts["ledger"]), encoding="utf-8")]
        crosswalk = read_csv(os.path.join(vphc, opts["crosswalk"]))
        self.media_src = os.path.join(vphc, "extract", "media")

        # Resolved on demand: a run covering only Virginia should not need West
        # Virginia to be set up.
        self.collections = {}
        for abbrev in ("VA", "WV"):
            region = Region.objects.filter(abbrev=abbrev, region_tier="STATE").first()
            collection = Collection.objects.filter(
                region=region, is_active=True).first() if region else None
            if collection is not None:
                self.collections[abbrev] = collection
        if not self.collections:
            raise CommandError(
                "no active Collection for VA or WV -- contributions have "
                "nowhere to be filed")

        self.auto = {s.strip() for s in opts["auto_approve"].split(",") if s.strip()}
        self.scanned = set()
        totals = Counter()
        applied = []
        # A skipped row used to bump a stdout counter and vanish. Four listings
        # left the ledger and never reached the queue that way, and nothing on
        # disk recorded which four -- the discrepancy was only found by counting
        # the ledger against the queue a week later. Issue #115.
        self.skipped = []

        if dry:
            self.stdout.write(self.style.NOTICE("DRY RUN: nothing will be committed."))

        try:
            with transaction.atomic():
                if {"create", "update", "images"} & steps:
                    self._contributions(crosswalk, ledger, steps, actor, totals,
                                        applied, dry)
                if "archive" in steps:
                    self._archive(ledger, actor, totals, applied)
                if dry:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(self.style.ERROR("Aborted; all changes rolled back."))
            raise

        for key in sorted(totals):
            self.stdout.write(f"  {key:<40} {totals[key]:>6}")
        if not dry and applied:
            self._write_ledger(vphc, applied)
            self.stdout.write(f"  appended {len(applied)} lines to LEDGER.jsonl")
        if self.skipped:
            path = self._write_skipped(vphc)
            self.stdout.write(self.style.WARNING(
                f"  {len(self.skipped)} listing(s) skipped -- written to {path}"))
        self.stdout.write(self.style.SUCCESS(
            ("[DRY RUN] " if dry else "") + "VPHC ledger applied."))

    # ---------------------------------------------------------------- skips

    def _skip(self, line, row, reason):
        """Record a listing the applier declined to emit.

        Rule C1 says nothing from the sheet is ever dropped. When a row cannot
        be emitted that is still true in spirit -- it is a refusal, not a loss --
        but only if the refusal leaves a trace a human can find.
        """
        row = row or {}
        self.skipped.append({
            "src": line.get("src", ""),
            "action": line.get("action", ""),
            "target_code": line.get("target_code") or "",
            "reason": reason,
            "town": row.get("town", ""),
            "town_key": row.get("town_key", ""),
            "county": row.get("county", ""),
            "state": row.get("state", ""),
            "cancel_no": row.get("cancel_no", ""),
            "landing": row.get("landing", ""),
            "verdict": row.get("verdict", ""),
        })

    def _write_skipped(self, vphc):
        path = os.path.join(vphc, "crossexam", "skipped.csv")
        fields = ["src", "action", "target_code", "reason", "town", "town_key",
                  "county", "state", "cancel_no", "landing", "verdict"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for entry in self.skipped:
                writer.writerow(entry)
        return path

    # ------------------------------------------------------------- payloads

    def _contributions(self, crosswalk, ledger, steps, actor, totals, applied, dry):
        # The ledger says which rows act; the crosswalk carries their values.
        by_src = defaultdict(list)
        for row in crosswalk:
            by_src[row["src"]].append(row)
        changed = {(l["src"], l["target_code"]): l
                   for l in ledger if l["action"] == "update" and l["fields"]}

        for line in ledger:
            action = line["action"]
            if action == "create" and "create" in steps:
                row = next((r for r in by_src.get(line["src"], [])
                            if not r["prod_code"]), None)
                if row is None:
                    totals["creates skipped (no crosswalk row)"] += 1
                    self._skip(line, None, "no crosswalk row")
                    continue
                self._contribution(row, line, None, actor, totals, applied, dry)
            elif action == "update" and "update" in steps:
                key = (line["src"], line["target_code"])
                if key not in changed:
                    continue
                row = next((r for r in by_src.get(line["src"], [])
                            if r["prod_code"] == line["target_code"]), None)
                if row is None:
                    totals["updates skipped (no crosswalk row)"] += 1
                    self._skip(line, None, "no crosswalk row")
                    continue
                self._contribution(row, line, line["target_code"], actor,
                                   totals, applied, dry)

    def _contribution(self, row, line, edit_code, actor, totals, applied, dry):
        state = row["state"].strip().upper()
        if state not in self.collections:
            # The sheet leaves the state blank when the county is missing, but
            # the town may already be in the catalog -- in which case its post
            # office already knows which state it is in. Ask that rather than
            # discarding the marking.
            state = self._state_from_catalog(row["town_key"])
            if state is None:
                totals["skipped (state cannot be determined)"] += 1
                self._skip(line, row, "state cannot be determined")
                return
            totals["state recovered from an existing post office"] += 1

        flags = [f for f in row["flags"].split(";") if f]
        marking_type = row["sheet_type"].strip().upper()
        if not marking_type:
            marking_type = DEFAULT_TYPE
            flags.append("type_defaulted")
        inscription = first(row["sheet_insc"]) or row["town"].strip()
        if not inscription:
            totals["skipped (no inscription and no town)"] += 1
            self._skip(line, row, "no inscription and no town")
            return

        colours = [c for c in row["sheet_colors"].split(";") if c]
        known = self._colour_names()
        recognised = [c for c in colours if c.upper() in known]
        unrecognised = [c for c in colours if c.upper() not in known]

        # E5: one scan, one attachment. A sheet marking that matched several
        # fan-out members is one physical device recorded in several colours,
        # so copying its scan onto each colour variant would assert that the
        # same photograph is evidence for three separate records.
        vphc_key = f"{row['town_key']}#{row['cancel_no']}"
        if vphc_key in self.scanned:
            images = []
            totals["scans not repeated across colour variants"] += 1
        else:
            images = self._images(row, totals, dry)
            if images:
                self.scanned.add(vphc_key)

        # Production's grain is one Marking row per type x colour -- the
        # incumbent ASCC shape, settled 2026-08-15 (docs/DECISIONS.md). A NEW
        # listing recorded in three colours is therefore three markings. The
        # ingest used to emit one, keep colours[0], and bury the rest in the
        # description as "Colours recorded: ...", which is the book's
        # (town, cancel #) grain, not the site's: 277 of 1,798 create listings
        # with 347 colour observations stranded in prose.
        #
        # AN EDIT NEVER WRITES A COLOUR. Rule E6: colour is doing identity work
        # on this path -- I3 paired the edit to exactly one live row *by* colour
        # -- so writing it back could only restate what production already
        # holds, and the v2 invariant forbids a field both establishing identity
        # for a pair and being written for that same pair.
        #
        # This used to send `colours[0]`, the sheet's FIRST colour, which is a
        # different thing from the colour I3 matched on (`match_color`). For a
        # multi-colour listing they diverge: an edit targeting the BLUE row of a
        # RED;BLUE listing went out carrying RED, and approval repainted it.
        # Measured on woco.dev 2026-08-20 -- 58 of the 310, and it closed both
        # ways: on 58 of 58 the live colour equalled `match_color`, and on 58 of
        # 58 the payload colour equalled `sheet_colors[0]`. Issue #117.
        #
        # ⚠ The old comment here recorded "3 of the 310 edits changed, one of
        # them RED -> BLACK." That was the delta between two *candidate
        # implementations* (`colours[0]` vs `recognised[0]`), never a claim that
        # only 3 differed from the live marking -- but it was read as
        # reassurance for four days. The absolute question was 58.
        #
        # Emitting no colour is stronger than emitting `match_color`: it is
        # E6-clean, and it sources the value from the live row rather than a CSV
        # column. `_emit` then skips the `color` key, and the edit-only backfill
        # writes `marking.color.name` -- the marking's own value, a true no-op.
        # Where the marking has no colour the key is absent entirely and
        # `_payload_mentions_fk` leaves the field alone. Both branches are safe.
        #
        # `sheet_colour` is still computed because it is the sole producer of
        # the `color_unrecognised` flag, which is queried and asserted on. Only
        # its use as a payload value is removed.
        if edit_code:
            sheet_colour = colours[0] if colours else None
            if sheet_colour and sheet_colour.upper() not in known:
                flags.append("color_unrecognised")
                totals["colours not in the catalog vocabulary"] += 1
            colour_variants = [None]
        else:
            if unrecognised:
                # A colour the catalog has never heard of is dropped silently
                # by the contribution path. Say so instead.
                flags.append("color_unrecognised")
                totals["colours not in the catalog vocabulary"] += len(unrecognised)
            colour_variants = recognised or [None]
        if len(colour_variants) > 1:
            totals["create listings fanned out by colour"] += 1
            totals["extra rows from the colour fan-out"] += len(colour_variants) - 1

        for variant_index, colour in enumerate(colour_variants):
            if not self._emit(
                row, line, edit_code, actor, totals, applied, dry,
                state=state, flags=flags, marking_type=marking_type,
                inscription=inscription, colours=colours,
                unrecognised=unrecognised, colour=colour,
                # E5 again, one level down: the scan is evidence for one
                # physical device, so within a fanned-out listing it attaches
                # to the first colour variant only.
                images=images if variant_index == 0 else [],
            ):
                return
        # One line per ledger entry, not per contribution: LEDGER.jsonl is the
        # replayable record of what the ledger decided, and a colour fan-out is
        # one decision that produced several rows.
        applied.append({**line, "applied": True, "mode": "applied"})

    def _emit(self, row, line, edit_code, actor, totals, applied, dry, *,
              state, flags, marking_type, inscription, colours, unrecognised,
              colour, images):
        """Build and create one contribution. False means stop the whole row."""
        # Phase 6. The ledger line is the authority on what happens, so the
        # manuscript decision rides there rather than in a new crosswalk column:
        # a Phase 2 line has no `is_manuscript` in `after` and so still reads
        # False, which is exactly what it did when it was applied.
        #
        # is_irreg is omitted entirely for a manuscript rather than sent as
        # False. contribution_apply forces it to None either way (the model's
        # marking_manuscript_consistency constraint requires NULL), but
        # submitted_data is read back by the review UI, and "not irregular" is a
        # different claim from "irregularity does not apply to this marking".
        is_manuscript = bool(line.get("after", {}).get("is_manuscript", False))
        payload = {
            "submission_kind": "marking",
            "type": marking_type,
            "state": state,
            "town": row["town"].strip().title(),
            "inscription_txt": inscription,
            "is_manuscript": is_manuscript,
            "desc": self._description(
                row, flags,
                unplaced_colours=colours if edit_code else (),
                rejected_colours=() if edit_code else unrecognised,
            ),
            "reference_work_ids": [self.reference.pk],
            "reference_work_details": [{
                "reference_work_id": self.reference.pk,
                # The VPHC cancel number is exactly what a citation detail is
                # for: the place within the reference work.
                "page_number": row["cancel_no"],
            }],
            # Uncertainty travels with the record, queryable before approval.
            "vphc": {
                "src": row["src"], "cancel_no": row["cancel_no"],
                "vphc_code": row["vphc_code"], "rules_version": line["rule_version"],
                "why_unmatched": line.get("why_unmatched", ""),
                "flags": flags, "county": row["county"], "state": state,
            },
            "contributor_comment": line.get("editor_comment", ""),
        }
        if not is_manuscript:
            payload["is_irreg"] = False
        if dec(row["sheet_width"]):
            payload["width_mm"] = dec(row["sheet_width"])
        if dec(row["sheet_height"]):
            payload["height_mm"] = dec(row["sheet_height"])
        if row["sheet_shape"].strip():
            payload["shape"] = row["sheet_shape"].strip()
        if colour:
            payload["color"] = colour
        if marking_type == "RATEMARK":
            # No rate in the device means no rate. The key stays absent rather
            # than empty: _apply_marking_edit merges on presence (issue #111),
            # so absent leaves a live marking's own rate alone where "" would
            # clear it, and a guess would be wrong while looking deliberate.
            rate = rate_from_inscription(row["sheet_insc"])
            if rate:
                payload["rate_val"] = rate
        self._dates(row, payload)

        if images:
            payload["marking_image_metas"] = images
            payload["image_metas"] = images
            payload["image_meta"] = images[0]
        else:
            # _sync_images refuses a marking that neither has an image nor says
            # it has none on purpose.
            payload["no_marking_image"] = True

        if edit_code:
            marking = Marking.all_objects.filter(code=edit_code).first()
            if marking is None:
                totals["updates skipped (marking gone)"] += 1
                return False
            payload["edit_marking_id"] = marking.pk
            # Approval merges rather than replaces, so these are no longer
            # needed to protect the record -- they are here so the review UI,
            # which renders submitted_data as the field rows an editor reads,
            # shows the marking's actual values instead of blank rows.
            payload.setdefault("lettering", marking.lettering.name
                               if marking.lettering_id else None)
            if "color" not in payload and marking.color_id:
                payload["color"] = marking.color.name
            if "shape" not in payload and marking.shape_id:
                payload["shape"] = marking.shape.name
            if "width_mm" not in payload and marking.width is not None:
                payload["width_mm"] = str(marking.width)
            if "height_mm" not in payload and marking.height is not None:
                payload["height_mm"] = str(marking.height)
            payload["is_irreg"] = bool(marking.is_irreg)
            # Citations are the one field where silence is not an option: the
            # payload has to name VPHC1 to add it, and naming any id states the
            # complete desired set. So the marking's existing bibliography --
            # in practice its ASCC citation -- has to travel with it or
            # approval deletes it. VPHC1 stays first: catalog_codes derives the
            # code prefix from the first id.
            already_cited = Citation.objects.filter(
                subject_type="MARKING", subject_id=marking.pk
            ).values_list("reference_work_id", flat=True)
            payload["reference_work_ids"] = [self.reference.pk] + [
                rid for rid in dict.fromkeys(already_cited)
                if rid != self.reference.pk
            ]

        status = (Contribution.STATUS_APPROVED
                  if line.get("why_unmatched") in self.auto
                  else Contribution.STATUS_PENDING)
        Contribution.objects.create(
            contributor=actor, collection=self.collections[state],
            submitted_data=payload, status=status,
            created_by=actor, modified_by=actor)
        totals[f"contributions created ({'edit' if edit_code else 'new'})"] += 1
        if flags:
            totals["contributions carrying a flag"] += 1
        return True

    def _colour_names(self):
        if not hasattr(self, "_colours"):
            self._colours = {c.upper() for c in
                             Color.objects.values_list("name", flat=True)}
        return self._colours

    def _state_from_catalog(self, town_key):
        """Which state an existing town sits in, by its post office's region."""
        if not hasattr(self, "_town_states"):
            # Only VA and WV. Town names repeat across states, so an unscoped
            # index would answer "Michigan" for a Virginia town and the
            # marking would be filed against the wrong state's collection.
            self._town_states = {}
            for por in PostOfficeRegion.objects.filter(
                    region__region_tier="STATE",
                    region__abbrev__in=list(self.collections)).select_related(
                        "post_office", "region"):
                key = re.sub(r"[^A-Z0-9]", "", por.post_office.name.upper())
                self._town_states.setdefault(key, por.region.abbrev.upper())
        state = self._town_states.get(town_key)
        return state if state in self.collections else None

    def _description(self, row, flags, unplaced_colours=(), rejected_colours=()):
        """Two different colour remainders, deliberately worded differently.

        `unplaced_colours` (edit path): the row already exists and
        Marking.color is single-valued, so the sheet's other observations have
        nowhere to go but prose. Wording and the >1 guard are unchanged from
        before the fan-out, so the 310 queued edits stay byte-identical.

        `rejected_colours` (create path): a create now emits one marking per
        recognised colour, so the only homeless ones are those the catalog
        vocabulary does not know. Those are named at ANY count -- the
        color_unrecognised flag says a colour was dropped, but not which one,
        and an editor fixing it needs the name.
        """
        parts = [f"Virginia Postal History Catalog {row['town'].title()} "
                 f"#{row['cancel_no']} ({row['src']})."]
        if len(unplaced_colours) > 1:
            # Marking.color is single-valued; the rest would be lost silently.
            parts.append("Colours recorded: " + ", ".join(unplaced_colours) + ".")
        if rejected_colours:
            parts.append(
                "Colours recorded but not in the catalog vocabulary: "
                + ", ".join(rejected_colours) + "."
            )
        if row["catalog_marker"]:
            parts.append(row["catalog_marker"])
        if "type_defaulted" in flags:
            parts.append(f"[VPHC: device code {row['sheet_type'] or 'unknown'!r} "
                         f"not recognised — type defaulted to {DEFAULT_TYPE}, "
                         f"please correct]")
        return " ".join(parts)

    def _dates(self, row, payload):
        for src, prefix in (("sheet_earliest", "marking_erd"),
                            ("sheet_latest", "marking_lrd")):
            value = (row[src] or "").strip()
            if not value.isdigit():
                continue
            payload[f"{prefix}_date_year"] = int(value)
            payload[f"{prefix}_granularity"] = "YEAR"

    # ---------------------------------------------------------------- images

    def _images(self, row, totals, dry):
        """Copy the scans into MEDIA_ROOT and describe them.

        Nothing in the import path moves bytes, so this does. Purely additive:
        a file already in place is reused, never overwritten.
        """
        files = [f for f in row["sheet_image_files"].split(";") if f]
        if not files:
            return []
        state = row["state"].strip().lower() or "va"
        metas = []
        for index, name in enumerate(files):
            source = os.path.join(self.media_src, name)
            if not os.path.exists(source):
                totals["images missing on disk"] += 1
                continue
            storage = f"{state}/{name}"
            target = os.path.join(settings.MEDIA_ROOT, storage)
            if not os.path.exists(target) and not dry:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
                totals["image files copied"] += 1
            meta = read_image_metadata_from_path(Path(source))
            if meta is None:
                totals["images unreadable"] += 1
                continue
            meta = dict(meta)
            meta["storage_filename"] = storage
            meta["original_filename"] = name
            metas.append(meta)
            totals["image rows proposed"] += 1
        return metas

    # -------------------------------------------------------------- archives

    def _archive(self, ledger, actor, totals, applied):
        """Recycle-bin the duplicates. Archived, never deleted."""
        for line in ledger:
            if line["action"] != "archive":
                continue
            code = line["target_code"]
            marking = Marking.all_objects.filter(code=code).first()
            if marking is None:
                totals["archives skipped (marking not found)"] += 1
                continue
            if MarkingRecycleBin.objects.filter(marking=marking).exists():
                totals["archives skipped (already archived)"] += 1
                continue
            reason = (f"Duplicate of {line['after'].get('duplicate_of')} — "
                      f"{line['after'].get('reason')}")
            log_marking_removed(marking, actor, reason)
            MarkingRecycleBin.objects.create(
                marking=marking, removed_by=actor, reason=reason)
            totals["markings archived"] += 1
            applied.append({**line, "applied": True, "mode": "applied"})

    # ---------------------------------------------------------------- ledger

    def _write_ledger(self, vphc, applied):
        """README.md: no write happens that is not first a ledger line. The
        reviewed proposal is appended verbatim, with applied flipped true."""
        path = os.path.join(vphc, "LEDGER.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for line in applied:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
