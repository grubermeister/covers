import hashlib
import uuid
from datetime import date as date_cls
from django.db import models
from django.db.models import Q, F
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils.dateparse import parse_date
from colorfield.fields import ColorField

class TimestampedModel(models.Model):
    """Abstract base model with creation and modification tracking"""
    created_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='%(class)s_created')
    modified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='%(class)s_modified')

    class Meta:
        abstract = True

class Color(TimestampedModel):
    """
    Value table of ink or cover material colors.

    model.md domain type: Color
    Seed values: BLACK, BLUE, RED, GREEN, BROWN, ORANGE, PURPLE, MAGENTA,
    VIOLET (plus catalog compounds such as BROWN-RED and RED-ORANGE). The
    canonical color rows are supplied by the import pipeline. Marking.color may
    be null when the catalog entry does not identify a color.
    """
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, unique=True)
    hex_val = ColorField(default='#FFFFFF')
    pantone_code = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = 'Colors'
        verbose_name = 'Color'
        verbose_name_plural = 'Colors'
        ordering = ['name']

    def __str__(self):
        return self.name


class MarkingType(models.TextChoices):
    TOWNMARK = 'TOWNMARK', 'Townmark'
    RATEMARK = 'RATEMARK', 'Ratemark'
    AUXMARK = 'AUXMARK', 'Auxmark'


class MarkingQuerySet(models.QuerySet):
    # earliest_seen / latest_seen are real columns maintained by
    # common.date_range (issue #59); the former with_date_range()
    # correlated-subquery annotation is gone. The subclass remains so
    # future marking-specific queryset methods have a home.
    pass


class MarkingManager(models.Manager.from_queryset(MarkingQuerySet)):
    """
    Default manager for Marking. Hides rows that are in the recycle bin
    (i.e. that have a related MarkingRecycleBin row). A marking is "removed"
    by creating its recycle-bin sidecar row, not by mutating the Marking
    itself -- see MarkingRecycleBin. Code that must see removed markings
    (recycle-bin endpoints, restore, audit) uses Marking.all_objects.

    Keeps the MarkingQuerySet methods via from_queryset.
    """
    def get_queryset(self):
        return super().get_queryset().filter(recycle_bin_entry__isnull=True)


MARKING_DATE_FMT_CHOICES = [('MD', 'MD'), ('MDD', 'MDD'), ('YD', 'YD'), ('YMD', 'YMD'), ('YMDD', 'YMDD')]
MARKING_IMPRESSION_CHOICES = [('Normal', 'Normal'), ('Stencil', 'Stencil'), ('Negative', 'Negative')]
DATE_RANGE_GRANULARITY_CHOICES = [('DAY', 'Day'), ('MONTH', 'Month'), ('YEAR', 'Year')]


class Marking(TimestampedModel):
    """
    A unified postal marking row -- TOWNMARK, RATEMARK, or AUXMARK -- as
    observed on one or more Covers. Replaces the prior split Townmark /
    Ratemark / Auxmark tables.

    model.md domain type: markings
    """
    DATE_FMT_CHOICES = MARKING_DATE_FMT_CHOICES
    IMPRESSION_CHOICES = MARKING_IMPRESSION_CHOICES

    id = models.AutoField(primary_key=True)
    code = models.CharField(max_length=30, unique=True, null=True, blank=True, help_text='Editor-assigned reference identifier')
    type = models.CharField(max_length=8, choices=MarkingType.choices, help_text='Functional classification of this marking')
    catalog_txt = models.TextField(null=True, blank=True, help_text='Authoritative ASCC catalog entry text for this listing')
    inscription_txt = models.TextField(help_text='Text as physically inscribed on the marking')
    desc = models.TextField(null=True, blank=True, help_text='Free-text contributor annotation')
    is_manuscript = models.BooleanField()
    shape = models.ForeignKey('Shape', on_delete=models.PROTECT, null=True, blank=True, related_name='markings')
    lettering = models.ForeignKey('Lettering', on_delete=models.PROTECT, null=True, blank=True, related_name='markings')
    color = models.ForeignKey(Color, on_delete=models.PROTECT, null=True, blank=True, related_name='markings')
    is_irreg = models.BooleanField(null=True, blank=True)
    width = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text='Horizontal dimension in millimeters')
    height = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text='Vertical dimension in millimeters')
    date_fmt = models.CharField(max_length=10, choices=MARKING_DATE_FMT_CHOICES, null=True, blank=True)
    impression = models.CharField(max_length=10, choices=MARKING_IMPRESSION_CHOICES, null=True, blank=True)
    rate_val = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Non-negative rate amount; most common on RATEMARK and integrated-rate TOWNMARK rows')
    post_office = models.ForeignKey('PostOffice', on_delete=models.PROTECT, related_name='markings')
    is_reviewed = models.BooleanField(default=False, help_text='A state editor has personally vetted this record (Issue #22).')
    display_submitter_name = models.BooleanField(default=False, help_text="Whether the submitter opted in to show their name on the public marking detail page")

    # Derived cache of the use range (issue #59): min/max over the marking's
    # own DateSeen rows plus DateSeen rows of covers linked via CoverMarking,
    # with direct rows supplying the granularity on boundary-date ties.
    # dates_seen stays the source of truth; common.date_range recomputes these
    # whenever that evidence changes. Never write these fields directly.
    earliest_seen = models.DateField(null=True, blank=True, editable=False)
    earliest_seen_granularity = models.CharField(
        max_length=5, choices=DATE_RANGE_GRANULARITY_CHOICES,
        null=True, blank=True, editable=False,
    )
    latest_seen = models.DateField(null=True, blank=True, editable=False)
    latest_seen_granularity = models.CharField(
        max_length=5, choices=DATE_RANGE_GRANULARITY_CHOICES,
        null=True, blank=True, editable=False,
    )

    # objects: default manager, EXCLUDES recycle-binned markings.
    # all_objects: unfiltered, INCLUDES recycle-binned markings.
    # base_manager_name='all_objects' makes Django's related/FK access
    # (contribution.marking, cover_marking.marking, etc.) resolve via the
    # unfiltered manager so structural references never break on a removed row.
    objects = MarkingManager()
    all_objects = MarkingQuerySet.as_manager()

    class Meta:
        db_table = 'Markings'
        verbose_name = 'Marking'
        verbose_name_plural = 'Markings'
        ordering = ['id']
        base_manager_name = 'all_objects'
        indexes = [
            models.Index(fields=['earliest_seen'], name='marking_earliest_seen_idx'),
            models.Index(fields=['latest_seen'], name='marking_latest_seen_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(type__in=[c[0] for c in MarkingType.choices]),
                name='marking_type_valid',
            ),
            models.CheckConstraint(
                check=Q(date_fmt__isnull=True) | Q(date_fmt__in=[c[0] for c in MARKING_DATE_FMT_CHOICES]),
                name='marking_date_fmt_valid',
            ),
            models.CheckConstraint(
                check=Q(impression__isnull=True) | Q(impression__in=[c[0] for c in MARKING_IMPRESSION_CHOICES]),
                name='marking_impression_valid',
            ),
            models.CheckConstraint(
                check=(
                    Q(is_manuscript=True, lettering__isnull=True, shape__isnull=True, is_irreg__isnull=True)
                    | Q(is_manuscript=False, is_irreg__isnull=False)
                ),
                name='marking_manuscript_consistency',
            ),
        ]

    def clean(self):
        super().clean()
        if self.is_manuscript:
            if self.lettering_id is not None:
                raise ValidationError({'lettering': 'Must be null when is_manuscript is true.'})
            if self.shape_id is not None:
                raise ValidationError({'shape': 'Must be null when is_manuscript is true.'})
            if self.is_irreg is not None:
                raise ValidationError({'is_irreg': 'Must be null when is_manuscript is true.'})
        else:
            if self.is_irreg is None:
                self.is_irreg = False

    def save(self, *args, **kwargs):
        if self.is_manuscript:
            self.lettering = None
            self.shape = None
            self.is_irreg = None
        else:
            if self.is_irreg is None:
                self.is_irreg = False
        super().save(*args, **kwargs)

    def __str__(self):
        if self.code:
            return f'{self.type} {self.code}'
        return f'{self.type} #{self.pk}'


