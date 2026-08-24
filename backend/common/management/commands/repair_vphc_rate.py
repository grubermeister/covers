"""Repair the rate the VPHC ingest took from the drawing number (issue #120).

Ian, 2026-08-24, on contributions 9145/9138/9137: "The rate is 3 but is showing
4 ... It is bringing in the drawing number instead of the rate."

`apply_vphc_ledger` used to read `rate_val` out of `cancel_no` whenever the
first inscription key was not a bare digit -- which is every compound rate,
because "PAID 3" reaches the applier as the sorted key set
"3/PAID;PAID 3;PAID/3" and leads with "3/PAID". `cancel_no` is the drawing
number: the marking's sequence within its town, and the key that names its scan
(rule E5). The applier is fixed; this repairs what it already emitted.

Runbook:
  cwd: repo root
  command: uv run python backend/manage.py repair_vphc_rate --dry-run
  on the box: cd /srv/woco && sudo -u wocod -H bash -lc \
                'uv run python backend/manage.py repair_vphc_rate --dry-run'
  expected exit code: 0

Deliberately NOT a re-emit. `apply_vphc_ledger` is not idempotent, so
regenerating the creates is a delete-and-rebuild of all 2,084 of them to
correct a couple of hundred -- with the eight pending human submissions and the
310 edits inside the blast radius. This edits `submitted_data` in place and
deletes nothing.

STANDING RULE, inherited from every VPHC re-emit: select on the `vphc` key,
NEVER on status. A row an editor approved since the last census is not ours to
skip silently, and a human submission is not ours to touch at all. The rows
this protects are printed before anything is written.
"""
from __future__ import annotations

import csv
import os
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import Citation, Contribution, Marking, ReferenceWork

from .apply_vphc_ledger import VPHC_REFERENCE_CODE, rate_from_inscription

RATEMARK = "RATEMARK"


def _same_rate(stored, stated):
    """Numeric equality for a DecimalField against the string a device states."""
    try:
        return Decimal(stored) == Decimal(stated)
    except (InvalidOperation, TypeError, ValueError):
        return str(stored) == str(stated)


def repaired_rate(submitted_data):
    """(old, new) for one payload, or None if this row is not in scope.

    `new` is None when the device states no rate at all -- the key is then
    removed rather than emptied, because _apply_marking_edit merges on key
    presence (issue #111): absent leaves a live marking's rate alone, "" clears
    it, and the drawing number is a wrong answer that looks like a deliberate
    one.
    """
    if not isinstance(submitted_data, dict):
        return None
    if submitted_data.get("type") != RATEMARK:
        return None
    old = submitted_data.get("rate_val")
    new = rate_from_inscription(submitted_data.get("inscription_txt", "")) or None
    if old is None and new is None:
        return None
    if str(old) == str(new):
        return None
    return old, new


