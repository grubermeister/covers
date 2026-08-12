"""
Load the VPHC reference layer: counties, post offices, postmasters, tenures.

Phase B of the VPHC ingest. This is the *reference* half -- the places and
people that markings hang off. It must run before the marking import, because a
contribution cannot name a town that has no post office and a tenure cannot
hang off one either.

Runbook:
  cwd: repo root
  command: uv run python backend/manage.py import_vphc_reference \
               --vphc-dir ../docs/vphc --actor 1 --dry-run
  expected exit code: 0

Inputs (produced by tools/vphc_extract.py and tools/vphc_crossexam.py, which
live in the workspace root, not this repo):

  extract/t1_markings.csv              town populations
  extract/t3_postmasters.csv           9,244 people, 11,544 appointment events
  crossexam/post_offices_to_create.csv the towns the catalog does not have

Everything runs in one transaction. --dry-run rolls back at the tail rather
than using per-step savepoints, for the same reason import_ascc_bundle does:
a rolled-back step cannot resolve foreign keys for the step after it.

Why this writes through the ORM instead of reusing the ASCC Resource classes:
TimestampedModelResource.skip_row treats any row matching an existing record as
a skip, never an update (common/admin.py). This command has to *update* post
offices that already exist, to give them a population and a county.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter, defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import (
    PostOffice,
    PostOfficeRegion,
    Postmaster,
    PostmasterTenure,
    Region,
)

RULES_VERSION = 2
STATE_CODES = {"VA": "USA-VA1", "WV": "USA-WV1"}


def town_key(raw):
    """R4 -- the match key: punctuation and spacing removed."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def county_code(state, county):
    slug = re.sub(r"[^A-Z0-9]", "", (county or "").upper())
    return f"{STATE_CODES[state]}-C-{slug}"[:30]


def sort_name(name):
    """Surname-first, for ordering and for spotting the same person twice.

    Deliberately crude: the last whitespace-delimited token, minus a trailing
    suffix. "Gerrard T. Conn" -> "Conn, Gerrard T.". Getting this wrong costs
    ordering, not identity -- identity is the full name string.
    """
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return (name or "").strip()
    suffixes = {"JR.", "JR", "SR.", "SR", "II", "III", "IV"}
    idx = -1
    if parts[-1].upper().rstrip(",") in suffixes and len(parts) > 2:
        idx = -2
    surname = parts[idx]
    rest = parts[:idx] + (parts[idx + 1:] if idx != -1 else [])
    return f"{surname}, {' '.join(rest)}".strip().rstrip(",")


def index_post_offices_by_state():
    """`(state abbrev, town key) -> [PostOffice]`.

    Keyed by state, never by name alone. Town names repeat across states --
    woco.dev holds an ANNA in Maryland and Illinois, an AUBURN in Alabama,
    Illinois and Michigan -- so a name-only index makes Virginia's ANNA look
    like it already exists and would hang a Virginia county off an Illinois
    post office. Invisible on a database holding only VA and WV; caught by the
    dry run against the real one.
    """
    index = defaultdict(list)
    rows = PostOfficeRegion.objects.filter(
        region__region_tier="STATE"
    ).select_related("post_office", "region")
    for link in rows:
        index[(link.region.abbrev.upper(),
               town_key(link.post_office.name))].append(link.post_office)
    return index


def resolve_office(index, state, key):
    """Find a town, preferring the state the sheet names but accepting the other.

    The source is *pre-1863 Virginia*, one jurisdiction that later became two
    states, and R3 routes each row to its modern state by county. The same town
    can therefore be routed differently by different rows -- Falls Mill's
    postmaster rows say WV while its marking rows say VA, and Sweet Springs is
    the reverse. Refusing the mismatch loses 51 real appointments.

    The fallback is deliberately limited to VA and WV. It exists because these
    two states were once one, which is not true of anywhere else, and widening
    it would reintroduce the Michigan-town bug this scoping was added to fix.
    """
    order = ([state] if state in STATE_CODES else []) + [
        s for s in STATE_CODES if s != state]
    for candidate in order:
        offices = index.get((candidate, key))
        if offices:
            return offices[0], candidate
    return None, None