class Contribution(TimestampedModel):
    """
    Moderation ticket for catalog contributions.
    Submissions create a Contribution instead of directly updating the catalog.
    Editors approve/reject; on approval, submitted_data is applied to Marking.

    Inherits TimestampedModel: created_date / modified_date / created_by /
    modified_by. The `contributor` and `reviewer` FKs are workflow roles
    (who authored the submission, who reviewed it) and are distinct from
    created_by / modified_by (which user wrote which row revision).
    """
    STATUS_DRAFT = "draft"
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_NEEDS_REVISION = "needs_revision"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_NEEDS_REVISION, "Needs revision"),
    ]
    id = models.AutoField(primary_key=True)
    contributor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='contributions')
    marking = models.OneToOneField(Marking, on_delete=models.CASCADE, related_name='contribution', null=True, blank=True, help_text='Set when approved; Marking created from submitted_data for new entries')
    # Routing target: which institutional Collection this contribution belongs to.
    # Resolved at submit time from the contributor-supplied state. NOT NULL -- every
    # contribution must land in a Collection so the right Editors see it.
    collection = models.ForeignKey('Collection', on_delete=models.PROTECT, related_name='contributions')
    submitted_data = models.JSONField(default=dict, blank=True, help_text='Proposed changes (state, town, type, color, description, etc.)')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_contributions')
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'Contributions'
        verbose_name = 'Contribution'
        verbose_name_plural = 'Contributions'
        ordering = ['-created_date']
        permissions = [
            ('review_contribution', 'Can review (approve / reject) contributions'),
        ]

    def __str__(self):
        return f'Contribution #{self.id} ({self.status})'

    def apply_to_catalog(self):
        from common.contribution_apply import apply_contribution_to_catalog
        return apply_contribution_to_catalog(self)


class ContributionRecycleBin(models.Model):
    """
    Archive sidecar for Contribution (Issue #89). The presence of a row here
    means the contribution has been cleared off the *editor* review dashboard;
    the Contribution row itself is never mutated or deleted, so its status,
    review notes and audit trail stay intact and it can be restored.

    Deliberately NOT a manager override, unlike MarkingRecycleBin and
    CoverRecycleBin. Archiving is editor-side housekeeping, not deletion: the
    contributor must keep seeing their own submission and the editor's feedback
    on it. Only the editor queue (_get_editor_contribution_queryset) filters on
    this sidecar -- see the visibility notes on ContributionViewSet.get_queryset.

    Only a REVIEWED contribution can be archived (approved / rejected /
    needs_revision). A pending one has to be decided first, so nothing leaves
    the review queue without a decision.
    """
    contribution = models.OneToOneField(
        Contribution,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="recycle_bin_entry",
    )
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contributions_archived",
    )
    archived_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "contribution_recycle_bin"
        verbose_name = "Archived Contribution"
        verbose_name_plural = "Archived Contributions"
        ordering = ["-archived_at"]
        indexes = [
            models.Index(fields=["archived_at"]),
        ]

    def __str__(self):
        return f"Contribution #{self.contribution_id} archived at {self.archived_at}"


