import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Case, IntegerField, Q, When
from django.utils import timezone

from common.marking_resources import (
    MARKING_BACKUP_SCHEMA,
    SUBJECT_KIND_COVER,
    SUBJECT_KIND_MARKING,
    build_polymorphic_code_lookup,
    marking_dataset_specs,
)
from common.models import (
    Citation,
    Collection,
    Color,
    Contribution,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    CoverValuation,
    CoverVersion,
    DateSeen,
    Image,
    Lettering,
    Marking,
    MarkingRecycleBin,
    MarkingVersion,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Region,
    Shape,
    SubmissionTransaction,
)


def _dataset_payload(dataset):
    return {
        "headers": list(dataset.headers or []),
        "rows": [dict(row) for row in dataset.dict],
    }


def _parse_int(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_cover_contribution_data(data):
    if not isinstance(data, dict):
        return False
    kind = str(data.get("submission_kind") or data.get("submissionKind") or "")
    return kind.strip().lower() == "cover"


def _cover_contribution_marking_id(contribution):
    data = contribution.submitted_data or {}
    if not _is_cover_contribution_data(data):
        return None
    for key in ("parent_marking_id", "marking_id", "marking"):
        marking_id = _parse_int(data.get(key))
        if marking_id is not None:
            return marking_id
    return contribution.marking_id


def _cover_contribution_cover_id(contribution):
    data = contribution.submitted_data or {}
    if not _is_cover_contribution_data(data):
        return None
    for key in (
        "edit_cover_id",
        "editCoverId",
        "cover_id",
        "materialized_cover_id",
    ):
        cover_id = _parse_int(data.get(key))
        if cover_id is not None:
            return cover_id
    return None


def _walk_region_ancestors(region_ids):
    """Return input Region PKs plus every parent Region PK."""
    expanded = set(region_ids)
    frontier = set(region_ids)
    while frontier:
        parents = set(
            Region.objects.filter(pk__in=frontier)
            .exclude(parent_region__isnull=True)
            .values_list("parent_region_id", flat=True)
        )
        new = parents - expanded
        expanded.update(new)
        frontier = new
    return expanded


def _region_topo_order(region_ids):
    """Return Region PKs with parents before children for FK import."""
    rows = Region.objects.filter(pk__in=region_ids).values("pk", "parent_region_id")
    parent_by_pk = {row["pk"]: row["parent_region_id"] for row in rows}
    children_by_parent = {}
    for pk, parent in parent_by_pk.items():
        children_by_parent.setdefault(parent, []).append(pk)

    ordered = []
    seen = set()

    def visit(pk):
        if pk in seen:
            return
        parent = parent_by_pk.get(pk)
        if parent in parent_by_pk:
            visit(parent)
        seen.add(pk)
        ordered.append(pk)
        for child in sorted(children_by_parent.get(pk, [])):
            visit(child)

    for pk in sorted(parent_by_pk):
        visit(pk)
    return ordered


class Command(BaseCommand):
    help = (
        "Export one Marking by code plus related metadata and user-data rows "
        "to a portable JSON backup. Image binaries are not copied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "code",
            help="The Marking.code value to export.",
        )
        parser.add_argument(
            "path",
            help="JSON output file path.",
        )

    def handle(self, *args, **options):
        code = (options["code"] or "").strip()
        if not code:
            raise CommandError("A non-empty marking code is required.")

        path = Path(options["path"])
        if path.exists() and path.is_dir():
            raise CommandError(f"JSON output path is a directory: {path}")

        root = (
            Marking.all_objects.select_related(
                "post_office",
                "color",
                "shape",
                "lettering",
            )
            .filter(code=code)
            .first()
        )
        if root is None:
            raise CommandError(f"No Marking found with code {code!r}.")

        scope = self._build_scope(root)
        querysets = self._build_querysets(scope)
        code_lookup = build_polymorphic_code_lookup()
        payload = {
            "schema": MARKING_BACKUP_SCHEMA,
            "generated_at": timezone.now().isoformat(),
            "root_marking_code": root.code,
            "media_policy": "metadata_only",
            "datasets": {},
        }

        for spec in marking_dataset_specs():
            resource_kwargs = {}
            if spec.polymorphic:
                resource_kwargs["code_lookup"] = code_lookup
            resource = spec.resource_class(**resource_kwargs)
            dataset = resource.export(querysets.get(spec.name))
            payload["datasets"][spec.name] = _dataset_payload(dataset)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload,
                cls=DjangoJSONEncoder,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        self.stdout.write(
            self.style.SUCCESS(f"Exported marking {root.code} backup to {path}")
        )
        self.stdout.write(
            self.style.WARNING(
                "Image rows reference storage_filename only; binary files are "
                "not included in this backup."
            )
        )

    def _build_scope(self, root):
        cover_ids = set(
            CoverMarking.objects.filter(marking=root).values_list(
                "cover_id",
                flat=True,
            )
        )
        contribution_ids = set(
            Contribution.objects.filter(marking=root).values_list("pk", flat=True)
        )
        for contribution in Contribution.objects.filter(
            submitted_data__submission_kind="cover",
        ):
            if _cover_contribution_marking_id(contribution) != root.pk:
                continue
            contribution_ids.add(contribution.pk)
            cover_id = _cover_contribution_cover_id(contribution)
            if cover_id is not None:
                cover_ids.add(cover_id)
        collection_ids = set(
            Contribution.objects.filter(pk__in=contribution_ids).values_list(
                "collection_id",
                flat=True,
            )
        )

        tx_filter = Q(marking=root) | Q(contribution_id__in=contribution_ids)
        if cover_ids:
            tx_filter |= Q(cover_id__in=cover_ids)
        submission_transaction_ids = set(
            SubmissionTransaction.objects.filter(tx_filter).values_list(
                "pk",
                flat=True,
            )
        )

        color_ids = {root.color_id}
        color_ids.update(
            Cover.all_objects.filter(pk__in=cover_ids)
            .exclude(color_id__isnull=True)
            .values_list("color_id", flat=True)
        )
        for data in Contribution.objects.filter(
            pk__in=contribution_ids,
        ).values_list("submitted_data", flat=True):
            if not isinstance(data, dict):
                continue
            color_id = _parse_int(data.get("color_id"))
            if color_id is not None:
                color_ids.add(color_id)
        shape_ids = {root.shape_id} if root.shape_id else set()
        lettering_ids = {root.lettering_id} if root.lettering_id else set()
        post_office_ids = {root.post_office_id}

        region_ids = set(
            PostOfficeRegion.objects.filter(
                post_office_id__in=post_office_ids,
            ).values_list("region_id", flat=True)
        )
        region_ids.update(
            Collection.objects.filter(pk__in=collection_ids).values_list(
                "region_id",
                flat=True,
            )
        )
        region_ids = _walk_region_ancestors(region_ids)

        polymorphic_filter = Q(subject_type=SUBJECT_KIND_MARKING, subject_id=root.pk)
        if cover_ids:
            polymorphic_filter |= Q(
                subject_type=SUBJECT_KIND_COVER,
                subject_id__in=cover_ids,
            )

        citation_qs = Citation.objects.filter(polymorphic_filter)
        reference_work_ids = set(
            citation_qs.values_list("reference_work_id", flat=True)
        )
        reference_work_ids.discard(None)

        return {
            "root_id": root.pk,
            "cover_ids": cover_ids,
            "color_ids": color_ids,
            "shape_ids": shape_ids,
            "lettering_ids": lettering_ids,
            "post_office_ids": post_office_ids,
            "region_ids": region_ids,
            "reference_work_ids": reference_work_ids,
            "collection_ids": collection_ids,
            "contribution_ids": contribution_ids,
            "submission_transaction_ids": submission_transaction_ids,
            "polymorphic_filter": polymorphic_filter,
        }

    def _build_querysets(self, scope):
        root_id = scope["root_id"]
        cover_ids = scope["cover_ids"]
        region_order = _region_topo_order(scope["region_ids"])
        if region_order:
            region_order_case = Case(
                *[
                    When(pk=pk, then=index)
                    for index, pk in enumerate(region_order)
                ],
                output_field=IntegerField(),
            )
            region_qs = Region.objects.filter(pk__in=region_order).order_by(
                region_order_case,
            )
        else:
            region_qs = Region.objects.none()

        return {
            "colors": Color.objects.filter(pk__in=scope["color_ids"]).order_by("name"),
            "shapes": Shape.objects.filter(pk__in=scope["shape_ids"]).order_by("name"),
            "letterings": Lettering.objects.filter(
                pk__in=scope["lettering_ids"],
            ).order_by("name"),
            "reference_works": ReferenceWork.objects.filter(
                pk__in=scope["reference_work_ids"],
            ).order_by("code", "title", "publication_year"),
            "regions": region_qs,
            "post_offices": PostOffice.objects.filter(
                pk__in=scope["post_office_ids"],
            ).order_by("name", "pk"),
            "post_office_regions": PostOfficeRegion.objects.filter(
                post_office_id__in=scope["post_office_ids"],
            ).order_by("post_office__name", "region__name", "pk"),
            "collections": Collection.objects.filter(
                pk__in=scope["collection_ids"],
            ).order_by("name"),
            "covers": Cover.all_objects.filter(pk__in=cover_ids).order_by("code"),
            "cover_recycle_bin": CoverRecycleBin.objects.filter(
                cover_id__in=cover_ids,
            ).order_by("cover__code"),
            "cover_valuations": CoverValuation.objects.filter(
                cover_id__in=cover_ids,
            ).order_by("cover__code", "appraisal_date", "amt", "pk"),
            "markings": Marking.all_objects.filter(pk=root_id).order_by("code"),
            "marking_recycle_bin": MarkingRecycleBin.objects.filter(
                marking_id=root_id,
            ).order_by("marking__code"),
            "contributions": Contribution.objects.filter(
                pk__in=scope["contribution_ids"],
            ).order_by("marking__code", "pk"),
            "submission_transactions": SubmissionTransaction.objects.filter(
                pk__in=scope["submission_transaction_ids"],
            ).order_by("created_at", "pk"),
            "marking_versions": MarkingVersion.objects.filter(
                marking_id=root_id,
            ).order_by("version_no", "pk"),
            "cover_versions": CoverVersion.objects.filter(
                cover_id__in=cover_ids,
            ).order_by("cover__code", "version_no", "pk"),
            "cover_markings": CoverMarking.objects.filter(
                cover_id__in=cover_ids,
                marking_id=root_id,
            ).order_by("cover__code", "marking__code", "pk"),
            "dates_seen": DateSeen.objects.filter(
                scope["polymorphic_filter"],
            ).order_by("subject_type", "subject_id", "date", "pk"),
            "images": Image.objects.filter(scope["polymorphic_filter"]).order_by(
                "subject_type",
                "subject_id",
                "display_order",
                "image_id",
            ),
            "citations": Citation.objects.filter(
                scope["polymorphic_filter"],
            ).order_by("subject_type", "subject_id", "reference_work_id", "pk"),
        }
