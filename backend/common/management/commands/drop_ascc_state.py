"""
Delete one ASCC state's imported catalog data.

Run from repo root:
    python backend/manage.py drop_ascc_state VA --dry-run

Expected exit code: 0.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import (
    Citation,
    Contribution,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    CoverValuation,
    CoverVersion,
    DateSeen,
    Image,
    Marking,
    MarkingRecycleBin,
    MarkingVersion,
    PostOffice,
    PostOfficeRegion,
    Region,
    SubmissionTransaction,
)


def _delete_queryset(label, queryset):
    count = queryset.count()
    queryset.delete()
    return label, count


class Command(BaseCommand):
    help = "Delete one ASCC state's imported catalog rows by Region code."

    def add_arguments(self, parser):
        parser.add_argument(
            "state",
            nargs="?",
            help="Two or three letter Region.abbrev, e.g. VA.",
        )
        parser.add_argument(
            "--region-code",
            default=None,
            help="Exact Region.code to drop, e.g. USA-VA1. Overrides state abbrev.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report delete counts and roll back all changes.",
        )

    def _resolve_region(self, state, region_code):
        if region_code:
            try:
                return Region.objects.get(code=region_code)
            except Region.DoesNotExist as exc:
                raise CommandError(f"No Region found with code={region_code!r}.") from exc
        if not state:
            raise CommandError("Provide a state abbrev or --region-code.")
        abbrev = state.strip().upper()
        matches = list(Region.objects.filter(abbrev__iexact=abbrev).order_by("code"))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise CommandError(f"No Region found with abbrev={abbrev!r}.")
        codes = ", ".join(region.code or f"pk={region.pk}" for region in matches)
        raise CommandError(
            f"Region abbrev {abbrev!r} is ambiguous ({codes}); use --region-code."
        )

    def handle(self, *args, **options):
        region = self._resolve_region(options.get("state"), options.get("region_code"))
        if not region.code:
            raise CommandError(f"Region pk={region.pk} has no code.")
        prefix = f"{region.code}-"
        dry_run = bool(options["dry_run"])

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: no rows will be committed."))

        post_office_ids = list(
            PostOffice.objects.filter(code__startswith=prefix).values_list("pk", flat=True)
        )
        marking_ids = list(
            Marking.all_objects.filter(post_office_id__in=post_office_ids).values_list("pk", flat=True)
        )
        cover_ids = list(
            CoverMarking.objects.filter(marking_id__in=marking_ids)
            .values_list("cover_id", flat=True)
            .distinct()
        )
        contribution_ids = list(
            Contribution.objects.filter(marking_id__in=marking_ids).values_list("pk", flat=True)
        )

        self.stdout.write(
            f"Dropping ASCC state region={region.code} post_office_prefix={prefix}"
        )

        operations = [
            (
                "images_marking",
                Image.objects.filter(subject_type=Image.SUBJECT_MARKING, subject_id__in=marking_ids),
            ),
            (
                "images_cover",
                Image.objects.filter(subject_type=Image.SUBJECT_COVER, subject_id__in=cover_ids),
            ),
            (
                "citations_marking",
                Citation.objects.filter(subject_type="MARKING", subject_id__in=marking_ids),
            ),
            (
                "citations_cover",
                Citation.objects.filter(subject_type="COVER", subject_id__in=cover_ids),
            ),
            (
                "dates_seen_marking",
                DateSeen.objects.filter(subject_type=DateSeen.SUBJECT_MARKING, subject_id__in=marking_ids),
            ),
            (
                "dates_seen_cover",
                DateSeen.objects.filter(subject_type=DateSeen.SUBJECT_COVER, subject_id__in=cover_ids),
            ),
            ("cover_valuations", CoverValuation.objects.filter(cover_id__in=cover_ids)),
            ("cover_markings", CoverMarking.objects.filter(cover_id__in=cover_ids)),
            (
                "submission_transactions",
                SubmissionTransaction.objects.filter(
                    contribution_id__in=contribution_ids
                )
                | SubmissionTransaction.objects.filter(marking_id__in=marking_ids)
                | SubmissionTransaction.objects.filter(cover_id__in=cover_ids),
            ),
            ("cover_versions", CoverVersion.objects.filter(cover_id__in=cover_ids)),
            ("cover_recycle_bin", CoverRecycleBin.objects.filter(cover_id__in=cover_ids)),
            ("covers", Cover.all_objects.filter(pk__in=cover_ids)),
            ("marking_versions", MarkingVersion.objects.filter(marking_id__in=marking_ids)),
            ("marking_recycle_bin", MarkingRecycleBin.objects.filter(marking_id__in=marking_ids)),
            ("contributions", Contribution.objects.filter(pk__in=contribution_ids)),
            ("markings", Marking.all_objects.filter(pk__in=marking_ids)),
            ("post_office_regions", PostOfficeRegion.objects.filter(post_office_id__in=post_office_ids)),
            ("post_offices", PostOffice.objects.filter(pk__in=post_office_ids)),
        ]

        counts = []
        with transaction.atomic():
            for label, queryset in operations:
                counts.append(_delete_queryset(label, queryset))
            if dry_run:
                transaction.set_rollback(True)

        for label, count in counts:
            self.stdout.write(f"  {label:<24s} deleted={count:>5d}")

        prefix_text = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix_text}Done."))
