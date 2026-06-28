###################################################################################################
## WoCo Commons - Admin Panel Configuration
## Phase 1 model rewrite -- unified Marking, polymorphic Image, Cover* shape.
###################################################################################################
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from allauth.account.models import EmailAddress
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.html import format_html
from django.core.paginator import Paginator
from django.utils.functional import cached_property
from django import forms
from django.contrib import messages

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import CharWidget, ForeignKeyWidget, Widget
from django.db.models import CharField as DjangoCharField, TextField as DjangoTextField
from django.utils.dateparse import parse_datetime
from django.core.exceptions import ValidationError

from common.auth_resources import (
    CollectionAssignmentResource as AuthCollectionAssignmentResource,
    CollectionResource as AuthCollectionResource,
)


class IsoDateTimeWidget(Widget):
    """Accepts ISO 8601 datetimes (with or without microseconds / tz offset) on import,
    and renders ISO format on export."""

    def clean(self, value, row=None, *args, **kwargs):
        if value in (None, ""):
            return None
        if hasattr(value, "isoformat"):
            return value
        parsed = parse_datetime(str(value).strip())
        if parsed is None:
            raise ValueError(f"Could not parse datetime: {value!r}")
        return parsed

    def render(self, value, obj=None, **kwargs):
        if value is None:
            return ""
        return value.isoformat()


class NullableCharWidget(CharWidget):
    """CharWidget that maps empty/whitespace input to None instead of ''.

    Used automatically by TimestampedModelResource for every nullable
    CharField/TextField in the model (see widget_from_django_field
    override). Reasons we want this everywhere by default:

      - unique=True columns: MySQL UNIQUE allows multiple NULLs but
        rejects multiple ''. A blank CSV cell on row 2 would otherwise
        collide with row 1.
      - choices columns with CHECK constraints (e.g. Marking.date_fmt,
        Marking.impression): '' is not a valid choice; NULL is allowed.
      - data hygiene: null/blank fields stay null rather than carrying
        a sentinel '' that downstream code has to special-case.
    """

    def clean(self, value, row=None, **kwargs):
        if value is None:
            return None
        s = str(value).strip()
        if s == "":
            return None
        return s


from reversion_compare.admin import CompareVersionAdmin

from .models import (
    Color,
    Marking,
    MarkingType,
    MarkingVersion,
    Image,
    Postcover,
    DateSeen,
    CoverValuation,
    CoverMarking,
    Region,
    PostOffice,
    PostOfficeRegion,
    ReferenceWork,
    Shape,
    Cover,
    Lettering,
    Citation,
    Collection,
    CollectionAssignment,
    Contribution,
    FAQEntry,
)

User = get_user_model()


def _collection_assignment_table_available() -> bool:
    """True if the CollectionAssignments table exists."""
    try:
        CollectionAssignment.objects.only("pk").first()
        return True
    except Exception:
        return False


# ========== BASE ABSTRACT MODELS ==========

class NoCountPaginator(Paginator):
    @cached_property
    def count(self):
        return 10_000_000


class ReversionImportExportAdmin(CompareVersionAdmin, ImportExportModelAdmin):
    """Base admin for models that already use ImportExportModelAdmin and want reversion."""
    pass


class TimestampedModelAdmin(ReversionImportExportAdmin):
    """Base admin for models using TimestampedModel"""
    readonly_fields = ['created_by', 'created_date', 'modified_by', 'modified_date']
    show_full_result_count = False
    list_per_page = 50
    list_max_show_all = 200

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.modified_by = request.user
        super().save_model(request, obj, form, change)


class InlineRevisionMixin:
    """Populate created_by / modified_by on inline objects."""

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if hasattr(obj, "created_by") and getattr(obj, "created_by_id", None) is None:
                obj.created_by = request.user
            if hasattr(obj, "modified_by"):
                obj.modified_by = request.user
        formset.save_m2m()