def read_csv(path):
    if not os.path.exists(path):
        raise CommandError(f"missing input: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Command(BaseCommand):
    help = "Load VPHC counties, post offices, postmasters and tenures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--vphc-dir", required=True,
            help="Path to the docs/vphc directory in the workspace root")
        parser.add_argument(
            "--actor", type=int, default=1,
            help="User id recorded as created_by/modified_by (default 1)")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Roll back at the end; print what would have been written")

    # ------------------------------------------------------------------ main

    def handle(self, *args, **opts):
        vphc = opts["vphc_dir"]
        dry = opts["dry_run"]
        csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

        try:
            actor = get_user_model().objects.get(pk=opts["actor"])
        except get_user_model().DoesNotExist:
            raise CommandError(f"no user with id {opts['actor']}")

        t1 = read_csv(os.path.join(vphc, "extract", "t1_markings.csv"))
        t3 = read_csv(os.path.join(vphc, "extract", "t3_postmasters.csv"))
        new_pos = read_csv(
            os.path.join(vphc, "crossexam", "post_offices_to_create.csv"))

        states = {
            abbr: Region.objects.get(code=code)
            for abbr, code in STATE_CODES.items()
        }

        if dry:
            self.stdout.write(self.style.NOTICE("DRY RUN: nothing will be committed."))

        totals = Counter()
        try:
            with transaction.atomic():
                self._counties(t1, t3, states, actor, totals)
                self._post_offices(new_pos, t1, t3, states, actor, totals)
                self._postmasters(t3, actor, totals)
                self._tenures(t3, actor, totals)
                if dry:
                    transaction.set_rollback(True)
        except Exception:
            self.stdout.write(self.style.ERROR(
                "Aborted; all changes rolled back."))
            raise

        for key in sorted(totals):
            self.stdout.write(f"  {key:<34} {totals[key]:>6}")
        summary = "VPHC reference load complete."
        self.stdout.write(self.style.SUCCESS(
            ("[DRY RUN] " if dry else "") + summary))

    # -------------------------------------------------------------- counties

    def _counties(self, t1, t3, states, actor, totals):
        """141 county Regions, parented to their state.

        `region_tier='COUNTY'` has existed since the initial migration and has
        never been used; this is its first population.
        """
        pairs = set()
        for row in t1 + t3:
            county, state = row["county"].strip(), row["state"].strip()
            if county and state in STATE_CODES:
                pairs.add((state, county))

        existing = {
            r.code: r
            for r in Region.objects.filter(region_tier="COUNTY")
        }
        self.counties = {}
        for state, county in sorted(pairs):
            code = county_code(state, county)
            region = existing.get(code)
            if region is None:
                region = Region(
                    code=code, name=county.title(), abbrev=state,
                    region_tier="COUNTY", parent_region=states[state],
                    created_by=actor, modified_by=actor)
                region.save()
                totals["counties created"] += 1
            else:
                totals["counties already present"] += 1
            self.counties[(state, town_key(county))] = region

    # ---------------------------------------------------------- post offices

    def _post_offices(self, new_pos, t1, t3, states, actor, totals):
        """Create the towns the catalog lacks; give every town its county.

        Existing post offices are linked to their state only -- all 1,527 of
        them carry exactly one region row. This adds the county alongside,
        which is what makes the county tier worth having.

        Counties come from both tables. Most of the new towns (522 of 632) are
        needed only for postmasters and never appear in the marking table, so
        reading counties from markings alone leaves them county-less.
        """
        populations = {}
        counties = {}
        for row in t1 + t3:
            key = row["town_key"]
            if not key:
                continue
            if row.get("population", "").strip() and key not in populations:
                try:
                    populations[key] = int(float(row["population"]))
                except ValueError:
                    pass
            if row["county"].strip() and row["state"].strip() and key not in counties:
                counties[key] = (row["state"].strip(), row["county"].strip())

        by_key = index_post_offices_by_state()

        # New codes continue the existing dense per-region series.
        next_serial = {}
        for abbr, prefix in STATE_CODES.items():
            used = PostOffice.objects.filter(
                code__startswith=f"{prefix}-").values_list("code", flat=True)
            top = 0
            for code in used:
                tail = code.rsplit("-", 1)[-1]
                if tail.isdigit():
                    top = max(top, int(tail))
            next_serial[abbr] = top + 1

        for row in new_pos:
            state = row["state"].strip()
            if state not in STATE_CODES:
                raise CommandError(
                    f"post office {row['town']!r} has no usable state -- it should "
                    "have been quarantined by rule P1, not offered for creation")
            existing, found_in = resolve_office(by_key, state, row["town_key"])
            if existing is not None:
                totals["post offices already present"] += 1
                if found_in != state:
                    totals["...already present under the other of VA/WV"] += 1
                continue
            key = (state, row["town_key"])
            po = PostOffice(
                code=f"{STATE_CODES[state]}-{next_serial[state]}",
                name=row["town"].title(),
                population=populations.get(key),
                created_by=actor, modified_by=actor)
            po.save()
            next_serial[state] += 1
            by_key[key].append(po)
            totals["post offices created"] += 1
            self._link(po, states[state], actor, totals)

        # Populations and county links for every town this ingest touches.
        for key, (state, county) in counties.items():
            # Scoped by state: a Virginia county must never be hung off a
            # same-named town in Michigan or Illinois.
            for po in by_key.get((state, key), []):
                region = self.counties.get((state, town_key(county)))
                if region is not None:
                    self._link(po, region, actor, totals)
                pop = populations.get(key)
                if pop is not None and po.population != pop:
                    po.population = pop
                    po.modified_by = actor
                    po.save(update_fields=["population", "modified_by", "modified_date"])
                    totals["populations set"] += 1

    def _link(self, po, region, actor, totals):
        _, created = PostOfficeRegion.objects.get_or_create(
            post_office=po, region=region, valid_from=None,
            defaults={"created_by": actor, "modified_by": actor})
        if created:
            totals[f"region links created ({region.region_tier.lower()})"] += 1

    # ------------------------------------------------------------ postmasters

    def _postmasters(self, t3, actor, totals):
        names = sorted({r["postmaster"].strip() for r in t3 if r["postmaster"].strip()})
        existing = {p.name: p for p in Postmaster.objects.all()}
        self.people = dict(existing)
        for name in names:
            if name in existing:
                totals["postmasters already present"] += 1
                continue
            person = Postmaster(
                name=name, sort_name=sort_name(name),
                created_by=actor, modified_by=actor)
            person.save()
            self.people[name] = person
            totals["postmasters created"] += 1

    # ---------------------------------------------------------------- tenures

    def _tenures(self, t3, actor, totals):
        by_key = index_post_offices_by_state()

        # Preload what is already stored so a second run is a no-op rather than
        # an IntegrityError on the (office, person, date, event) constraint.
        seen = {
            (po_id, pm_id, date.isoformat() if date else None, event)
            for po_id, pm_id, date, event in PostmasterTenure.objects.values_list(
                "post_office_id", "postmaster_id", "date_appointed", "event")
        }
        totals["tenures already present"] = len(seen)
        for row in t3:
            po, found_in = resolve_office(
                by_key, row["state"].strip().upper(), row["town_key"])
            if po is None:
                totals["tenures skipped (no post office)"] += 1
                continue
            if found_in != row["state"].strip().upper():
                totals["tenures matched via the other of VA/WV"] += 1
            person = self.people.get(row["postmaster"].strip())
            date = row["appointed_date"].strip() or None
            gran = row["appointed_granularity"].strip() or None
            # The unique constraint is (office, person, date, event); the source
            # genuinely repeats rows, so collapse them here rather than letting
            # the database raise mid-transaction.
            ident = (po.pk, person.pk if person else None, date,
                     row["event"] or "unknown")
            if ident in seen:
                totals["tenures skipped (already present or duplicated)"] += 1
                continue
            seen.add(ident)
            PostmasterTenure(
                post_office=po, postmaster=person, event=row["event"] or "unknown",
                date_appointed=date, date_appointed_granularity=gran,
                source_ref=row["src"], rules_version=RULES_VERSION,
                created_by=actor, modified_by=actor).save()
            totals["tenures created"] += 1
