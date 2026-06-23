###################################################################################################
## WoCo Commons - Resource classes for single-Marking backup/restore
##
## Mirrors the auth_resources.py pattern (Resource classes + DatasetSpec + JSON
## envelope) but rooted at one Marking. Restoring is idempotent: every dataset
## upserts on natural keys (name, code, transaction_uuid, etc.) rather than
## auto-increment PKs, so a backup file is portable across environments where
## the PK space differs.
##
## NOTE on timestamps: TimestampedModel fields and auto_now_add columns are
## re-stamped on restore. The audit trail in MarkingVersion.snapshot and
## SubmissionTransaction.before_payload / after_payload preserves the original
## state contents; only the row's created_at / modified_at on the destination
## reflect the restore time, not the source time.
###################################################################################################
import json
import uuid

from django.contrib.auth import get_user_model
from django.db.models import CharField as DjangoCharField, TextField as DjangoTextField
from django.utils import timezone

from import_export import resources, fields
from import_export.widgets import (
    ForeignKeyWidget,
    JSONWidget,
    Widget,
)

from common.admin import IsoDateTimeWidget, NullableCharWidget
from common.auth_resources import (
    AuthDatasetSpec,
    ExportOnlyIdMixin,
    TimestampedRestoreMixin,
    _fallback_audit_user,
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


User = get_user_model()
MARKING_BACKUP_SCHEMA = "worldcovers.marking_backup.v1"

# Special sentinel string used in subject_kind to distinguish from blank.
SUBJECT_KIND_COVER = "COVER"
SUBJECT_KIND_MARKING = "MARKING"


###################################################################################################
## Widgets
###################################################################################################


class UsernameForeignKeyWidget(ForeignKeyWidget):
    """User FK by username. Returns None on miss (TimestampedRestoreMixin or
    a dedicated before_save hook will substitute the audit user where the FK
    is PROTECT)."""

    def __init__(self, **kwargs):
        super().__init__(User, "username", **kwargs)

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            value = str(value)
        try:
            return super().clean(value, row=row, **kwargs)
        except User.DoesNotExist:
            return None


class AllObjectsForeignKeyWidget(ForeignKeyWidget):
    """ForeignKeyWidget that consults model.all_objects so recycle-binned
    Markings / Covers are visible to FK resolution on import."""

    def get_queryset(self, value, row, *args, **kwargs):
        return self.model.all_objects.all()


class UuidForeignKeyWidget(ForeignKeyWidget):
    """ForeignKeyWidget that accepts both str and uuid.UUID."""

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        if isinstance(value, uuid.UUID):
            value = str(value)
        try:
            return super().clean(value, row=row, **kwargs)
        except self.model.DoesNotExist:
            return None


class ContributionMarkingCodeWidget(Widget):
    """Contribution FK widget keyed by the approved Marking.code.

    The command imports Contribution rows before SubmissionTransaction rows, so
    a transaction can resolve its contribution from the portable marking code.
    """

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        code = str(value).strip()
        if not code:
            return None
        return Contribution.objects.filter(marking__code=code).first()

    def render(self, value, obj=None, **kwargs):
        if value is None or getattr(value, "marking_id", None) is None:
            return ""
        marking = getattr(value, "marking", None)
        return getattr(marking, "code", "") or ""


class UuidWidget(Widget):
    """UUIDField widget. Accepts str or UUID, renders as str."""

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    def render(self, value, obj=None, **kwargs):
        if value is None:
            return ""
        return str(value)


class NullableJSONWidget(JSONWidget):
    """JSONWidget that tolerates already-parsed dict/list values (the JSON
    payload loader hands us native Python objects, not strings)."""

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        if isinstance(value, (dict, list)):
            return value
        return super().clean(value, row=row, **kwargs)

    def render(self, value, obj=None, **kwargs):
        if value is None or value == "" or value == {} or value == []:
            return value
        return value


class RegionNaturalKeyWidget(Widget):
    """JSON-encoded natural key for a Region: {name, abbrev, tier}.

    Region.name is NOT unique on its own; abbrev + tier disambiguates the
    common cases. Two regions sharing all three are rare and will collapse
    -- documented in marking_resources.py and the backup_marking docstring.
    """

    def clean(self, value, row=None, **kwargs):
        if value in (None, "", {}):
            return None
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except json.JSONDecodeError:
                return None
        elif isinstance(value, dict):
            data = value
        else:
            return None

        name = (data.get("name") or "").strip()
        abbrev = (data.get("abbrev") or "").strip()
        tier = (data.get("tier") or "").strip()
        if not (name and tier):
            return None
        return Region.objects.filter(
            name=name,
            abbrev=abbrev,
            region_tier=tier,
        ).first()

    def render(self, value, obj=None, **kwargs):
        if value is None:
            return ""
        return json.dumps(
            {
                "name": value.name,
                "abbrev": value.abbrev,
                "tier": value.region_tier,
            },
            sort_keys=True,
        )


def encode_region_key(region):
    if region is None:
        return ""
    return json.dumps(
        {
            "name": region.name,
            "abbrev": region.abbrev,
            "tier": region.region_tier,
        },
        sort_keys=True,
    )


###################################################################################################
## Base resource
###################################################################################################


class PortableTimestampedResource(
    ExportOnlyIdMixin,
    TimestampedRestoreMixin,
    resources.ModelResource,
):
    """Base for TimestampedModel-derived resources. Audit FKs are exported by
    username (portable across environments) and timestamps as ISO 8601.
    TimestampedRestoreMixin re-stamps created/modified at restore time and
    falls back to the audit user when the original is missing."""

    created_by = fields.Field(
        column_name="created_by",
        attribute="created_by",
        widget=UsernameForeignKeyWidget(),
    )
    modified_by = fields.Field(
        column_name="modified_by",
        attribute="modified_by",
        widget=UsernameForeignKeyWidget(),
    )
    created_date = fields.Field(
        column_name="created_date",
        attribute="created_date",
        widget=IsoDateTimeWidget(),
    )
    modified_date = fields.Field(
        column_name="modified_date",
        attribute="modified_date",
        widget=IsoDateTimeWidget(),
    )

    class Meta:
        abstract = True

    @classmethod
    def widget_from_django_field(cls, f, default=Widget):
        if isinstance(f, (DjangoCharField, DjangoTextField)) and getattr(f, "null", False):
            return NullableCharWidget
        return super().widget_from_django_field(f, default=default)


###################################################################################################
## Polymorphic mixin -- subject_kind + subject_code -> subject_id
###################################################################################################


class PolymorphicPortableResource(PortableTimestampedResource):
    """Translates between (subject_type, subject_id) on the model and a
    (subject_kind, subject_code) pair on the backup row. The code is the
    Cover.code or Marking.code natural key.

    On export the resource consults `code_lookup`: {(kind, pk): code}.
    On import the resource consults `resolver`: callable(kind, code) -> pk.

    Both are passed via the resource constructor at backup/restore time so
    the resource has no global state.
    """

    subject_kind = fields.Field(
        column_name="subject_kind",
    )
    subject_code = fields.Field(
        column_name="subject_code",
    )

    class Meta(PortableTimestampedResource.Meta):
        abstract = True

    def __init__(self, code_lookup=None, resolver=None, **kwargs):
        super().__init__(**kwargs)
        self._poly_code_lookup = code_lookup or {}
        self._poly_resolver = resolver

    def dehydrate_subject_kind(self, obj):
        return obj.subject_type or ""

    def dehydrate_subject_code(self, obj):
        return self._poly_code_lookup.get((obj.subject_type, obj.subject_id), "")

    def before_import_row(self, row, **kwargs):
        kind = (row.get("subject_kind") or "").strip()
        code = (row.get("subject_code") or "").strip()
        if not kind:
            raise ValueError("Polymorphic row is missing subject_kind")
        if not code:
            raise ValueError(f"Polymorphic row ({kind}) is missing subject_code")
        if self._poly_resolver is None:
            raise RuntimeError("Polymorphic resolver not configured on resource")
        subject_id = self._poly_resolver(kind, code)
        if subject_id is None:
            raise ValueError(
                f"Cannot resolve polymorphic subject {kind}/{code} "
                "-- expected Cover.code or Marking.code in this backup"
            )
        row["subject_id"] = subject_id
        row["subject_type"] = kind
        super().before_import_row(row, **kwargs)


###################################################################################################
## Reference-table resources
###################################################################################################


class MarkingColorResource(PortableTimestampedResource):
    class Meta(PortableTimestampedResource.Meta):
        model = Color
        fields = (
            "id",
            "name",
            "hex_val",
            "pantone_code",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("name",)


class MarkingShapeResource(PortableTimestampedResource):
    class Meta(PortableTimestampedResource.Meta):
        model = Shape
        fields = (
            "id",
            "name",
            "code",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("name",)


class MarkingLetteringResource(PortableTimestampedResource):
    class Meta(PortableTimestampedResource.Meta):
        model = Lettering
        fields = (
            "id",
            "name",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("name",)


class MarkingReferenceWorkResource(PortableTimestampedResource):
    """ReferenceWork.code is unique but NULLABLE. Use the triple
    (code, title, publication_year) as the natural key -- when code is null,
    title+year still uniquely identify the work in practice."""

    class Meta(PortableTimestampedResource.Meta):
        model = ReferenceWork
        fields = (
            "id",
            "code",
            "title",
            "authorship",
            "publisher",
            "publication_year",
            "edition",
            "volume",
            "isbn",
            "url",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("code", "title", "publication_year")


class MarkingCollectionResource(
    TimestampedRestoreMixin,
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    """Collection sidecar keyed by Region for single-marking restore.

    Collection.name is editor configuration, not a stable catalog key. Old
    backups can contain stale names, so restoring by region keeps editor
    dashboard visibility on the destination's local Collection.
    """

    region = fields.Field(
        column_name="region",
        attribute="region",
        widget=ForeignKeyWidget(Region, "name"),
    )

    class Meta:
        model = Collection
        fields = ("id", "name", "description", "region", "is_active")
        import_id_fields = ("region",)

    def before_save_instance(self, instance, row, **kwargs):
        if instance.pk:
            existing = Collection.objects.only(
                "name",
                "description",
                "is_active",
            ).get(pk=instance.pk)
            instance.name = existing.name
            instance.description = existing.description
            instance.is_active = existing.is_active
        super().before_save_instance(instance, row, **kwargs)


###################################################################################################
## Region + PostOffice (special natural-key handling)
###################################################################################################


class MarkingRegionResource(PortableTimestampedResource):
    """Region resource using (name, abbrev, region_tier) as the natural key.
    The parent_region FK is serialized as a JSON-encoded natural-key string."""

    parent_region = fields.Field(
        column_name="parent_region",
        attribute="parent_region",
        widget=RegionNaturalKeyWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Region
        fields = (
            "id",
            "name",
            "abbrev",
            "region_tier",
            "established_date",
            "defunct_date",
            "parent_region",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("name", "abbrev", "region_tier")


class MarkingPostOfficeResource(PortableTimestampedResource):
    """PostOffice.name is NOT unique. We denormalize the active primary region
    (PostOffice.region property) into `primary_region_key` and override
    get_instance to match on (name, primary_region)."""

    primary_region_key = fields.Field(
        column_name="primary_region_key",
        widget=RegionNaturalKeyWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = PostOffice
        fields = (
            "id",
            "name",
            "primary_region_key",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("name",)

    def dehydrate_primary_region_key(self, obj):
        return encode_region_key(obj.region)

    def get_instance(self, instance_loader, row):
        name = (row.get("name") or "").strip()
        if not name:
            return None
        primary_region = self.fields["primary_region_key"].clean(row)
        candidates = list(PostOffice.objects.filter(name=name))
        if not candidates:
            return None
        if primary_region is None:
            return candidates[0]
        for candidate in candidates:
            if candidate.region == primary_region:
                return candidate
        return candidates[0]


class MarkingPostOfficeRegionResource(PortableTimestampedResource):
    """Junction with natural-key FKs."""

    post_office_key = fields.Field(
        column_name="post_office_key",
    )
    region_key = fields.Field(
        column_name="region_key",
        attribute="region",
        widget=RegionNaturalKeyWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = PostOfficeRegion
        fields = (
            "id",
            "post_office_key",
            "region_key",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("post_office_key", "region_key")

    def dehydrate_post_office_key(self, obj):
        return json.dumps(
            {
                "name": obj.post_office.name,
                "region_key": encode_region_key(obj.post_office.region),
            },
            sort_keys=True,
        )

    def get_instance(self, instance_loader, row):
        po_key_raw = row.get("post_office_key") or ""
        region = self.fields["region_key"].clean(row)
        if not po_key_raw or region is None:
            return None
        try:
            po_data = json.loads(po_key_raw) if isinstance(po_key_raw, str) else po_key_raw
        except json.JSONDecodeError:
            return None
        name = (po_data.get("name") or "").strip()
        if not name:
            return None
        primary_key_raw = po_data.get("region_key") or ""
        try:
            primary_data = (
                json.loads(primary_key_raw)
                if isinstance(primary_key_raw, str) and primary_key_raw
                else primary_key_raw
            )
        except json.JSONDecodeError:
            primary_data = None
        primary_region = None
        if isinstance(primary_data, dict):
            primary_region = Region.objects.filter(
                name=(primary_data.get("name") or "").strip(),
                abbrev=(primary_data.get("abbrev") or "").strip(),
                region_tier=(primary_data.get("tier") or "").strip(),
            ).first()

        candidates = PostOffice.objects.filter(name=name)
        if primary_region is not None:
            for candidate in candidates:
                if candidate.region == primary_region:
                    return PostOfficeRegion.objects.filter(
                        post_office=candidate, region=region
                    ).first()
        candidate = candidates.first()
        if candidate is None:
            return None
        return PostOfficeRegion.objects.filter(
            post_office=candidate, region=region
        ).first()

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "post_office_id", None) is None:
            po_key_raw = row.get("post_office_key") or ""
            try:
                po_data = (
                    json.loads(po_key_raw) if isinstance(po_key_raw, str) else po_key_raw
                )
            except json.JSONDecodeError:
                po_data = {}
            name = (po_data.get("name") or "").strip()
            primary_key_raw = po_data.get("region_key") or ""
            try:
                primary_data = (
                    json.loads(primary_key_raw)
                    if isinstance(primary_key_raw, str) and primary_key_raw
                    else primary_key_raw
                )
            except json.JSONDecodeError:
                primary_data = None
            primary_region = None
            if isinstance(primary_data, dict):
                primary_region = Region.objects.filter(
                    name=(primary_data.get("name") or "").strip(),
                    abbrev=(primary_data.get("abbrev") or "").strip(),
                    region_tier=(primary_data.get("tier") or "").strip(),
                ).first()
            candidates = list(PostOffice.objects.filter(name=name))
            chosen = None
            if primary_region is not None:
                for candidate in candidates:
                    if candidate.region == primary_region:
                        chosen = candidate
                        break
            if chosen is None and candidates:
                chosen = candidates[0]
            if chosen is None:
                raise ValueError(
                    f"PostOfficeRegion row references unknown PostOffice: {name!r}"
                )
            instance.post_office = chosen
        super().before_save_instance(instance, row, **kwargs)


###################################################################################################
## Cover side
###################################################################################################


class MarkingCoverResource(PortableTimestampedResource):
    color = fields.Field(
        column_name="color",
        attribute="color",
        widget=ForeignKeyWidget(Color, "name"),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Cover
        fields = (
            "id",
            "code",
            "color",
            "type",
            "has_adhesive",
            "height",
            "is_institutional",
            "width",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("code",)

    def get_queryset(self):
        return Cover.all_objects.all()

    def before_save_instance(self, instance, row, **kwargs):
        if not (instance.code or "").strip():
            raise ValueError(
                "Cover row in backup is missing a code; refusing to import "
                "because Cover.save() would mint a fresh C-{pk} value and "
                "lose the natural key."
            )
        super().before_save_instance(instance, row, **kwargs)


class MarkingCoverRecycleBinResource(
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    """Soft-delete sidecar for Cover. Has its own removed_at (auto_now_add) and
    removed_by (PROTECT), so we substitute the audit user if the original is
    missing on the destination."""

    cover = fields.Field(
        column_name="cover_code",
        attribute="cover",
        widget=AllObjectsForeignKeyWidget(Cover, "code"),
    )
    removed_by = fields.Field(
        column_name="removed_by",
        attribute="removed_by",
        widget=UsernameForeignKeyWidget(),
    )
    removed_at = fields.Field(
        column_name="removed_at",
        attribute="removed_at",
        widget=IsoDateTimeWidget(),
        readonly=True,
    )

    class Meta:
        model = CoverRecycleBin
        fields = ("cover", "removed_by", "removed_at", "reason")
        import_id_fields = ("cover",)

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "removed_by_id", None) is None:
            fallback = _fallback_audit_user()
            if fallback is None:
                raise ValueError(
                    "Cannot restore CoverRecycleBin: removed_by user missing "
                    "and no fallback audit user exists."
                )
            instance.removed_by = fallback
        super().before_save_instance(instance, row, **kwargs)


class MarkingCoverValuationResource(PortableTimestampedResource):
    cover = fields.Field(
        column_name="cover_code",
        attribute="cover",
        widget=AllObjectsForeignKeyWidget(Cover, "code"),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = CoverValuation
        fields = (
            "id",
            "cover",
            "amt",
            "appraisal_date",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("cover", "appraisal_date", "amt")


###################################################################################################
## Marking + recycle bin
###################################################################################################


class MarkingMarkingResource(PortableTimestampedResource):
    color = fields.Field(
        column_name="color",
        attribute="color",
        widget=ForeignKeyWidget(Color, "name"),
    )
    shape = fields.Field(
        column_name="shape",
        attribute="shape",
        widget=ForeignKeyWidget(Shape, "name"),
    )
    lettering = fields.Field(
        column_name="lettering",
        attribute="lettering",
        widget=ForeignKeyWidget(Lettering, "name"),
    )
    post_office_key = fields.Field(
        column_name="post_office_key",
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Marking
        fields = (
            "id",
            "code",
            "type",
            "catalog_txt",
            "inscription_txt",
            "desc",
            "is_manuscript",
            "shape",
            "lettering",
            "color",
            "is_irreg",
            "width",
            "height",
            "date_fmt",
            "impression",
            "rate_val",
            "post_office_key",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("code",)

    def get_queryset(self):
        return Marking.all_objects.all()

    def dehydrate_post_office_key(self, obj):
        if obj.post_office_id is None:
            return ""
        return json.dumps(
            {
                "name": obj.post_office.name,
                "region_key": encode_region_key(obj.post_office.region),
            },
            sort_keys=True,
        )

    def before_save_instance(self, instance, row, **kwargs):
        if not (instance.code or "").strip():
            raise ValueError(
                "Marking row in backup has no code; cannot restore portably "
                "because Marking has no fallback minting."
            )
        if getattr(instance, "post_office_id", None) is None:
            po_key_raw = row.get("post_office_key") or ""
            try:
                po_data = (
                    json.loads(po_key_raw) if isinstance(po_key_raw, str) else po_key_raw
                )
            except json.JSONDecodeError:
                po_data = {}
            name = (po_data.get("name") or "").strip()
            primary_key_raw = po_data.get("region_key") or ""
            try:
                primary_data = (
                    json.loads(primary_key_raw)
                    if isinstance(primary_key_raw, str) and primary_key_raw
                    else primary_key_raw
                )
            except json.JSONDecodeError:
                primary_data = None
            primary_region = None
            if isinstance(primary_data, dict):
                primary_region = Region.objects.filter(
                    name=(primary_data.get("name") or "").strip(),
                    abbrev=(primary_data.get("abbrev") or "").strip(),
                    region_tier=(primary_data.get("tier") or "").strip(),
                ).first()
            candidates = list(PostOffice.objects.filter(name=name))
            chosen = None
            if primary_region is not None:
                for candidate in candidates:
                    if candidate.region == primary_region:
                        chosen = candidate
                        break
            if chosen is None and candidates:
                chosen = candidates[0]
            if chosen is None:
                raise ValueError(
                    f"Marking row references unknown PostOffice: {name!r}"
                )
            instance.post_office = chosen
        super().before_save_instance(instance, row, **kwargs)


class MarkingRecycleBinResource(
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    marking = fields.Field(
        column_name="marking_code",
        attribute="marking",
        widget=AllObjectsForeignKeyWidget(Marking, "code"),
    )
    removed_by = fields.Field(
        column_name="removed_by",
        attribute="removed_by",
        widget=UsernameForeignKeyWidget(),
    )
    removed_at = fields.Field(
        column_name="removed_at",
        attribute="removed_at",
        widget=IsoDateTimeWidget(),
        readonly=True,
    )

    class Meta:
        model = MarkingRecycleBin
        fields = ("marking", "removed_by", "removed_at", "reason")
        import_id_fields = ("marking",)

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "removed_by_id", None) is None:
            fallback = _fallback_audit_user()
            if fallback is None:
                raise ValueError(
                    "Cannot restore MarkingRecycleBin: removed_by user "
                    "missing and no fallback audit user exists."
                )
            instance.removed_by = fallback
        super().before_save_instance(instance, row, **kwargs)


###################################################################################################
## Audit trail
###################################################################################################


class MarkingSubmissionTransactionResource(
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    transaction_uuid = fields.Field(
        column_name="transaction_uuid",
        attribute="transaction_uuid",
        widget=UuidWidget(),
    )
    actor = fields.Field(
        column_name="actor",
        attribute="actor",
        widget=UsernameForeignKeyWidget(),
    )
    contribution = fields.Field(
        column_name="contribution_marking_code",
        attribute="contribution",
        widget=ContributionMarkingCodeWidget(),
    )
    marking = fields.Field(
        column_name="marking_code",
        attribute="marking",
        widget=AllObjectsForeignKeyWidget(Marking, "code"),
    )
    cover = fields.Field(
        column_name="cover_code",
        attribute="cover",
        widget=AllObjectsForeignKeyWidget(Cover, "code"),
    )
    before_payload = fields.Field(
        column_name="before_payload",
        attribute="before_payload",
        widget=NullableJSONWidget(),
    )
    after_payload = fields.Field(
        column_name="after_payload",
        attribute="after_payload",
        widget=NullableJSONWidget(),
    )
    diff_payload = fields.Field(
        column_name="diff_payload",
        attribute="diff_payload",
        widget=NullableJSONWidget(),
    )
    extra_payload = fields.Field(
        column_name="extra_payload",
        attribute="extra_payload",
        widget=NullableJSONWidget(),
    )
    created_at = fields.Field(
        column_name="created_at",
        attribute="created_at",
        widget=IsoDateTimeWidget(),
        readonly=True,
    )

    class Meta:
        model = SubmissionTransaction
        fields = (
            "id",
            "transaction_uuid",
            "actor",
            "action",
            "contribution",
            "marking",
            "cover",
            "source",
            "before_payload",
            "after_payload",
            "diff_payload",
            "extra_payload",
            "created_at",
        )
        import_id_fields = ("transaction_uuid",)

    def before_save_instance(self, instance, row, **kwargs):
        for json_attr in (
            "before_payload",
            "after_payload",
            "extra_payload",
        ):
            if getattr(instance, json_attr) is None:
                setattr(instance, json_attr, {})
        if getattr(instance, "diff_payload") is None:
            instance.diff_payload = []
        super().before_save_instance(instance, row, **kwargs)


class MarkingContributionResource(PortableTimestampedResource):
    """Contribution.marking is OneToOne -- one Contribution per Marking. We
    key on marking_code so restoring is idempotent."""

    contributor = fields.Field(
        column_name="contributor",
        attribute="contributor",
        widget=UsernameForeignKeyWidget(),
    )
    marking = fields.Field(
        column_name="marking_code",
        attribute="marking",
        widget=AllObjectsForeignKeyWidget(Marking, "code"),
    )
    collection = fields.Field(
        column_name="collection",
        attribute="collection",
        widget=ForeignKeyWidget(__import__("common.models", fromlist=["Collection"]).Collection, "name"),
    )
    reviewer = fields.Field(
        column_name="reviewer",
        attribute="reviewer",
        widget=UsernameForeignKeyWidget(),
    )
    submitted_data = fields.Field(
        column_name="submitted_data",
        attribute="submitted_data",
        widget=NullableJSONWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Contribution
        fields = (
            "id",
            "contributor",
            "marking",
            "collection",
            "submitted_data",
            "status",
            "reviewer",
            "review_notes",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("marking",)

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "submitted_data", None) is None:
            instance.submitted_data = {}
        if getattr(instance, "contributor_id", None) is None:
            fallback = _fallback_audit_user()
            if fallback is None:
                raise ValueError(
                    "Cannot restore Contribution: contributor user missing "
                    "and no fallback audit user exists."
                )
            instance.contributor = fallback
        super().before_save_instance(instance, row, **kwargs)


class MarkingVersionResource(
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    marking = fields.Field(
        column_name="marking_code",
        attribute="marking",
        widget=AllObjectsForeignKeyWidget(Marking, "code"),
    )
    transaction = fields.Field(
        column_name="transaction_uuid",
        attribute="transaction",
        widget=UuidForeignKeyWidget(SubmissionTransaction, "transaction_uuid"),
    )
    created_by = fields.Field(
        column_name="created_by",
        attribute="created_by",
        widget=UsernameForeignKeyWidget(),
    )
    snapshot = fields.Field(
        column_name="snapshot",
        attribute="snapshot",
        widget=NullableJSONWidget(),
    )
    created_at = fields.Field(
        column_name="created_at",
        attribute="created_at",
        widget=IsoDateTimeWidget(),
        readonly=True,
    )

    class Meta:
        model = MarkingVersion
        fields = (
            "id",
            "marking",
            "version_no",
            "snapshot",
            "transaction",
            "created_by",
            "created_at",
        )
        import_id_fields = ("marking", "version_no")

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "snapshot", None) is None:
            instance.snapshot = {}
        super().before_save_instance(instance, row, **kwargs)


class MarkingCoverVersionResource(
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    cover = fields.Field(
        column_name="cover_code",
        attribute="cover",
        widget=AllObjectsForeignKeyWidget(Cover, "code"),
    )
    transaction = fields.Field(
        column_name="transaction_uuid",
        attribute="transaction",
        widget=UuidForeignKeyWidget(SubmissionTransaction, "transaction_uuid"),
    )
    created_by = fields.Field(
        column_name="created_by",
        attribute="created_by",
        widget=UsernameForeignKeyWidget(),
    )
    snapshot = fields.Field(
        column_name="snapshot",
        attribute="snapshot",
        widget=NullableJSONWidget(),
    )
    created_at = fields.Field(
        column_name="created_at",
        attribute="created_at",
        widget=IsoDateTimeWidget(),
        readonly=True,
    )

    class Meta:
        model = CoverVersion
        fields = (
            "id",
            "cover",
            "version_no",
            "snapshot",
            "transaction",
            "created_by",
            "created_at",
        )
        import_id_fields = ("cover", "version_no")

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "snapshot", None) is None:
            instance.snapshot = {}
        super().before_save_instance(instance, row, **kwargs)


###################################################################################################
## CoverMarking junction (other markings on the same covers are NOT exported)
###################################################################################################


class MarkingCoverMarkingResource(PortableTimestampedResource):
    cover = fields.Field(
        column_name="cover_code",
        attribute="cover",
        widget=AllObjectsForeignKeyWidget(Cover, "code"),
    )
    marking = fields.Field(
        column_name="marking_code",
        attribute="marking",
        widget=AllObjectsForeignKeyWidget(Marking, "code"),
    )
    reviewer = fields.Field(
        column_name="reviewer",
        attribute="reviewer",
        widget=UsernameForeignKeyWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = CoverMarking
        fields = (
            "id",
            "cover",
            "marking",
            "is_backstamp",
            "placement",
            "contributor_comment",
            "review_status",
            "reviewer",
            "review_notes",
            "reviewed_at",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("cover", "marking")

    def skip_row(self, instance, original, row, import_validation_errors=None):
        if (
            instance is None
            or getattr(instance, "cover_id", None) is None
            or getattr(instance, "marking_id", None) is None
        ):
            return True
        return super().skip_row(
            instance,
            original,
            row,
            import_validation_errors=import_validation_errors,
        )


###################################################################################################
## Polymorphic (DateSeen, Image, Citation)
###################################################################################################


class MarkingDateSeenResource(PolymorphicPortableResource):
    class Meta(PortableTimestampedResource.Meta):
        model = DateSeen
        fields = (
            "id",
            "subject_kind",
            "subject_code",
            "subject_type",
            "subject_id",
            "date",
            "granularity",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = ("subject_type", "subject_id", "date", "granularity")


class MarkingImageResource(PolymorphicPortableResource):
    uploaded_by = fields.Field(
        column_name="uploaded_by",
        attribute="uploaded_by",
        widget=UsernameForeignKeyWidget(),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Image
        fields = (
            "image_id",
            "subject_kind",
            "subject_code",
            "subject_type",
            "subject_id",
            "original_filename",
            "storage_filename",
            "file_checksum",
            "mime_type",
            "image_width",
            "image_height",
            "file_size_bytes",
            "image_view",
            "image_description",
            "is_tracing",
            "display_order",
            "uploaded_by",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = (
            "subject_type",
            "subject_id",
            "file_checksum",
            "image_view",
            "display_order",
        )

    def before_save_instance(self, instance, row, **kwargs):
        if getattr(instance, "uploaded_by_id", None) is None:
            fallback = _fallback_audit_user()
            if fallback is None:
                raise ValueError(
                    "Cannot restore Image: uploaded_by user missing and "
                    "no fallback audit user exists."
                )
            instance.uploaded_by = fallback
        super().before_save_instance(instance, row, **kwargs)


class MarkingCitationResource(PolymorphicPortableResource):
    reference_work = fields.Field(
        column_name="reference_work_key",
        attribute="reference_work",
        widget=ForeignKeyWidget(ReferenceWork, "code"),
    )

    class Meta(PortableTimestampedResource.Meta):
        model = Citation
        fields = (
            "id",
            "reference_work",
            "subject_kind",
            "subject_code",
            "subject_type",
            "subject_id",
            "citation_detail",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
        )
        import_id_fields = (
            "subject_type",
            "subject_id",
            "reference_work",
            "citation_detail",
        )


###################################################################################################
## Dataset spec
###################################################################################################


class MarkingDatasetSpec:
    """Mirror of AuthDatasetSpec for marking-rooted backups.

    `polymorphic` flags datasets that need the (kind, code) -> pk resolver
    bound at import time. `requires_audit_user_fallback` flags datasets
    whose resource needs the audit user to be guaranteed present before
    restore (PROTECT FKs that fall back to it).
    """

    def __init__(
        self,
        name,
        label,
        resource_class,
        polymorphic=False,
        required=False,
    ):
        self.name = name
        self.label = label
        self.resource_class = resource_class
        self.polymorphic = polymorphic
        self.required = required


MARKING_DATASET_SPECS = (
    MarkingDatasetSpec("colors", "colors", MarkingColorResource),
    MarkingDatasetSpec("shapes", "shapes", MarkingShapeResource),
    MarkingDatasetSpec("letterings", "letterings", MarkingLetteringResource),
    MarkingDatasetSpec(
        "reference_works",
        "reference works",
        MarkingReferenceWorkResource,
    ),
    MarkingDatasetSpec("regions", "regions", MarkingRegionResource),
    MarkingDatasetSpec(
        "post_offices",
        "post offices",
        MarkingPostOfficeResource,
    ),
    MarkingDatasetSpec(
        "post_office_regions",
        "post office regions",
        MarkingPostOfficeRegionResource,
    ),
    MarkingDatasetSpec("collections", "collections", MarkingCollectionResource),
    MarkingDatasetSpec("covers", "covers", MarkingCoverResource),
    MarkingDatasetSpec(
        "cover_recycle_bin",
        "cover recycle bin",
        MarkingCoverRecycleBinResource,
    ),
    MarkingDatasetSpec(
        "cover_valuations",
        "cover valuations",
        MarkingCoverValuationResource,
    ),
    MarkingDatasetSpec(
        "markings",
        "markings",
        MarkingMarkingResource,
        required=True,
    ),
    MarkingDatasetSpec(
        "marking_recycle_bin",
        "marking recycle bin",
        MarkingRecycleBinResource,
    ),
    MarkingDatasetSpec(
        "contributions",
        "contributions",
        MarkingContributionResource,
    ),
    MarkingDatasetSpec(
        "submission_transactions",
        "submission transactions",
        MarkingSubmissionTransactionResource,
    ),
    MarkingDatasetSpec(
        "marking_versions",
        "marking versions",
        MarkingVersionResource,
    ),
    MarkingDatasetSpec(
        "cover_versions",
        "cover versions",
        MarkingCoverVersionResource,
    ),
    MarkingDatasetSpec(
        "cover_markings",
        "cover-marking junctions",
        MarkingCoverMarkingResource,
    ),
    MarkingDatasetSpec(
        "dates_seen",
        "dates seen",
        MarkingDateSeenResource,
        polymorphic=True,
    ),
    MarkingDatasetSpec(
        "images",
        "images (metadata)",
        MarkingImageResource,
        polymorphic=True,
    ),
    MarkingDatasetSpec(
        "citations",
        "citations",
        MarkingCitationResource,
        polymorphic=True,
    ),
)


def marking_dataset_specs():
    return MARKING_DATASET_SPECS


###################################################################################################
## Polymorphic resolver helper
###################################################################################################


def build_polymorphic_resolver():
    """Build a (kind, code) -> pk lookup after Cover + Marking imports
    complete. MUST use all_objects -- a marking we just imported may have
    its recycle-bin sidecar landing in a later dataset, but the marking
    row itself is still present and addressable via all_objects."""

    covers = dict(Cover.all_objects.values_list("code", "pk"))
    markings = dict(Marking.all_objects.values_list("code", "pk"))

    def resolve(kind, code):
        kind = (kind or "").strip()
        code = (code or "").strip()
        if not kind or not code:
            return None
        if kind == SUBJECT_KIND_COVER:
            return covers.get(code)
        if kind == SUBJECT_KIND_MARKING:
            return markings.get(code)
        raise ValueError(f"Unknown polymorphic subject_kind: {kind!r}")

    return resolve


def build_polymorphic_code_lookup():
    """Build the export-side (kind, pk) -> code lookup."""

    lookup = {}
    for code, pk in Cover.all_objects.values_list("code", "pk"):
        lookup[(SUBJECT_KIND_COVER, pk)] = code
    for code, pk in Marking.all_objects.values_list("code", "pk"):
        lookup[(SUBJECT_KIND_MARKING, pk)] = code
    return lookup


###################################################################################################
