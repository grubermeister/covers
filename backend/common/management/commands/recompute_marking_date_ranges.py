"""
Recompute the Marking date-range cache columns (issue #59).

    ./woco recompute_marking_date_ranges --all            # repair everything
    ./woco recompute_marking_date_ranges --ids 17 42
    ./woco recompute_marking_date_ranges --all --verify   # report drift, write nothing

--verify exits 1 when any marking's stored columns differ from a fresh
computation over dates_seen + cover_markings — the source of truth always
wins, so a dirty verify is fixed by rerunning without --verify. Intended
after bulk imports/restores and as a periodic integrity check.
"""
from django.core.management.base import BaseCommand, CommandError

from common.date_range import (
    DATE_RANGE_FIELDS,
    compute_marking_date_ranges,
    refresh_marking_date_ranges,
)
from common.models import Marking


class Command(BaseCommand):
    help = "Recompute (or --verify) Marking.earliest_seen/latest_seen cache columns."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--ids", nargs="+", type=int)
        parser.add_argument("--verify", action="store_true",
                            help="Report mismatches without writing; exit 1 if any.")
        parser.add_argument("--batch-size", type=int, default=5000)

    def handle(self, *args, **opts):
        if opts["all"]:
            ids = list(Marking.all_objects.order_by("pk").values_list("pk", flat=True))
        else:
            ids = opts["ids"]

        batch = opts["batch_size"]
        if opts["verify"]:
            mismatches = 0
            for start in range(0, len(ids), batch):
                chunk = ids[start:start + batch]
                expected = compute_marking_date_ranges(chunk)
                stored = {
                    pk: values
                    for pk, *values in Marking.all_objects.filter(pk__in=chunk)
                    .values_list("pk", *DATE_RANGE_FIELDS)
                }
                for pk, (e, eg, l, lg) in expected.items():
                    if pk in stored and tuple(stored[pk]) != (e, eg, l, lg):
                        mismatches += 1
                        if mismatches <= 20:
                            self.stderr.write(
                                f"marking {pk}: stored={tuple(stored[pk])} "
                                f"expected={(e, eg, l, lg)}"
                            )
            if mismatches:
                raise CommandError(f"{mismatches} marking(s) have stale date-range columns.")
            self.stdout.write(self.style.SUCCESS(f"Verified {len(ids)} markings: all fresh."))
            return

        written = 0
        for start in range(0, len(ids), batch):
            written += refresh_marking_date_ranges(ids[start:start + batch])
        self.stdout.write(self.style.SUCCESS(f"Recomputed {written} markings."))