class SubmissionTransaction(models.Model):
    """Immutable audit events for submission and moderation workflows."""
    ACTION_SUBMIT = "submit"
    ACTION_EDIT_SUBMISSION = "edit_submission"
    # Retained for historical log rows; no longer written after the
    # editor inline-edit form was removed from ContributionDetail.
    ACTION_EDITOR_EDIT = "editor_edit"
    ACTION_APPROVE = "approve"
    ACTION_REJECT = "reject"
    ACTION_CATALOG_DIRECT_EDIT = "catalog_direct_edit"
    ACTION_RESTORE_VERSION = "restore_version"
    ACTION_RECORD_CREATE = "record_create"
    ACTION_RECORD_UPDATE = "record_update"
    ACTION_RECORD_DELETE = "record_delete"
    # New deletion-model actions (see MarkingRecycleBin):
    #   DRAFT_DELETED   -- a contributor/editor hard-deleted their own draft
    #   MARKING_REMOVED -- a marking was soft-removed into the recycle bin
    #   MARKING_RESTORED-- a marking was restored from the recycle bin
    ACTION_DRAFT_DELETED = "draft_deleted"
    ACTION_CONTRIBUTION_SUPERSEDED = "contribution_superseded"
    ACTION_MARKING_REMOVED = "marking_removed"
    ACTION_MARKING_RESTORED = "marking_restored"
    # Cover deletion-model actions, mirroring the marking recycle bin
    # (see CoverRecycleBin):
    #   COVER_REMOVED  -- a cover was soft-removed into the recycle bin
    #   COVER_RESTORED -- a cover was restored from the recycle bin
    ACTION_COVER_REMOVED = "cover_removed"
    ACTION_COVER_RESTORED = "cover_restored"
    # Editor dashboard housekeeping (see ContributionRecycleBin):
    #   CONTRIBUTION_ARCHIVED  -- a reviewed contribution was cleared off the queue
    #   CONTRIBUTION_RESTORED  -- ... and put back
    ACTION_CONTRIBUTION_ARCHIVED = "contribution_archived"
    ACTION_CONTRIBUTION_RESTORED = "contribution_restored"
    ACTION_CHOICES = [
        (ACTION_SUBMIT, "Record submitted"),
        (ACTION_EDIT_SUBMISSION, "Edited submission"),
        (ACTION_EDITOR_EDIT, "Editor edit"),
        (ACTION_APPROVE, "Approved"),
        (ACTION_REJECT, "Rejected"),
        (ACTION_CATALOG_DIRECT_EDIT, "Catalog direct edit"),
        (ACTION_RESTORE_VERSION, "Restored version"),
        (ACTION_RECORD_CREATE, "Record created"),
        (ACTION_RECORD_UPDATE, "Record updated"),
        (ACTION_RECORD_DELETE, "Record deleted"),
        (ACTION_DRAFT_DELETED, "Draft deleted"),
        (ACTION_CONTRIBUTION_SUPERSEDED, "Contribution superseded"),
        (ACTION_MARKING_REMOVED, "Marking removed"),
        (ACTION_MARKING_RESTORED, "Marking restored"),
        (ACTION_COVER_REMOVED, "Cover removed"),
        (ACTION_COVER_RESTORED, "Cover restored"),
        (ACTION_CONTRIBUTION_ARCHIVED, "Contribution archived"),
        (ACTION_CONTRIBUTION_RESTORED, "Contribution restored"),
    ]

    SOURCE_CONTRIBUTOR_PORTAL = "contributor_portal"
    SOURCE_EDITOR_PORTAL = "editor_portal"
    SOURCE_SYSTEM = "system"
    SOURCE_CHOICES = [
        (SOURCE_CONTRIBUTOR_PORTAL, "Contributor portal"),
        (SOURCE_EDITOR_PORTAL, "Editor portal"),
        (SOURCE_SYSTEM, "System"),
    ]

    id = models.AutoField(primary_key=True)
    transaction_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submission_transactions",
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    marking = models.ForeignKey(
        Marking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    # Cover is defined later in this module, so reference it by name. Nullable
    # like `marking`: most transactions are not cover-scoped.
    cover = models.ForeignKey(
        'Cover',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_SYSTEM)
    before_payload = models.JSONField(default=dict, blank=True)
    after_payload = models.JSONField(default=dict, blank=True)
    diff_payload = models.JSONField(default=list, blank=True)
    extra_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "SubmissionTransactions"
        verbose_name = "Submission Transaction"
        verbose_name_plural = "Submission Transactions"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["marking", "created_at"]),
            models.Index(fields=["cover", "created_at"]),
            models.Index(fields=["contribution", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"Transaction #{self.id} ({self.action})"


class MarkingVersion(models.Model):
    """Snapshot history for recoverable marking states."""
    id = models.AutoField(primary_key=True)
    marking = models.ForeignKey(Marking, on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict, blank=True)
    transaction = models.ForeignKey(
        SubmissionTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marking_versions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "MarkingVersions"
        verbose_name = "Marking Version"
        verbose_name_plural = "Marking Versions"
        ordering = ["-version_no", "-id"]
        unique_together = [["marking", "version_no"]]
        indexes = [
            models.Index(fields=["marking", "version_no"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Marking #{self.marking_id} v{self.version_no}"


class CoverVersion(models.Model):
    """Snapshot history for recoverable cover states. Mirrors MarkingVersion."""
    id = models.AutoField(primary_key=True)
    # Cover is defined later in this module, so reference it by name.
    cover = models.ForeignKey('Cover', on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict, blank=True)
    transaction = models.ForeignKey(
        SubmissionTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cover_versions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cover_versions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "CoverVersions"
        verbose_name = "Cover Version"
        verbose_name_plural = "Cover Versions"
        ordering = ["-version_no", "-id"]
        unique_together = [["cover", "version_no"]]
        indexes = [
            models.Index(fields=["cover", "version_no"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Cover #{self.cover_id} v{self.version_no}"


class MarkingRecycleBin(models.Model):
    """
    Soft-delete sidecar for Marking. The presence of a row here means the
    referenced Marking is "removed" (in the recycle bin). The Marking row
    itself never moves and is never mutated, so all FKs, versions, cover
    links, dates, images and citations stay intact -- complete history is
    preserved. Restoring a marking is just deleting its row here.

    The default Marking manager (MarkingManager) excludes any Marking that
    has a row in this table; Marking.all_objects includes them.
    """
    marking = models.OneToOneField(
        Marking,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="recycle_bin_entry",
    )
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="markings_removed",
    )
    removed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "marking_recycle_bin"
        verbose_name = "Recycle-binned Marking"
        verbose_name_plural = "Recycle-binned Markings"
        ordering = ["-removed_at"]
        indexes = [
            models.Index(fields=["removed_at"]),
        ]

    def __str__(self):
        return f"Marking #{self.marking_id} removed at {self.removed_at}"


IMAGE_MARKING_VIEW_CHOICES = ['FULL', 'DETAIL']
IMAGE_COVER_VIEW_CHOICES = ['FRONT', 'BACK', 'INTERIOR', 'DETAIL']
IMAGE_VIEW_CHOICES_TUPLES = [(v, v.title()) for v in sorted(set(IMAGE_MARKING_VIEW_CHOICES + IMAGE_COVER_VIEW_CHOICES))]


class Image(TimestampedModel):
    """
    Polymorphic image attached to either a Cover or a Marking, keyed by
    (subject_type, subject_id). Replaces the old per-subject image tables.
    """
    SUBJECT_COVER = 'COVER'
    SUBJECT_MARKING = 'MARKING'
    SUBJECT_TYPE_CHOICES = [(SUBJECT_COVER, 'Cover'), (SUBJECT_MARKING, 'Marking')]

    MARKING_VIEW_CHOICES = IMAGE_MARKING_VIEW_CHOICES
    COVER_VIEW_CHOICES = IMAGE_COVER_VIEW_CHOICES
    IMAGE_VIEW_CHOICES = IMAGE_VIEW_CHOICES_TUPLES

    image_id = models.AutoField(primary_key=True)
    subject_type = models.CharField(max_length=8, choices=SUBJECT_TYPE_CHOICES)
    subject_id = models.PositiveIntegerField()
    original_filename = models.CharField(max_length=255)
    # storage_filename alone is intentionally NOT unique: a single image file
    # on disk can be referenced by different Image rows (e.g. one per color
    # fan-out child of a parent marking in the ASCC munger output). The
    # subject-scoped uniqueness constraint below prevents duplicate rows for
    # the same file on the same subject while preserving cross-subject reuse.
    # Default destroy only removes the row, leaving the file and any sibling
    # rows intact -- see ImageViewSet.destroy and the absence of pre_delete
    # signals on this model.
    storage_filename = models.CharField(max_length=255)
    file_checksum = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=64)
    image_width = models.IntegerField()
    image_height = models.IntegerField()
    file_size_bytes = models.BigIntegerField()
    image_view = models.CharField(max_length=16, choices=IMAGE_VIEW_CHOICES_TUPLES)
    image_description = models.TextField(blank=True)
    is_tracing = models.BooleanField(default=False)
    display_order = models.IntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='images_uploaded')
    # Set when this row was produced by cropping another image (issue #77):
    # an editor cutting the marking out of a whole-cover scan. Kept so a bad
    # crop can be traced back to what it came from. SET_NULL rather than
    # CASCADE -- deleting the original must not destroy the crop, which by then
    # is the marking's catalog image.
    cropped_from = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='crops',
    )

    class Meta:
        db_table = 'images'
        verbose_name = 'Image'
        verbose_name_plural = 'Images'
        ordering = ['subject_type', 'subject_id', 'display_order']
        indexes = [
            models.Index(fields=['subject_type', 'subject_id', 'display_order']),
            models.Index(fields=['file_checksum']),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(subject_type='MARKING', image_view__in=IMAGE_MARKING_VIEW_CHOICES)
                    | Q(subject_type='COVER', image_view__in=IMAGE_COVER_VIEW_CHOICES)
                ),
                name='image_view_matches_subject_type',
            ),
            models.UniqueConstraint(
                fields=['storage_filename', 'subject_type', 'subject_id'],
                name='image_storage_subject_unique',
            ),
        ]
        permissions = [
            ('approve_image', 'Can approve / reject image submissions'),
        ]

    def clean(self):
        super().clean()
        if self.subject_type == self.SUBJECT_MARKING:
            if self.image_view not in self.MARKING_VIEW_CHOICES:
                raise ValidationError({'image_view': f'Must be one of {self.MARKING_VIEW_CHOICES} when subject_type=MARKING.'})
        elif self.subject_type == self.SUBJECT_COVER:
            if self.image_view not in self.COVER_VIEW_CHOICES:
                raise ValidationError({'image_view': f'Must be one of {self.COVER_VIEW_CHOICES} when subject_type=COVER.'})

    def save(self, *args, **kwargs):
        if not self.file_checksum and hasattr(self, 'file_object'):
            self.file_checksum = self.generate_checksum(self.file_object)
        super().save(*args, **kwargs)

    @staticmethod
    def generate_checksum(file_object):
        sha256_hash = hashlib.sha256()
        for byte_block in iter(lambda: file_object.read(4096), b''):
            sha256_hash.update(byte_block)
        file_object.seek(0)
        return sha256_hash.hexdigest()

    def __str__(self):
        return f'{self.subject_type} #{self.subject_id} - {self.original_filename}'


class Postcover(TimestampedModel):
    """Physical postal covers/envelopes that collectors own"""
    postcover_id = models.AutoField(primary_key=True)
    owner_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='postcovers_owned')
    postcover_key = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'Postcovers'
        verbose_name = 'Example Cover'
        verbose_name_plural = 'Example Covers'
        indexes = [models.Index(fields=['owner_user']), models.Index(fields=['postcover_key'])]

    def __str__(self):
        return f'{self.postcover_key} (Owner: {self.owner_user})'


    def __str__(self):
        return f'{self.postcover} - {self.original_filename}'

    def save(self, *args, **kwargs):
        """Generate file checksum if not provided"""
        if not self.file_checksum and hasattr(self, 'file_object'):
            self.file_checksum = Image.generate_checksum(self.file_object)
        super().save(*args, **kwargs)

class Collection(TimestampedModel):
    """
    An institutional collection -- a curatorial unit that wraps exactly one Region
    and has many Editor assignments. Contributions are routed to a Collection
    based on the state submitted; only Editors assigned to that Collection
    (or a superuser/Administrator) may review them.
    """
    name = models.CharField(max_length=200, help_text='Display name for this Collection (e.g. "Virginia").')
    description = models.TextField(blank=True)
    region = models.OneToOneField(
        'Region',
        on_delete=models.PROTECT,
        related_name='collection',
        help_text='The Region this Collection covers. One Collection per Region.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'Collections'
        verbose_name = 'Collection'
        verbose_name_plural = 'Collections'
        ordering = ['name']

    def __str__(self):
        return self.name


class CollectionAssignment(TimestampedModel):
    """Links an Editor to a Collection they are responsible for curating."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='collection_assignments',
    )
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name='editor_assignments',
    )

    class Meta:
        db_table = 'CollectionAssignments'
        verbose_name = 'Collection assignment'
        verbose_name_plural = 'Collection assignments'
        unique_together = [['user', 'collection']]
        ordering = ['collection', 'user']

    def __str__(self):
        return f'{self.user} -> {self.collection}'

    def save(self, *args, **kwargs):
        """
        On assignment, ensure the user is in the Editors group so that group-level
        permissions (review_contribution, catalog write access, etc.) are granted
        immediately. Removal is intentionally NOT auto-demoted -- admins explicitly
        remove from Editors group via the user admin if they want to revoke perms.
        """
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating:
            from django.contrib.auth.models import Group
            try:
                editors_group = Group.objects.get(name='Editors')
            except Group.DoesNotExist:
                return
            self.user.groups.add(editors_group)

class FAQEntry(TimestampedModel):
    """
    Simple FAQ entry for the public site, managed in Django admin
    and exposed to the SPA via a read-only API.
    """
    faq_entry_id = models.AutoField(primary_key=True)
    question = models.CharField(max_length=500)
    answer = models.TextField()
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0, help_text='Lower numbers appear first.')

    class Meta:
        db_table = 'FAQEntries'
        verbose_name = 'FAQ entry'
        verbose_name_plural = 'FAQ entries'
        ordering = ['display_order', 'faq_entry_id']

    def __str__(self):
        return self.question[:100]

class Region(TimestampedModel):
    """
    A named geographic or administrative area used to organize PostOffices
    within a historical hierarchy. Self-referential for nesting.

    model.md domain type: Region
    """
    REGION_TIER_CHOICES = [('COUNTRY', 'Country'), ('TERRITORY', 'Territory'), ('STATE', 'State'), ('PROVINCE', 'Province'), ('COUNTY', 'County'), ('CITY', 'City'), ('DISTRICT', 'District'), ('OTHER', 'Other')]
    code = models.CharField(max_length=30, unique=True, null=True, blank=True, help_text='Editor-assigned reference identifier')
    name = models.CharField(max_length=100, help_text='Canonical region name for the applicable historical period')
    abbrev = models.CharField(max_length=3, help_text='Canonical two or three character abbreviation')
    region_tier = models.CharField(max_length=9, choices=REGION_TIER_CHOICES)
    parent_region = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='child_regions')
    established_date = models.DateField(null=True, blank=True, help_text='First date this Region definition is considered in force')
    defunct_date = models.DateField(null=True, blank=True, help_text='Last date this Region definition is in force; null = active')

    class Meta:
        verbose_name = 'Region'
        verbose_name_plural = 'Regions'
        ordering = ['name']

    def __str__(self):
        return self.name

class PostOffice(TimestampedModel):
    """
    A postal facility identified as a fixed geographic place. Its political
    jurisdiction over time is recorded as a set of associations to regions
    in post_office_regions; the post office row itself does not name a
    single region.

    model.md domain type: PostOffice
    """
    code = models.CharField(max_length=40, unique=True, null=True, blank=True, help_text='Editor-assigned reference identifier')
    name = models.CharField(max_length=255, help_text='Normalized town name, e.g. Abingdon, Richmond')
    population = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Contemporary population as recorded by the source catalog, where given',
    )

    class Meta:
        db_table = 'post_office'
        verbose_name = 'Post Office'
        verbose_name_plural = 'Post Offices'
        ordering = ['name']

    def __str__(self):
        r = self.region
        return f'{self.name} ({r})' if r is not None else self.name

    @property
    def region(self):
        # Resolve the most-recent active Region linked via the
        # post_office_regions junction. "Active" means defunct_date IS NULL;
        # NULLS-FIRST on defunct_date_desc puts active rows ahead of expired
        # ones, then we tie-break by latest established_date.
        link = (
            self.post_office_regions
            .select_related('region')
            .order_by(
                F('region__defunct_date').desc(nulls_first=True),
                F('region__established_date').desc(nulls_last=True),
            )
            .first()
        )
        return link.region if link is not None else None

    @property
    def regions(self):
        # All Regions this PostOffice is linked to via the post_office_regions
        # junction, ordered the same way as .region: current/active first
        # (defunct_date NULL via NULLS-FIRST), then most-recently established.
        # Unlike .region (which collapses to one), this exposes a town's full
        # multi-territory history -- e.g. Michigan Territory + Michigan -- so a
        # town spanning several jurisdictions over time shows all of them.
        return [
            link.region
            for link in (
                self.post_office_regions
                .select_related('region')
                .order_by(
                    F('region__defunct_date').desc(nulls_first=True),
                    F('region__established_date').desc(nulls_last=True),
                )
            )
        ]

class PostOfficeRegion(TimestampedModel):
    """
    Association linking a PostOffice to a Region under whose jurisdiction
    it operated.

    Validity is normally derived from Region.established_date and
    Region.defunct_date, which is enough while a town's jurisdiction only ever
    ends because the region itself ceased to exist. County boundaries break
    that assumption: when Rappahannock was formed out of Culpeper in 1833 both
    counties survived, so a town's membership ended without either region
    becoming defunct. valid_from/valid_to carry that case and are null whenever
    the region's own lifespan is sufficient.

    model.md domain type: post_office_regions
    """
    post_office = models.ForeignKey(PostOffice, on_delete=models.CASCADE, related_name='post_office_regions')
    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name='post_office_regions')
    valid_from = models.DateField(
        null=True, blank=True,
        help_text='First date this jurisdiction applied; null = from the region\'s own start',
    )
    valid_to = models.DateField(
        null=True, blank=True,
        help_text='Last date this jurisdiction applied; null = still current',
    )

    class Meta:
        db_table = 'post_office_region'
        verbose_name = 'Post Office Region'
        verbose_name_plural = 'Post Office Regions'
        # valid_from participates so one town can belong to the same region
        # across two disjoint spans; NULL repeats stay unique in MySQL only by
        # convention, so the importer keeps at most one open-ended row per pair.
        unique_together = [['post_office', 'region', 'valid_from']]
        ordering = ['post_office__name', 'region__name']

    def __str__(self):
        return f'{self.post_office.name} -- {self.region.name}'


class Postmaster(TimestampedModel):
    """
    A person appointed to run a post office.

    Deliberately just the person. Everything about *when* they served belongs to
    PostmasterTenure, because the same name recurs across towns and decades and
    a postmaster who moved is one person with two tenures, not two rows.

    model.md domain type: Postmaster
    """
    name = models.CharField(max_length=255, help_text='Name as printed in the source, e.g. Gerrard T. Conn')
    sort_name = models.CharField(
        max_length=255, db_index=True,
        help_text='Surname-first form used for ordering and de-duplication',
    )

    class Meta:
        db_table = 'postmaster'
        verbose_name = 'Postmaster'
        verbose_name_plural = 'Postmasters'
        ordering = ['sort_name', 'name']
        indexes = [models.Index(fields=['name'], name='postmaster_name_idx')]

    def __str__(self):
        return self.name


class PostmasterTenure(TimestampedModel):
    """
    One appointment event: this person, this post office, this date.

    The grain is the *event*, not the span, because that is what the sources
    record -- an appointment date and occasionally a discontinuation. An end
    date is implied by the next appointment at the same office and is left to
    the reader rather than invented here.

    Dates carry their own granularity for the same reason DateSeen does: the
    source often gives a year alone, and storing 1 January would assert a
    precision nobody has.

    model.md domain type: PostmasterTenure
    """
    EVENT_APPOINTMENT = 'appointment'
    EVENT_DISCONTINUED = 'discontinued'
    EVENT_REAPPOINTMENT = 'reappointment'
    EVENT_UNKNOWN = 'unknown'
    EVENT_CHOICES = [
        (EVENT_APPOINTMENT, 'Appointment'),
        (EVENT_DISCONTINUED, 'Office discontinued'),
        (EVENT_REAPPOINTMENT, 'Reappointment'),
        (EVENT_UNKNOWN, 'Unknown'),
    ]
    GRANULARITY_CHOICES = [('DAY', 'Day'), ('MONTH', 'Month'), ('YEAR', 'Year')]

    tenure_id = models.AutoField(primary_key=True)
    post_office = models.ForeignKey(
        PostOffice, on_delete=models.PROTECT, related_name='postmaster_tenures')
    postmaster = models.ForeignKey(
        Postmaster, on_delete=models.PROTECT, related_name='tenures', null=True, blank=True,
        help_text='Null for an office-level event such as a discontinuation')
    event = models.CharField(max_length=16, choices=EVENT_CHOICES, default=EVENT_APPOINTMENT)
    date_appointed = models.DateField(
        null=True, blank=True, help_text='Null when the source gives no usable date')
    date_appointed_granularity = models.CharField(
        max_length=5, choices=GRANULARITY_CHOICES, null=True, blank=True,
        help_text='How much of date_appointed the source actually stated')
    source_ref = models.CharField(
        max_length=32, blank=True, default='',
        help_text='Cell reference in the source workbook, e.g. T3:r15904')
    rules_version = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text='RULES.md version that decoded this row')

    class Meta:
        db_table = 'postmaster_tenure'
        verbose_name = 'Postmaster Tenure'
        verbose_name_plural = 'Postmaster Tenures'
        ordering = ['post_office__name', 'date_appointed', 'tenure_id']
        indexes = [
            models.Index(fields=['post_office', 'date_appointed'],
                         name='tenure_office_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(event__in=['appointment', 'discontinued', 'reappointment', 'unknown']),
                name='tenure_event_valid',
            ),
            # The source is the authority on duplicates: one row per source cell.
            models.UniqueConstraint(
                fields=['post_office', 'postmaster', 'date_appointed', 'event'],
                name='tenure_office_person_date_event_unique',
            ),
        ]

    def __str__(self):
        who = self.postmaster.name if self.postmaster_id else self.get_event_display()
        return f'{who} @ {self.post_office.name} ({self.date_appointed or "undated"})'


class Lettering(TimestampedModel):
    """
    Editorial value table for textual styling assigned to a postal marking.

    model.md domain type: Lettering
    Seed values: Italic, Serif, Sans-serif, Small, Large, Outline, Bold, Block,
    Gothic.
    """
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = 'Lettering'
        verbose_name_plural = 'Letterings'
        ordering = ['name']

    def __str__(self):
        return self.name

class Shape(TimestampedModel):
    """
    Editorial value table for the primary form assigned to a postal marking.

    model.md domain type: Shape
    Seed values: SL, BOX, O, C, ARC, Octagon, Pictorial, Ornamental Mortised, Other.
    """
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True, null=True, blank=True, help_text='Editor-assigned reference identifier')

    class Meta:
        verbose_name = 'Shape'
        verbose_name_plural = 'Shapes'
        ordering = ['name']

    def __str__(self):
        return self.name


class CoverQuerySet(models.QuerySet):
    """
    Base queryset for Cover. No custom methods yet; defined so all_objects can
    grow query helpers later and to mirror the MarkingQuerySet/MarkingManager
    shape that the recycle-bin pattern relies on.
    """
    pass


class CoverManager(models.Manager.from_queryset(CoverQuerySet)):
    """
    Default manager for Cover. Hides rows that are in the recycle bin (i.e.
    that have a related CoverRecycleBin row). A cover is "removed" by creating
    its recycle-bin sidecar row, not by mutating the Cover itself -- see
    CoverRecycleBin. Code that must see removed covers (recycle-bin endpoints,
    restore, audit) uses Cover.all_objects.
    """
    def get_queryset(self):
        return super().get_queryset().filter(recycle_bin_entry__isnull=True)


class Cover(TimestampedModel):
    """
    A physical postal cover with recorded postal markings.

    model.md domain type: Cover
    """
    COVER_TYPE_CHOICES = [('FC', 'Folded Cover'), ('FL', 'Folded Letter')]
    code = models.CharField(max_length=30, unique=True, null=True, blank=True, db_column='code', help_text='Editor-assigned reference identifier')
    color = models.ForeignKey(Color, on_delete=models.PROTECT, null=True, blank=True, related_name='covers', help_text='Ink or material color of the cover itself')
    type = models.CharField(max_length=2, choices=COVER_TYPE_CHOICES, null=True, blank=True)
    has_adhesive = models.BooleanField(default=False, help_text='Whether the cover bears an adhesive stamp')
    height = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='Vertical dimension in millimeters')
    is_institutional = models.BooleanField(null=True, blank=True, help_text='Institutionally owned (museum, society, etc.)')
    width = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text='Horizontal dimension in millimeters')
    display_submitter_name = models.BooleanField(default=False, help_text="Whether the submitter opted in to show their name on the public cover detail page")
    description = models.TextField(blank=True, default="", help_text="Free-text description / notes about the cover, shown on the public cover detail page")

    # objects: default manager, EXCLUDES recycle-binned covers.
    # all_objects: unfiltered, INCLUDES recycle-binned covers.
    # base_manager_name='all_objects' makes Django's related/FK access
    # (cover_marking.cover, cover_valuation.cover, snapshots) resolve via the
    # unfiltered manager so structural references never break on a removed row.
    objects = CoverManager()
    all_objects = CoverQuerySet.as_manager()

    class Meta:
        verbose_name = 'Cover'
        verbose_name_plural = 'Covers'
        ordering = ['id']
        base_manager_name = 'all_objects'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.code:
            return
        pk = self.pk
        base = f"C-{pk}"
        candidate = base
        suffix = 0
        # all_objects so a removed cover's code cannot be silently reused.
        while Cover.all_objects.filter(code=candidate).exclude(pk=pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        Cover.all_objects.filter(pk=pk).update(code=candidate)
        self.code = candidate

    def __str__(self):
        if self.code:
            return f'Cover {self.code}'
        return f'Cover #{self.pk}'


class CoverRecycleBin(models.Model):
    """
    Soft-delete sidecar for Cover, mirroring MarkingRecycleBin. The presence of
    a row here means the referenced Cover is "removed" (in the recycle bin).
    The Cover row itself never moves and is never mutated, so all FKs, versions,
    cover-marking links, valuations, dates, images and citations stay intact --
    complete history is preserved. Restoring a cover is just deleting its row
    here.

    The default Cover manager (CoverManager) excludes any Cover that has a row
    in this table; Cover.all_objects includes them.
    """
    cover = models.OneToOneField(
        Cover,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="recycle_bin_entry",
    )
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="covers_removed",
    )
    removed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "cover_recycle_bin"
        verbose_name = "Recycle-binned Cover"
        verbose_name_plural = "Recycle-binned Covers"
        ordering = ["-removed_at"]
        indexes = [
            models.Index(fields=["removed_at"]),
        ]

    def __str__(self):
        return f"Cover #{self.cover_id} removed at {self.removed_at}"


class DateSeen(TimestampedModel):
    """
    A single date point observed for either a Cover or a Marking.
    Polymorphic via (subject_type, subject_id), mirroring the Citation /
    Image polymorphic pattern.

    model.md domain type: dates_seen
    """
    SUBJECT_COVER = 'COVER'
    SUBJECT_MARKING = 'MARKING'
    SUBJECT_TYPE_CHOICES = [(SUBJECT_COVER, 'Cover'), (SUBJECT_MARKING, 'Marking')]

    GRANULARITY_YEAR = 'YEAR'
    GRANULARITY_MONTH = 'MONTH'
    GRANULARITY_DAY = 'DAY'
    GRANULARITY_MONTH_ONLY = 'MONTH_ONLY'
    GRANULARITY_DAY_ONLY = 'DAY_ONLY'
    GRANULARITY_YEAR_DAY = 'YEAR_DAY'
    GRANULARITY_MONTH_DAY = 'MONTH_DAY'

    GRANULARITY_CHOICES = [
        (GRANULARITY_DAY, 'Day'),
        (GRANULARITY_MONTH, 'Month'),
        (GRANULARITY_YEAR, 'Year'),
        (GRANULARITY_MONTH_ONLY, 'Month only'),
        (GRANULARITY_DAY_ONLY, 'Day only'),
        (GRANULARITY_YEAR_DAY, 'Year and day'),
        (GRANULARITY_MONTH_DAY, 'Month and day'),
    ]
    GRANULARITY_VALUES = {value for value, _label in GRANULARITY_CHOICES}

    subject_type = models.CharField(max_length=8, choices=SUBJECT_TYPE_CHOICES)
    subject_id = models.PositiveIntegerField(help_text='PK of the dated Cover or Marking')
    date = models.DateField(null=True, blank=True, help_text='Sortable calendar date when known date parts form one')
    granularity = models.CharField(max_length=10, choices=GRANULARITY_CHOICES)
    date_year = models.PositiveSmallIntegerField(null=True, blank=True)
    date_month = models.PositiveSmallIntegerField(null=True, blank=True)
    date_day = models.PositiveSmallIntegerField(null=True, blank=True)
    date_key = models.CharField(max_length=20, editable=False)

    class Meta:
        db_table = 'dates_seen'
        verbose_name = 'Date Seen'
        verbose_name_plural = 'Dates Seen'
        ordering = ['subject_type', 'subject_id', 'date']
        indexes = [
            models.Index(fields=['subject_type', 'subject_id', 'date'], name='dates_seen_subject_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(subject_type__in=['COVER', 'MARKING']),
                name='dates_seen_subject_type_valid',
            ),
            models.UniqueConstraint(
                fields=['subject_type', 'subject_id', 'granularity', 'date_key'],
                name='dates_seen_subject_parts_unique',
            ),
        ]

    @staticmethod
    def _int_or_none(value):
        if value in (None, ''):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValidationError('Date components must be integers.')
        return parsed

    @staticmethod
    def _legacy_date(value):
        if value in (None, ''):
            return None
        if isinstance(value, date_cls):
            return value
        parsed = parse_date(str(value)[:10])
        if parsed is None:
            raise ValidationError('date must be a valid ISO date.')
        return parsed

    @staticmethod
    def _max_day(year, month):
        if month in (1, 3, 5, 7, 8, 10, 12):
            return 31
        if month in (4, 6, 9, 11):
            return 30
        if month == 2:
            if year is None:
                return 29
            if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
                return 29
            return 28
        return 31

    @classmethod
    def granularity_for_parts(cls, year, month, day):
        has_year = year is not None
        has_month = month is not None
        has_day = day is not None
        if has_year and has_month and has_day:
            return cls.GRANULARITY_DAY
        if has_year and has_month:
            return cls.GRANULARITY_MONTH
        if has_year and has_day:
            return cls.GRANULARITY_YEAR_DAY
        if has_month and has_day:
            return cls.GRANULARITY_MONTH_DAY
        if has_year:
            return cls.GRANULARITY_YEAR
        if has_month:
            return cls.GRANULARITY_MONTH_ONLY
        if has_day:
            return cls.GRANULARITY_DAY_ONLY
        raise ValidationError('At least one date component is required.')

    @classmethod
    def generated_date_for_parts(cls, year, month, day, granularity):
        if granularity == cls.GRANULARITY_YEAR:
            return date_cls(year, 1, 1)
        if granularity == cls.GRANULARITY_MONTH:
            return date_cls(year, month, 1)
        if granularity == cls.GRANULARITY_DAY:
            return date_cls(year, month, day)
        return None

    @staticmethod
    def key_for_parts(year, month, day):
        parts = []
        if year is not None:
            parts.append('Y{:04d}'.format(year))
        if month is not None:
            parts.append('M{:02d}'.format(month))
        if day is not None:
            parts.append('D{:02d}'.format(day))
        return '-'.join(parts)

    def normalize_date_parts(self):
        self.date_year = self._int_or_none(self.date_year)
        self.date_month = self._int_or_none(self.date_month)
        self.date_day = self._int_or_none(self.date_day)

        legacy = self._legacy_date(self.date)
        if self.date_year is None and self.date_month is None and self.date_day is None:
            if legacy is not None:
                granularity = (self.granularity or self.GRANULARITY_DAY).strip().upper()
                if granularity == self.GRANULARITY_YEAR:
                    self.date_year = legacy.year
                elif granularity == self.GRANULARITY_MONTH:
                    self.date_year = legacy.year
                    self.date_month = legacy.month
                elif granularity == self.GRANULARITY_DAY:
                    self.date_year = legacy.year
                    self.date_month = legacy.month
                    self.date_day = legacy.day
                else:
                    raise ValidationError('Partial date granularities require component fields.')

        if self.date_year is not None and not (1 <= self.date_year <= 9999):
            raise ValidationError({'date_year': 'Year must be between 1 and 9999.'})
        if self.date_month is not None and not (1 <= self.date_month <= 12):
            raise ValidationError({'date_month': 'Month must be between 1 and 12.'})
        if self.date_day is not None and not (1 <= self.date_day <= 31):
            raise ValidationError({'date_day': 'Day must be between 1 and 31.'})
        if self.date_month is not None and self.date_day is not None:
            max_day = self._max_day(self.date_year, self.date_month)
            if self.date_day > max_day:
                raise ValidationError({'date_day': 'Day is not valid for the selected month.'})

        expected = self.granularity_for_parts(self.date_year, self.date_month, self.date_day)
        supplied = (self.granularity or expected).strip().upper()
        if supplied not in self.GRANULARITY_VALUES:
            raise ValidationError({'granularity': 'Invalid date granularity.'})
        if supplied != expected:
            raise ValidationError({'granularity': 'Granularity does not match known date components.'})
        self.granularity = expected
        self.date = self.generated_date_for_parts(
            self.date_year,
            self.date_month,
            self.date_day,
            self.granularity,
        )
        self.date_key = self.key_for_parts(self.date_year, self.date_month, self.date_day)

    def clean(self):
        super().clean()
        self.normalize_date_parts()

    def save(self, *args, **kwargs):
        self.normalize_date_parts()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.subject_type} #{self.subject_id} -- {self.date} ({self.granularity})'


class CoverValuation(TimestampedModel):
    """
    Estimated collector market value for a cover.

    model.md domain type: cover_valuations
    """
    id = models.AutoField(primary_key=True)
    cover = models.ForeignKey(Cover, on_delete=models.CASCADE, related_name='valuations')
    amt = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Non-negative USD; null = unpriced entry')
    appraisal_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'cover_valuation'
        verbose_name = 'Cover Valuation'
        verbose_name_plural = 'Cover Valuations'
        ordering = ['-appraisal_date']
        constraints = [
            models.UniqueConstraint(
                fields=['cover', 'appraisal_date'],
                name='cover_valuation_cover_appraisal_date_unique',
            ),
        ]

    def __str__(self):
        return f'Cover #{self.cover_id} - ${self.amt} ({self.appraisal_date})'


class CoverMarking(TimestampedModel):
    """
    Junction linking a Cover to a Marking, with positional context describing
    how the marking appears on that cover.

    model.md domain type: cover_markings
    """
    REVIEW_PENDING = 'pending'
    REVIEW_APPROVED = 'approved'
    REVIEW_REJECTED = 'rejected'
    REVIEW_NEEDS_REVISION = 'needs_revision'
    REVIEW_STATUS_CHOICES = [
        (REVIEW_PENDING, 'Pending review'),
        (REVIEW_APPROVED, 'Approved'),
        (REVIEW_REJECTED, 'Rejected'),
        (REVIEW_NEEDS_REVISION, 'Needs revision'),
    ]

    cover = models.ForeignKey(Cover, on_delete=models.CASCADE, related_name='cover_markings')
    marking = models.ForeignKey(Marking, on_delete=models.CASCADE, related_name='cover_markings')
    is_backstamp = models.BooleanField(default=False, help_text='Whether this marking appears on the reverse of the cover')
    placement = models.CharField(max_length=64, null=True, blank=True, help_text='Free-form placement qualifier; vocabulary not yet enumerated')
    contributor_comment = models.TextField(
        null=True,
        blank=True,
        help_text='Optional note from the contributor for reviewers when this link was submitted.',
    )
    review_status = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_APPROVED,
        help_text='Editor moderation state for this cover-marking association.',
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_cover_markings',
    )
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'cover_marking'
        verbose_name = 'Cover Marking'
        verbose_name_plural = 'Cover Markings'
        unique_together = [['cover', 'marking']]

    def __str__(self):
        return f'Cover #{self.cover_id} <-> Marking #{self.marking_id}'


class ReferenceWork(TimestampedModel):
    """
    A citable publication or source.

    model.md domain type: ReferenceWork
    """
    code = models.CharField(max_length=30, unique=True, null=True, blank=True, help_text='Editor-assigned reference identifier')
    title = models.CharField(max_length=500)
    authorship = models.CharField(max_length=500, help_text='Author(s) or Editor(s) of the publication')
    publisher = models.CharField(max_length=255)
    publication_year = models.IntegerField()
    edition = models.CharField(max_length=50, null=True, blank=True, help_text='Released version of publication')
    volume = models.CharField(max_length=50, null=True, blank=True, help_text='Identifier for a multi-volume series')
    isbn = models.CharField(max_length=20, null=True, blank=True)
    url = models.URLField(max_length=2000, null=True, blank=True)

    class Meta:
        db_table = 'reference_work'
        verbose_name = 'Reference Work'
        verbose_name_plural = 'Reference Works'
        ordering = ['title']

    def __str__(self):
        label = self.code or self.title
        return f'{label} ({self.publication_year})'

class Citation(TimestampedModel):
    """
    Links a ReferenceWork to a Cover or Marking.
    Polymorphic via subjectType/subjectId.

    model.md domain type: citations
    """
    SUBJECT_TYPE_CHOICES = [('COVER', 'Cover'), ('MARKING', 'Marking')]
    reference_work = models.ForeignKey(ReferenceWork, on_delete=models.PROTECT, related_name='citations')
    subject_type = models.CharField(max_length=20, choices=SUBJECT_TYPE_CHOICES)
    subject_id = models.PositiveIntegerField(help_text='PK of the referenced Cover or Marking')
    citation_detail = models.CharField(max_length=500, help_text='Page, section, or URL within the reference work')

    class Meta:
        verbose_name = 'Citation'
        verbose_name_plural = 'Citations'
        ordering = ['reference_work', 'subject_type', 'subject_id']
        constraints = [
            models.CheckConstraint(
                check=Q(subject_type__in=['COVER', 'MARKING']),
                name='citation_subject_type_valid',
            ),
            models.UniqueConstraint(
                fields=['reference_work', 'subject_type', 'subject_id', 'citation_detail'],
                name='citation_reference_subject_detail_unique',
            ),
        ]

    def __str__(self):
        return f'{self.reference_work} -> {self.subject_type} #{self.subject_id}'
