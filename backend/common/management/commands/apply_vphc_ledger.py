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
                  open(os.path.join(vphc, "crossexam", "ledger", "proposed.jsonl"),
                       encoding="utf-8")]
        crosswalk = read_csv(os.path.join(vphc, "crossexam", "crosswalk.csv"))
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
        self.stdout.write(self.style.SUCCESS(
            ("[DRY RUN] " if dry else "") + "VPHC ledger applied."))

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
        # EDITS ARE DELIBERATELY UNTOUCHED, and "untouched" is stricter than it
        # looks. Rule I3 already paired them one-per-colour against live rows,
        # so an edit targets exactly one existing marking and stays exactly one
        # contribution -- but it must also keep the *same* colour it had.
        # `recognised[0]` is not the same as the old `colours[0] if recognised`:
        # where the sheet's FIRST colour is one the vocabulary rejects, the old
        # code sent no colour at all and the edit branch below backfilled the
        # marking's own, whereas picking the first recognised colour instead
        # sends a different one and repaints a live marking on approval.
        # Measured over the real crosswalk: 3 of the 310 edits changed, one of
        # them RED -> BLACK. All 310 are already queued and verified on
        # woco.dev; they must come out of this byte-identical so they do not
        # need re-emitting a second time.
        if edit_code:
            sheet_colour = colours[0] if colours else None
            if sheet_colour and sheet_colour.upper() not in known:
                flags.append("color_unrecognised")
                totals["colours not in the catalog vocabulary"] += 1
                sheet_colour = None
            colour_variants = [sheet_colour]
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
        payload = {
            "submission_kind": "marking",
            "type": marking_type,
            "state": state,
            "town": row["town"].strip().title(),
            "inscription_txt": inscription,
            "is_manuscript": False,
            "is_irreg": False,
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
        if dec(row["sheet_width"]):
            payload["width_mm"] = dec(row["sheet_width"])
        if dec(row["sheet_height"]):
            payload["height_mm"] = dec(row["sheet_height"])
        if row["sheet_shape"].strip():
            payload["shape"] = row["sheet_shape"].strip()
        if colour:
            payload["color"] = colour
        if marking_type == "RATEMARK":
            rate = re.search(r"\d+", row["cancel_no"] or "")
            if inscription.isdigit():
                payload["rate_val"] = inscription
            elif rate:
                payload["rate_val"] = rate.group(0)
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