class TimestampedModelResource(resources.ModelResource):
    """Base resource that handles user foreign keys properly.

    Also installs NullableCharWidget for every nullable CharField/TextField
    on the model, so blank CSV cells write SQL NULL instead of ''. This is
    important for unique=True columns and CharField(choices=...) columns
    with CHECK constraints, where '' is treated as a duplicate or invalid
    value.
    """
    created_by = fields.Field(
        column_name='created_by',
        attribute='created_by',
        widget=ForeignKeyWidget(User, 'id'),
    )
    modified_by = fields.Field(
        column_name='modified_by',
        attribute='modified_by',
        widget=ForeignKeyWidget(User, 'id'),
    )
    created_date = fields.Field(
        column_name='created_date',
        attribute='created_date',
        widget=IsoDateTimeWidget(),
    )
    modified_date = fields.Field(
        column_name='modified_date',
        attribute='modified_date',
        widget=IsoDateTimeWidget(),
    )

    class Meta:
        abstract = True
        skip_unchanged = True

    def skip_row(self, instance, original, row, import_validation_errors=None):
        if instance.pk is not None and original is not None:
            return True
        return super().skip_row(instance, original, row, import_validation_errors)

    @classmethod
    def widget_from_django_field(cls, f, default=Widget):
        # For every nullable CharField / TextField on the model, swap the
        # default CharWidget for NullableCharWidget so blank CSV cells map
        # to None (and on save -> NULL) rather than ''. Subclasses that
        # declare an explicit fields.Field(...) override are unaffected
        # (this hook only runs for model-derived fields).
        if isinstance(f, (DjangoCharField, DjangoTextField)) and getattr(f, 'null', False):
            return NullableCharWidget
        return super().widget_from_django_field(f, default=default)


class PolymorphicSubjectResourceMixin:
    """Resolve CSV subject_id codes to DB PKs before import-export matching."""

    def before_import(self, dataset, **kwargs):
        super().before_import(dataset, **kwargs)
        self._marking_pk_by_code = dict(
            Marking.all_objects.exclude(code__isnull=True).values_list('code', 'pk')
        )
        self._cover_pk_by_code = dict(
            Cover.all_objects.exclude(code__isnull=True).values_list('code', 'pk')
        )

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        subject_type = row.get('subject_type')
        subject_code = row.get('subject_id')
        if subject_code in (None, ''):
            return
        if subject_type == 'MARKING':
            try:
                row['subject_id'] = str(self._marking_pk_by_code[subject_code])
            except KeyError as exc:
                raise ValueError(f"Unknown MARKING subject code: {subject_code}") from exc
        elif subject_type == 'COVER':
            try:
                row['subject_id'] = str(self._cover_pk_by_code[subject_code])
            except KeyError as exc:
                raise ValueError(f"Unknown COVER subject code: {subject_code}") from exc


# ========== RESOURCES ==========

class ColorResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = Color
        import_id_fields = ['name']