class Command(BaseCommand):
    help = "Fix rate_val on VPHC contributions that took the drawing number."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually write. Omit for a dry run, which is the default.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="No-op; a dry run is already the default. Accepted because "
                 "apply_vphc_ledger spells it this way and the safe spelling "
                 "must never be the one that errors.")
        parser.add_argument(
            "--expect", type=int, default=None,
            help="Abort unless exactly this many CONTRIBUTION rows need "
                 "repair. It does not cover --audit-live, whose catalog "
                 "markings are counted and reported separately.")
        parser.add_argument(
            "--report", default="",
            help="Write the full before/after list to this CSV path.")
        parser.add_argument(
            "--audit-live", action="store_true",
            help="Also report catalog markings already carrying the drawing "
                 "number, from contributions approved before the fix.")
        parser.add_argument(
            "--sync-approved", action="store_true",
            help="Also rewrite the payload of ALREADY-APPROVED contributions "
                 "to the rate their device states. Requires --audit-live, and "
                 "refuses unless that audit comes back with zero wrong "
                 "markings -- a clean catalog is what makes the rewrite safe.")
        parser.add_argument("--actor", type=int, default=1)

    # ------------------------------------------------------------------ main

    def handle(self, *args, **opts):
        commit = opts["commit"] and not opts["dry_run"]
        if opts["commit"] and opts["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "--dry-run overrides --commit; nothing will be written."))
        if not commit:
            self.stdout.write(self.style.NOTICE(
                "DRY RUN: nothing will be written. Pass --commit to write."))
        try:
            actor = get_user_model().objects.get(pk=opts["actor"])
        except get_user_model().DoesNotExist:
            raise CommandError(f"no user with id {opts['actor']}")
        if opts["actor"] == 1:
            # 1 is the documented ingest actor, same as apply_vphc_ledger's
            # default -- but every row this touches will be attributed to it,
            # so the run log should say so rather than leave it implied.
            self.stdout.write(self.style.WARNING(
                f"actor defaulted to id 1 ({actor.get_username()}); "
                f"every write will be attributed to them."))

        rows = list(Contribution.objects.all().order_by("id"))
        ours, protected = [], []
        for row in rows:
            data = row.submitted_data
            if isinstance(data, dict) and "vphc" in data:
                ours.append(row)
            else:
                protected.append(row)

        self.stdout.write(
            f"contributions total {len(rows)} · vphc {len(ours)} · "
            f"not ours {len(protected)}")
        self.stdout.write(self.style.WARNING(
            f"PROTECTED -- {len(protected)} non-VPHC submission(s), never touched:"))
        for row in protected:
            data = row.submitted_data if isinstance(row.submitted_data, dict) else {}
            self.stdout.write(
                f"  id={row.pk:<6} status={row.status:<14} "
                f"town={data.get('town')!r}")

        planned, blocked = [], []
        for row in ours:
            change = repaired_rate(row.submitted_data)
            if change is None:
                continue
            # An approved row's payload is already in the catalog, so the
            # catalog is repaired first (--audit-live) and the payload only
            # afterwards (--sync-approved), never the other way round. Doing
            # the payload alone would leave the wrong rate live and merely
            # hide it from this report.
            if row.status == Contribution.STATUS_APPROVED:
                blocked.append((row, change))
            else:
                planned.append((row, change))

        for row, (old, new) in blocked:
            self.stdout.write(self.style.WARNING(
                f"  APPROVED: id={row.pk} rate {old!r} -> {new!r} "
                + ("-- will sync" if opts["sync_approved"]
                   else "-- skipped; see --audit-live and --sync-approved")))

        self.stdout.write(f"rows needing repair: {len(planned)} "
                          f"(plus {len(blocked)} already approved)")
        for row, (old, new) in planned:
            code = (row.submitted_data.get("vphc") or {}).get("vphc_code", "")
            self.stdout.write(
                f"  id={row.pk:<6} {code:<34} rate {old!r} -> {new!r}")

        if opts["report"]:
            self._write_report(opts["report"], planned, blocked)

        expect = opts["expect"]
        if expect is not None and expect != len(planned):
            raise CommandError(
                f"--expect {expect} but {len(planned)} rows need repair; "
                f"refusing to write. Re-census before trusting a stale number.")

        live_wrong = self._audit_live(actor, commit) if opts["audit_live"] \
            else None

        syncing = []
        if opts["sync_approved"]:
            syncing = self._approved_to_sync(
                blocked, opts["audit_live"], live_wrong)

        if not commit:
            self.stdout.write(self.style.SUCCESS("[DRY RUN] nothing written."))
            return

        with transaction.atomic():
            for row, (_old, new) in planned + syncing:
                self._rewrite_rate(row, new, actor)
        self.stdout.write(self.style.SUCCESS(
            f"repaired {len(planned)} contribution(s)."))
        if syncing:
            self.stdout.write(self.style.SUCCESS(
                f"synced {len(syncing)} approved payload(s) to the catalog."))

    def _rewrite_rate(self, row, new, actor):
        data = dict(row.submitted_data)
        if new is None:
            data.pop("rate_val", None)
        else:
            data["rate_val"] = new
        row.submitted_data = data
        row.modified_by = actor
        # Per-row save, not bulk_update: bulk_update skips save(), skips the
        # pre_save/post_save signals and does not respect auto_now, and
        # Contribution.modified_date is auto_now. At this row count the speed
        # is worth nothing and the trail is worth a lot.
        # https://docs.djangoproject.com/en/5.2/ref/models/querysets/#bulk-update
        row.save(update_fields=["submitted_data", "modified_by",
                                "modified_date"])

    def _approved_to_sync(self, blocked, audit_ran, live_wrong):
        """Which approved payloads may be rewritten, and why the rest may not.

        The safety argument is coverage, not per-row linkage. Every approved
        VPHC contribution carries a VPHC1 citation by construction, so
        --audit-live's sweep of VPHC-cited RATEMARKs examines all of them; if
        it comes back clean, the catalog already states the device's rate
        everywhere and rewriting a payload to that same rate cannot introduce
        a disagreement.

        Resolving each contribution to its own Marking would be the obvious
        alternative and it is wrong here: Contribution.marking is a
        OneToOneField that the approve view only sets when no sibling
        contribution already claims that marking, so on a colour fan-out it is
        legitimately NULL on an approved row -- and a per-row link would
        silently skip exactly the rows this exists for.
        """
        if not audit_ran:
            raise CommandError(
                "--sync-approved requires --audit-live: without it nothing has "
                "checked that the catalog states the right rate, and the "
                "payload would be rewritten on an assumption.")
        if live_wrong is None:
            raise CommandError(
                f"--sync-approved refused: --audit-live could not run (no "
                f"{VPHC_REFERENCE_CODE} reference work), so it vouches for "
                f"nothing.")
        if live_wrong:
            raise CommandError(
                f"--sync-approved refused: --audit-live still reports "
                f"{live_wrong} marking(s) carrying a wrong rate. Repair the "
                f"catalog first (--audit-live --commit); syncing payloads now "
                f"would hide the defect from this report without fixing it.")

        syncing, skipped = [], []
        for row, (old, new) in blocked:
            # No derivable rate means there is nothing --audit-live verified,
            # so there is nothing this may safely assert. Dropping the key
            # would be a guess wearing a repair's clothes.
            (skipped if new is None else syncing).append((row, (old, new)))
        for row, (old, _new) in skipped:
            self.stdout.write(self.style.WARNING(
                f"  approved id={row.pk} NOT synced: its device states no "
                f"rate, so {old!r} is not ours to overwrite"))
        self.stdout.write(
            f"approved payloads to sync: {len(syncing)}"
            + (f" ({len(skipped)} left alone)" if skipped else ""))
        return syncing

    # --------------------------------------------------------------- reports

    def _write_report(self, path, planned, blocked):
        """Never fatal. The report is a convenience; the repair is the job.

        On woco.dev this command runs as `wocod` while the operator's scratch
        directory belongs to `reese`, so an unwritable --report path is the
        normal accident, not an exotic one. Raising there aborted the run
        *before* any repair -- the report killing the work it documents.
        """
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._write_report_rows(path, planned, blocked)
        except OSError as err:
            self.stdout.write(self.style.WARNING(
                f"  could not write the report to {path}: {err}. "
                f"Continuing -- the per-row list above is the same data."))
            return
        self.stdout.write(f"  report written to {path}")

    def _write_report_rows(self, path, planned, blocked):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(["contribution_id", "status", "vphc_code",
                             "cancel_no", "inscription_txt", "old_rate",
                             "new_rate", "action"])
            for row, (old, new) in planned + blocked:
                data = row.submitted_data
                vphc = data.get("vphc") or {}
                writer.writerow([
                    row.pk, row.status, vphc.get("vphc_code", ""),
                    vphc.get("cancel_no", ""), data.get("inscription_txt", ""),
                    old, new,
                    "skipped (approved)"
                    if row.status == Contribution.STATUS_APPROVED else "repair",
                ])

    # ----------------------------------------------------- the live catalog

    def _audit_live(self, actor, commit):
        """Markings approved before the fix, still carrying a drawing number.

        The fingerprint is the bug itself: a VPHC-cited RATEMARK whose stored
        rate is not the rate its own inscription states.
        """
        try:
            reference = ReferenceWork.objects.get(code=VPHC_REFERENCE_CODE)
        except ReferenceWork.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"--audit-live: no {VPHC_REFERENCE_CODE} reference work; "
                f"nothing to audit."))
            return None  # audited nothing, so vouches for nothing

        cited = set(Citation.objects.filter(
            reference_work=reference, subject_type="MARKING",
        ).values_list("subject_id", flat=True))
        suspects = []
        for marking in Marking.objects.filter(pk__in=cited, type=RATEMARK):
            stated = rate_from_inscription(marking.inscription_txt or "")
            if not stated:
                continue
            # Compare as Decimal, not via int(). rate_val is
            # DecimalField(decimal_places=2), so int() truncates: a stored 2.50
            # would read as 2, match a stated "2", and suppress a repair that
            # should have fired.
            if marking.rate_val is not None and \
                    _same_rate(marking.rate_val, stated):
                continue
            suspects.append((marking, stated))

        self.stdout.write(
            f"--audit-live: {len(cited)} VPHC-cited marking(s), "
            f"{len(suspects)} carrying a wrong rate")
        for marking, stated in suspects:
            self.stdout.write(
                f"  marking={marking.pk:<7} {marking.code or '':<18} "
                f"{marking.inscription_txt!r} rate {marking.rate_val} "
                f"-> {stated}")

        if not (commit and suspects):
            # How many are STILL wrong when this returns -- which on a dry run
            # is all of them, because nothing was written.
            return len(suspects)
        with transaction.atomic():
            for marking, stated in suspects:
                marking.rate_val = stated
                marking.modified_by = actor
                marking.save(update_fields=["rate_val", "modified_by",
                                            "modified_date"])
        self.stdout.write(self.style.SUCCESS(
            f"--audit-live: repaired {len(suspects)} marking(s)."))
        return 0
