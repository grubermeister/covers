"""
Batch-export every Marking that carries user-origin content to per-marking
JSON backups (one backup_marking call per Marking.code).

Built for the drop/re-import refresh flow: drop_ascc_state deletes a state's
catalog tree INCLUDING the user-submitted covers, images, and contributions
hanging off its markings, and post-policy bundles no longer contain covers.
Running this before the drop (and restore_user_markings after the re-import)
is what preserves that data across a refresh.

A marking is considered to carry user content when any of these hold:
  * it has at least one CoverMarking (covers are user/legacy data and are
    never re-created by bundle import),
  * a Contribution references it, directly or through a cover-submission
    payload,
  * an editor vetted it (is_reviewed=True),
  * it has MarkingVersion edit history,
  * --uploader USERNAME is given and a MARKING-subject Image was uploaded by
    that user. Catalog-imported image rows are attributed via the bundle's
    uploaded_by column, so image provenance is data-dependent; pass the
    usernames of real people (e.g. --uploader wayne).

Markings that match but have no code cannot be exported by backup_marking;
they are reported in the manifest, never silently skipped. Covers with no
CoverMarking at all are unreachable from any per-marking backup and are
likewise reported.

A backup failure aborts the run (fail-fast: a partial backup must not be
mistaken for a complete one before a destructive drop). The restore side is
the opposite: restore_user_markings continues past failures and reports them.

Usage:
    python backend/manage.py backup_user_markings ./tools/wip/user-backups
    python backend/manage.py backup_user_markings ./tools/wip/user-backups --state VA
    python backend/manage.py backup_user_markings ./tools/wip/user-backups --list-only
"""
import json
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from common.management.commands.backup_marking import _cover_contribution_marking_id
from common.models import (
    Contribution,
    Cover,
    CoverMarking,
    Image,
    Marking,
    MarkingVersion,
    Region,
)

MANIFEST_SCHEMA = "worldcovers.user_markings_manifest.v1"
MANIFEST_FILENAME = "manifest.json"


def backup_filename(code):
    """Marking.code is editor-assigned free text; keep filenames path-safe."""
    return code.replace("/", "_") + ".json"


class Command(BaseCommand):
    help = (
        "Batch-export every marking carrying user-origin content (covers, "
        "contributions, editor review, edit history) to per-marking JSON "
        "backups via backup_marking."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "out_dir",
            help="Directory to write per-marking JSON backups + manifest into.",
        )
        parser.add_argument(
            "--state",
            action="append",
            default=[],
            dest="states",
            help="Limit to one state's markings by Region.abbrev, e.g. VA. Repeatable.",
        )
        parser.add_argument(
            "--region-code",
            action="append",
            default=[],
            dest="region_codes",
            help="Limit by exact Region.code, e.g. USA-VA1. Repeatable; combined with --state.",
        )
        parser.add_argument(
            "--uploader",
            action="append",
            default=[],
            dest="uploaders",
            help="Also include markings with images uploaded by this username. Repeatable.",
        )
        parser.add_argument(
            "--list-only",
            action="store_true",
            help="Print what would be exported; write no files.",
        )

    def handle(self, *args, **options):
        out_dir = Path(options["out_dir"])
        if out_dir.exists() and not out_dir.is_dir():
            raise CommandError(f"Output path is not a directory: {out_dir}")

        region_filter = self._region_marking_filter(
            options["states"], options["region_codes"]
        )
        markings = Marking.all_objects.filter(
            pk__in=self._collect_marking_ids(options["uploaders"])
        )
        if region_filter is not None:
            markings = markings.filter(region_filter)

        coded = list(
            markings.exclude(code__isnull=True)
            .exclude(code="")
            .order_by("code")
            .values_list("code", flat=True)
        )
        no_code_pks = list(
            markings.filter(Q(code__isnull=True) | Q(code=""))
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        unlinked_cover_pks = list(
            Cover.all_objects.exclude(
                pk__in=CoverMarking.objects.values_list("cover_id", flat=True)
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )

        self.stdout.write(
            f"Markings with user content: {len(coded)} exportable, "
            f"{len(no_code_pks)} without a code (NOT exportable)."
        )
        if no_code_pks:
            self.stdout.write(
                self.style.WARNING(
                    "Markings without a code cannot be backed up by code and "
                    f"will be LOST by a drop: pks {no_code_pks}"
                )
            )
        if unlinked_cover_pks:
            self.stdout.write(
                self.style.WARNING(
                    "Covers linked to no marking are unreachable from "
                    f"per-marking backups: pks {unlinked_cover_pks}"
                )
            )

        if options["list_only"]:
            for code in coded:
                self.stdout.write(f"  {code}")
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        for code in coded:
            path = out_dir / backup_filename(code)
            try:
                call_command("backup_marking", code, str(path), verbosity=0)
            except Exception as exc:
                raise CommandError(
                    f"Backup failed for marking {code!r} ({exc}); aborting -- "
                    "do NOT proceed with a drop on a partial backup set."
                ) from exc

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generated_at": timezone.now().isoformat(),
            "criteria": {
                "states": options["states"],
                "region_codes": options["region_codes"],
                "uploaders": options["uploaders"],
            },
            "codes": coded,
            "skipped_no_code_marking_pks": no_code_pks,
            "unlinked_cover_pks": unlinked_cover_pks,
        }
        (out_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {len(coded)} marking backups to {out_dir} "
                f"(+ {MANIFEST_FILENAME})."
            )
        )

    def _collect_marking_ids(self, uploaders):
        ids = set(CoverMarking.objects.values_list("marking_id", flat=True))
        ids.update(
            Contribution.objects.exclude(marking=None).values_list(
                "marking_id", flat=True
            )
        )
        ids.update(
            Marking.all_objects.filter(is_reviewed=True).values_list("pk", flat=True)
        )
        ids.update(MarkingVersion.objects.values_list("marking_id", flat=True))
        for contribution in Contribution.objects.filter(
            submitted_data__submission_kind="cover"
        ):
            marking_id = _cover_contribution_marking_id(contribution)
            if marking_id is not None:
                ids.add(marking_id)
        if uploaders:
            ids.update(
                Image.objects.filter(
                    subject_type=Image.SUBJECT_MARKING,
                    uploaded_by__username__in=uploaders,
                ).values_list("subject_id", flat=True)
            )
        ids.discard(None)
        return ids

    def _region_marking_filter(self, states, region_codes):
        if not states and not region_codes:
            return None
        prefixes = []
        for code in region_codes:
            region = Region.objects.filter(code=code).first()
            if region is None:
                raise CommandError(f"No Region found with code={code!r}.")
            prefixes.append(f"{region.code}-")
        for abbrev in states:
            matches = list(
                Region.objects.filter(abbrev__iexact=abbrev.strip()).order_by("code")
            )
            if not matches:
                raise CommandError(f"No Region found with abbrev={abbrev!r}.")
            if len(matches) > 1:
                codes = ", ".join(r.code or f"pk={r.pk}" for r in matches)
                raise CommandError(
                    f"Region abbrev {abbrev!r} is ambiguous ({codes}); "
                    "use --region-code."
                )
            if not matches[0].code:
                raise CommandError(
                    f"Region pk={matches[0].pk} has no code; cannot scope by prefix."
                )
            prefixes.append(f"{matches[0].code}-")
        region_q = Q()
        for prefix in prefixes:
            region_q |= Q(post_office__code__startswith=prefix)
        return region_q