class MarkingResource(TimestampedModelResource):
    shape = fields.Field(
        column_name='shape',
        attribute='shape',
        widget=ForeignKeyWidget(Shape, 'name'),
    )
    lettering = fields.Field(
        column_name='lettering',
        attribute='lettering',
        widget=ForeignKeyWidget(Lettering, 'name'),
    )
    color = fields.Field(
        column_name='color',
        attribute='color',
        widget=ForeignKeyWidget(Color, 'name'),
    )
    post_office = fields.Field(
        column_name='post_office',
        attribute='post_office',
        widget=ForeignKeyWidget(PostOffice, 'code'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = Marking
        import_id_fields = ['code']


class ShapeResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = Shape
        import_id_fields = ['name']


class LetteringResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = Lettering
        import_id_fields = ['name']


class RegionResource(TimestampedModelResource):
    parent_region = fields.Field(
        column_name='parent_region',
        attribute='parent_region',
        widget=ForeignKeyWidget(Region, 'code'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = Region
        import_id_fields = ['code']


class PostOfficeResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = PostOffice
        import_id_fields = ['code']


class PostOfficeRegionResource(TimestampedModelResource):
    post_office = fields.Field(
        column_name='post_office',
        attribute='post_office',
        widget=ForeignKeyWidget(PostOffice, 'code'),
    )
    region = fields.Field(
        column_name='region',
        attribute='region',
        widget=ForeignKeyWidget(Region, 'code'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = PostOfficeRegion
        import_id_fields = ['post_office', 'region']


class CoverResource(TimestampedModelResource):
    color = fields.Field(
        column_name='color',
        attribute='color',
        widget=ForeignKeyWidget(Color, 'name'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = Cover
        import_id_fields = ['code']


class DateSeenResource(PolymorphicSubjectResourceMixin, TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = DateSeen
        import_id_fields = ['subject_type', 'subject_id', 'date', 'granularity']


class CoverValuationResource(TimestampedModelResource):
    cover = fields.Field(
        column_name='cover',
        attribute='cover',
        widget=ForeignKeyWidget(Cover, 'code'),
    )

    def before_import_row(self, row, **kwargs):
        super().before_import_row(row, **kwargs)
        if row.get('appraisal_date') in (None, ''):
            raise ValidationError({'appraisal_date': 'Required for additive import.'})

    class Meta(TimestampedModelResource.Meta):
        model = CoverValuation
        import_id_fields = ['cover', 'appraisal_date']


class CoverMarkingResource(TimestampedModelResource):
    cover = fields.Field(
        column_name='cover',
        attribute='cover',
        widget=ForeignKeyWidget(Cover, 'code'),
    )
    marking = fields.Field(
        column_name='marking',
        attribute='marking',
        widget=ForeignKeyWidget(Marking, 'code'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = CoverMarking
        import_id_fields = ['cover', 'marking']


class ImageResource(PolymorphicSubjectResourceMixin, TimestampedModelResource):
    uploaded_by = fields.Field(
        column_name='uploaded_by',
        attribute='uploaded_by',
        widget=ForeignKeyWidget(User, 'id'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = Image
        import_id_fields = ['storage_filename', 'subject_type', 'subject_id']


class ReferenceWorkResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = ReferenceWork
        import_id_fields = ['code']


class CitationResource(PolymorphicSubjectResourceMixin, TimestampedModelResource):
    reference_work = fields.Field(
        column_name='reference_work',
        attribute='reference_work',
        widget=ForeignKeyWidget(ReferenceWork, 'code'),
    )

    class Meta(TimestampedModelResource.Meta):
        model = Citation
        import_id_fields = ['reference_work', 'subject_type', 'subject_id', 'citation_detail']


class CollectionAssignmentResource(resources.ModelResource):
    user = fields.Field(
        column_name='user',
        attribute='user',
        widget=ForeignKeyWidget(User, 'id'),
    )
    collection = fields.Field(
        column_name='collection',
        attribute='collection',
        widget=ForeignKeyWidget(Collection, 'id'),
    )

    class Meta:
        model = CollectionAssignment
        import_id_fields = ['id']


class CollectionResource(resources.ModelResource):
    region = fields.Field(
        column_name='region',
        attribute='region',
        widget=ForeignKeyWidget(Region, 'id'),
    )

    class Meta:
        model = Collection
        import_id_fields = ['id']


class FAQEntryResource(TimestampedModelResource):
    class Meta(TimestampedModelResource.Meta):
        model = FAQEntry
        import_id_fields = ['faq_entry_id']


# ========== PHYSICAL CHARACTERISTICS ADMIN ==========

@admin.register(Color)
class ColorAdmin(TimestampedModelAdmin):
    resource_class = ColorResource
    list_display = ['name', 'hex_val']
    search_fields = ['name', 'hex_val']
    actions = ['delete_colors_keep_listings']

    @admin.action(description='Delete selected colors (clear marking color)')
    def delete_colors_keep_listings(self, request, queryset):
        """
        Delete Color records while preserving Marking listings. Markings whose
        color FK points to a deleted color have color cleared to null.
        """
        from django.db import transaction

        total_colors = queryset.count()
        total_repointed = 0
        with transaction.atomic():
            for color in queryset:
                if color.pk == 1:
                    continue
                count = Marking.objects.filter(color=color).update(color=None)
                total_repointed += count
                color.delete()

        messages.success(
            request,
            f"Deleted {total_colors} color(s); cleared color on "
            f"{total_repointed} marking(s). All catalog listings were kept."
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


# ========== MARKING ADMIN ==========

class CoverMarkingInline(admin.TabularInline):
    """Covers attached to this Marking via CoverMarking."""
    model = CoverMarking
    extra = 0
    raw_id_fields = ['cover']
    fields = ['cover', 'is_backstamp', 'placement']
    exclude = ['created_by', 'modified_by', 'created_date', 'modified_date']


@admin.register(Marking)
class MarkingAdmin(InlineRevisionMixin, TimestampedModelAdmin):
    resource_class = MarkingResource
    list_display = ['id', 'code', 'type', 'post_office', 'color', 'shape', 'is_manuscript']
    list_filter = ['type', 'is_manuscript', 'color', 'shape']
    search_fields = ['code', 'catalog_txt', 'inscription_txt', 'desc', 'post_office__name']
    raw_id_fields = ['post_office', 'shape', 'lettering', 'color']
    inlines = [CoverMarkingInline]

    fieldsets = (
        ('Identity', {
            'fields': ('code', 'type', 'post_office'),
        }),
        ('Physical Characteristics', {
            'fields': (
                'shape', 'lettering', 'color', 'is_manuscript', 'impression',
                'is_irreg', 'width', 'height', 'date_fmt', 'rate_val',
            ),
        }),
        ('Text', {
            'fields': ('catalog_txt', 'inscription_txt', 'desc'),
        }),
        ('Metadata', {
            'fields': ('created_date', 'modified_date', 'created_by', 'modified_by'),
            'classes': ('collapse',),
        }),
    )


class CatalogRequestMarking(Marking):
    """Proxy that scopes the Marking changelist to user-contributed entries
    awaiting / under editorial review."""

    class Meta:
        proxy = True
        verbose_name = 'Catalog request'
        verbose_name_plural = 'Catalog requests'


@admin.register(CatalogRequestMarking)
class CatalogRequestMarkingAdmin(MarkingAdmin):
    """Admin view restricted to Markings linked to a Contribution row."""

    def get_queryset(self, request):
        return super().get_queryset(request).filter(contribution__isnull=False)


# ========== IMAGE ADMIN ==========

@admin.register(Image)
class ImageAdmin(TimestampedModelAdmin):
    resource_class = ImageResource
    list_display = ['image_id', 'subject_type', 'subject_id', 'original_filename', 'image_view', 'is_tracing', 'display_order', 'uploaded_by']
    list_filter = ['subject_type', 'image_view', 'is_tracing']
    search_fields = ['original_filename', 'storage_filename', 'subject_id']
    readonly_fields = ['created_by', 'created_date', 'modified_by', 'modified_date', 'file_checksum']
    paginator = NoCountPaginator

    fieldsets = (
        ('Subject', {
            'fields': ('subject_type', 'subject_id'),
        }),
        ('File Information', {
            'fields': (
                'original_filename', 'storage_filename', 'file_checksum',
                'mime_type', 'image_width', 'image_height', 'file_size_bytes',
            ),
        }),
        ('Display Settings', {
            'fields': ('image_view', 'image_description', 'is_tracing', 'display_order'),
        }),
        ('Submission Information', {
            'fields': ('uploaded_by',),
        }),
        ('Metadata', {
            'fields': ('created_date', 'modified_date', 'created_by', 'modified_by'),
            'classes': ('collapse',),
        }),
    )


# ========== COVER ADMIN ==========

class CoverValuationInline(admin.TabularInline):
    model = CoverValuation
    extra = 0
    fields = ['amt', 'appraisal_date']
    exclude = ['created_by', 'modified_by', 'created_date', 'modified_date']


class CoverMarkingForCoverInline(admin.TabularInline):
    model = CoverMarking
    extra = 0
    raw_id_fields = ['marking']
    fields = ['marking', 'is_backstamp', 'placement']
    exclude = ['created_by', 'modified_by', 'created_date', 'modified_date']


@admin.register(Cover)
class CoverAdmin(TimestampedModelAdmin):
    # DateSeen is polymorphic (subject_type/subject_id) and no longer carries
    # an FK to Cover, so it cannot be edited via TabularInline here. Manage
    # DateSeen rows through the standalone DateSeenAdmin, filtering by
    # subject_type='COVER' and the cover's PK.
    resource_class = CoverResource
    list_display = ['code', 'type', 'color', 'has_adhesive', 'height', 'width', 'is_institutional']
    list_filter = ['type', 'has_adhesive', 'is_institutional']
    search_fields = ['code', 'type']
    raw_id_fields = ['color']
    inlines = [CoverMarkingForCoverInline, CoverValuationInline]

    def has_delete_permission(self, request, obj=None):
        # Removing the unaudited hard-delete path (and its cascade to
        # valuations / cover-markings). Cover removal goes through the audited,
        # reversible recycle bin: POST /api/v2/covers/<pk>/remove/.
        return False


@admin.register(DateSeen)
class DateSeenAdmin(TimestampedModelAdmin):
    resource_class = DateSeenResource
    list_display = ['subject_type', 'subject_id', 'date', 'granularity']
    list_filter = ['granularity', 'subject_type']
    search_fields = ['subject_id']
    ordering = ['subject_type', 'subject_id', 'date']


@admin.register(CoverValuation)
class CoverValuationAdmin(TimestampedModelAdmin):
    resource_class = CoverValuationResource
    list_display = ['id', 'cover', 'amt', 'appraisal_date']
    list_filter = ['appraisal_date']
    search_fields = ['cover__code']
    raw_id_fields = ['cover']
    ordering = ['-appraisal_date']


@admin.register(CoverMarking)
class CoverMarkingAdmin(TimestampedModelAdmin):
    resource_class = CoverMarkingResource
    list_display = ['cover', 'marking', 'is_backstamp', 'placement']
    list_filter = ['is_backstamp']
    search_fields = ['cover__code', 'marking__code', 'placement']
    raw_id_fields = ['cover', 'marking']


@admin.register(MarkingVersion)
class MarkingVersionAdmin(admin.ModelAdmin):
    list_display = ['id', 'marking', 'version_no', 'created_at', 'created_by']
    list_filter = ['created_at']
    search_fields = ['marking__code']
    raw_id_fields = ['marking', 'transaction']
    ordering = ['-created_at']

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ========== POSTCOVER (DEPRECATED) ADMIN ==========

@admin.register(Postcover)
class PostcoverAdmin(InlineRevisionMixin, TimestampedModelAdmin):
    list_display = ['postcover_key', 'owner_user']
    search_fields = ['postcover_key', 'owner_user__username', 'description']
    raw_id_fields = ['owner_user']


# ========== USER ADMIN ==========

class CollectionAssignmentInline(admin.TabularInline):
    model = CollectionAssignment
    extra = 0
    raw_id_fields = ['collection']
    verbose_name = 'Collection assignment'
    verbose_name_plural = 'Collection assignments'


class EmailAddressInline(admin.TabularInline):
    model = EmailAddress
    extra = 0
    fields = ['email', 'verified', 'primary']


ROLE_CONTRIBUTOR = "contributor"
ROLE_EDITOR = "editor"

ROLE_CHOICES = (
    (ROLE_CONTRIBUTOR, "Contributor"),
    (ROLE_EDITOR, "Editor"),
)


class CollectionUserChangeForm(DjangoUserAdmin.form):
    """Manage Collection assignments via a two-column selector on the user form."""

    collections = forms.ModelMultipleChoiceField(
        label='Assigned Collections',
        queryset=Collection.objects.none(),
        required=False,
        widget=FilteredSelectMultiple('Collections', is_stacked=False),
        help_text='Collections this Editor is responsible for. Editors automatically '
                  'gain the review_contribution permission via the Editors group.',
    )

    role = forms.ChoiceField(
        label='Role',
        choices=ROLE_CHOICES,
        required=False,
        help_text='Application role: Contributor or Editor. Administrator is the '
                  'is_superuser flag below -- there is no separate Administrator group.',
    )

    class Meta(DjangoUserAdmin.form.Meta):
        _parent_fields = DjangoUserAdmin.form.Meta.fields
        if _parent_fields == '__all__':
            fields = [f.name for f in User._meta.get_fields() if f.concrete] + ['collections', 'role']
        else:
            fields = tuple(_parent_fields) + ('collections', 'role')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        collection_qs = Collection.objects.filter(is_active=True).select_related('region').order_by('name')
        self.fields['collections'].queryset = collection_qs

        if self.instance.pk and _collection_assignment_table_available():
            self.fields['collections'].initial = collection_qs.filter(
                editor_assignments__user=self.instance,
            )

        role_initial = ROLE_CONTRIBUTOR
        if self.instance.pk:
            if self.instance.groups.filter(name__iexact="Editors").exists():
                role_initial = ROLE_EDITOR
            elif (
                _collection_assignment_table_available()
                and CollectionAssignment.objects.filter(user=self.instance).exists()
            ):
                role_initial = ROLE_EDITOR
        self.fields['role'].initial = role_initial or ROLE_CONTRIBUTOR

    def _save_collections(self, request_user=None):
        if not self.instance.pk:
            return
        if not _collection_assignment_table_available():
            return

        selected_qs = self.cleaned_data.get('collections') or Collection.objects.none()
        user = self.instance
        actor = request_user or user
        selected_ids = set(selected_qs.values_list('pk', flat=True))

        CollectionAssignment.objects.filter(user=user).exclude(
            collection_id__in=selected_ids,
        ).delete()

        existing_ids = set(
            CollectionAssignment.objects.filter(user=user).values_list('collection_id', flat=True)
        )
        for pk in (selected_ids - existing_ids):
            CollectionAssignment.objects.create(
                user=user,
                collection_id=pk,
                created_by=actor,
                modified_by=actor,
            )

    def _save_role_groups(self):
        if not self.instance.pk:
            return
        role = self.cleaned_data.get("role")
        if not role:
            return

        contributors_group, _ = Group.objects.get_or_create(name="Contributors")
        editors_group, _ = Group.objects.get_or_create(name="Editors")
        user = self.instance
        user.groups.remove(contributors_group, editors_group)

        if role == ROLE_CONTRIBUTOR:
            user.groups.add(contributors_group)
        elif role == ROLE_EDITOR:
            user.groups.add(editors_group)

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role") or ROLE_CONTRIBUTOR
        collections = cleaned.get("collections")

        from django.core.exceptions import ValidationError

        if role == ROLE_CONTRIBUTOR:
            cleaned["collections"] = Collection.objects.none()
        elif role == ROLE_EDITOR:
            if not collections or not list(collections):
                self.add_error(
                    "collections",
                    ValidationError("Editors must be assigned to at least one Collection."),
                )
        return cleaned

    def save_m2m(self):
        super().save_m2m()
        self._save_collections()
        self._save_role_groups()

    class Media:
        js = ("common/admin_user_role.js",)


try:
    admin.site.unregister(User)
except NotRegistered:
    pass

try:
    admin.site.unregister(EmailAddress)
except NotRegistered:
    pass


@admin.register(User)
class CustomUserAdmin(DjangoUserAdmin):
    form = CollectionUserChangeForm
    inlines = [EmailAddressInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if hasattr(form, '_save_collections'):
            form._save_collections(request_user=request.user)
        if hasattr(form, '_save_role_groups'):
            form._save_role_groups()

    _base_fieldsets = list(DjangoUserAdmin.fieldsets)
    try:
        _important_idx = next(
            idx
            for idx, (name, options) in enumerate(_base_fieldsets)
            if name == 'Important dates'
        )
    except StopIteration:
        _base_fieldsets.append(('Role & Collections', {'fields': ('role', 'collections')}))
    else:
        _base_fieldsets.insert(_important_idx, ('Role & Collections', {'fields': ('role', 'collections')}))

    fieldsets = tuple(_base_fieldsets)


# ========== CONTRIBUTION ADMIN ==========

@admin.register(Contribution)
class ContributionAdmin(CompareVersionAdmin):
    list_display = ["id", "contributor", "status", "get_state", "get_town", "reviewer", "created_date"]
    list_filter = ["status"]
    search_fields = ["contributor__username", "submitted_data"]
    readonly_fields = ["created_date", "modified_date", "created_by", "modified_by", "marking"]
    actions = ["reject_contributions"]

    def get_state(self, obj):
        return (obj.submitted_data or {}).get("state", "-")
    get_state.short_description = "State"

    def get_town(self, obj):
        return (obj.submitted_data or {}).get("town", "-")
    get_town.short_description = "Town"

    @admin.action(description="Reject selected contributions (no catalog change)")
    def reject_contributions(self, request, queryset):
        rejected = 0
        for contrib in queryset:
            if contrib.status != Contribution.STATUS_PENDING:
                continue
            contrib.status = Contribution.STATUS_REJECTED
            contrib.reviewer = request.user
            contrib.modified_by = request.user
            contrib.save(update_fields=["status", "reviewer", "modified_date", "modified_by"])
            rejected += 1
        if rejected:
            self.message_user(
                request,
                f"Rejected {rejected} contribution(s).",
                level=messages.SUCCESS,
            )


# ========== LOOKUPS ==========

@admin.register(Lettering)
class LetteringAdmin(TimestampedModelAdmin):
    resource_class = LetteringResource
    list_display = ["name"]
    search_fields = ["name"]
    ordering = ["name"]


@admin.register(Shape)
class ShapeAdmin(TimestampedModelAdmin):
    resource_class = ShapeResource
    list_display = ["name", "code"]
    search_fields = ["name", "code"]
    ordering = ["name"]


@admin.register(Citation)
class CitationAdmin(TimestampedModelAdmin):
    resource_class = CitationResource
    list_display = ["reference_work", "subject_type", "subject_id", "citation_detail"]
    list_filter = ["subject_type"]
    search_fields = ["reference_work__title", "citation_detail"]
    ordering = ["reference_work", "subject_type", "subject_id"]


@admin.register(Region)
class RegionAdmin(TimestampedModelAdmin):
    resource_class = RegionResource
    list_display = ["code", "name", "abbrev", "region_tier", "parent_region"]
    list_filter = ["region_tier"]
    search_fields = ["code", "name", "abbrev"]
    raw_id_fields = ["parent_region"]
    ordering = ["name"]


@admin.register(PostOffice)
class PostOfficeAdmin(TimestampedModelAdmin):
    resource_class = PostOfficeResource
    list_display = ["code", "name", "region"]
    search_fields = ["code", "name", "post_office_regions__region__name"]
    ordering = ["name"]


@admin.register(PostOfficeRegion)
class PostOfficeRegionAdmin(TimestampedModelAdmin):
    resource_class = PostOfficeRegionResource
    list_display = ["post_office", "region"]
    list_filter = ["region"]
    search_fields = ["post_office__name", "region__name", "region__abbrev"]
    raw_id_fields = ["post_office", "region"]
    ordering = ["post_office__name", "region__name"]


@admin.register(ReferenceWork)
class ReferenceWorkAdmin(TimestampedModelAdmin):
    resource_class = ReferenceWorkResource
    list_display = ["code", "title", "authorship", "publication_year", "publisher"]
    search_fields = ["code", "title", "authorship", "publisher", "isbn"]
    ordering = ["title"]


@admin.register(Collection)
class CollectionAdmin(ReversionImportExportAdmin):
    resource_class = AuthCollectionResource
    list_display = ['name', 'region', 'is_active', 'created_date']
    list_filter = ['is_active']
    search_fields = ['name', 'description', 'region__name', 'region__abbrev']
    raw_id_fields = ['region']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.modified_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CollectionAssignment)
class CollectionAssignmentAdmin(ReversionImportExportAdmin):
    resource_class = AuthCollectionAssignmentResource
    list_display = ['user', 'collection', 'created_date']
    search_fields = ['user__username', 'collection__name', 'collection__region__name']
    raw_id_fields = ['user', 'collection']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.modified_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(FAQEntry)
class FAQEntryAdmin(TimestampedModelAdmin):
    resource_class = FAQEntryResource
    list_display = ("question", "is_active", "display_order")
    list_filter = ("is_active",)
    search_fields = ("question", "answer")
    ordering = ("display_order", "faq_entry_id")


###################################################################################################
