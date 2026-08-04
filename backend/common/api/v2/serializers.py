###################################################################################################
## WoCo Commons - API v2 Serializers (Phase 2 rewrite)
##
## Unified Marking model: Townmark / Ratemark / Auxmark are rows in a single
## Marking table discriminated by `type`. CoverMarking carries placement.
## CoverValuation belongs to Cover. DateSeen is polymorphic over
## (subject_type, subject_id) and can be attached to a Cover or a Marking.
## Image is polymorphic over (subject_type, subject_id).
###################################################################################################
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from common.catalog_codes import (
    CatalogCodeError,
    strip_catalog_code_keys,
    validate_unique_catalog_code,
)
from common.contribution_consolidation import contribution_target
from common.models import (
    Citation,
    Collection,
    CollectionAssignment,
    Color,
    Contribution,
    Cover,
    CoverMarking,
    CoverRecycleBin,
    CoverValuation,
    DateSeen,
    FAQEntry,
    Image,
    Lettering,
    Marking,
    MarkingRecycleBin,
    MarkingType,
    PostOffice,
    ReferenceWork,
    Region,
    Shape,
)

from .permissions import (
    _user_is_responsible_for_cover,
    _user_is_responsible_for_marking,
    REVIEW_CONTRIBUTION_PERM,
)


User = get_user_model()


def _viewer_may_see_catalog_code(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or user.has_perm(REVIEW_CONTRIBUTION_PERM))


def _redact_catalog_code(serializer, data):
    request = serializer.context.get("request") if serializer.context else None
    user = getattr(request, "user", None)
    if not _viewer_may_see_catalog_code(user):
        data["code"] = None
    return data


###################################################################################################
## Lookup / shared
###################################################################################################
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "is_staff", "is_superuser"]
        read_only_fields = fields


class LoginRequestSerializer(serializers.Serializer):
    """Validates login access request (email, first_name, last_name). Creates User directly."""
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )
        return value


class ColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Color
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class PostOfficeSerializer(serializers.ModelSerializer):
    # PostOffice.region is a property that resolves to the most-recent active
    # Region via the post_office_regions junction; both fields return "" when
    # the PO has no junction row.
    region_name = serializers.CharField(source="region.name", read_only=True, default="")
    region_abbrev = serializers.CharField(source="region.abbrev", read_only=True, default="")

    class Meta:
        model = PostOffice
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class LetteringSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lettering
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class ShapeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shape
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class ReferenceWorkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferenceWork
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date", "created_by", "modified_by"]


class FAQEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQEntry
        fields = ["faq_entry_id", "question", "answer", "is_active", "display_order"]


###################################################################################################
## Image (polymorphic over COVER | MARKING)
###################################################################################################
class ImageSerializer(serializers.ModelSerializer):
    """Polymorphic image attached to either a Cover or a Marking by (subject_type, subject_id)."""
    image_url = serializers.SerializerMethodField()
    # Multipart upload support: clients may POST a raw image file under `file`.
    # We store it under MEDIA_ROOT and persist storage_filename + extracted metadata.
    # Use FileField (not ImageField): ImageField runs PIL validation before `create()`
    # and can reject uploads that we still handle via extract_image_metadata.
    file = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Image
        fields = [
            "image_id",
            "subject_type",
            "subject_id",
            "file",
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
            "image_url",
            "created_date",
        ]
        read_only_fields = [
            "image_id",
            "created_date",
            "modified_date",
            "uploaded_by",
        ]
        # These are always filled server-side when `file` is uploaded. DRF would
        # otherwise require them on the incoming payload before `create()` runs.
        extra_kwargs = {
            "original_filename": {"required": False, "allow_blank": True},
            "storage_filename": {"required": False, "allow_blank": True},
            "mime_type": {"required": False, "allow_blank": True},
            "image_width": {"required": False},
            "image_height": {"required": False},
            "file_size_bytes": {"required": False},
            "file_checksum": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        # Create: either multipart `file` (normal SPA upload) or a full manual row
        # (imports) with storage_filename + metadata.
        if self.instance is not None:
            return self._validate_update(attrs)
        has_file = attrs.get("file") is not None
        if has_file:
            return attrs
        storage = (attrs.get("storage_filename") or "").strip()
        if not storage:
            raise serializers.ValidationError(
                {
                    "file": (
                        "Send the image as multipart form field `file`. "
                        "Optional text fields (original_filename, mime_type, etc.) "
                        "may be included but are derived server-side when `file` is present."
                    )
                }
            )
        manual = (
            "original_filename",
            "mime_type",
            "image_width",
            "image_height",
            "file_size_bytes",
            "file_checksum",
        )
        missing = [k for k in manual if attrs.get(k) is None]
        if missing:
            raise serializers.ValidationError(
                {k: "Required when `file` is omitted (import path)." for k in missing}
            )
        return attrs

    def _validate_update(self, attrs):
        """
        Update path. subject_type/subject_id are writable so editors can move
        an image between a marking and a cover (issue #48; v1 attached every
        upload to the marking). subject_id is a plain integer column, so the
        target's existence must be checked here, and the image_view/subject
        pairing is validated so a mismatch is a 400 instead of the DB check
        constraint's 500.
        """
        subject_type = attrs.get("subject_type", self.instance.subject_type)
        subject_id = attrs.get("subject_id", self.instance.subject_id)
        image_view = attrs.get("image_view", self.instance.image_view)

        subject_changed = (
            subject_type != self.instance.subject_type
            or subject_id != self.instance.subject_id
        )
        if subject_changed:
            if subject_type == Image.SUBJECT_MARKING:
                target_exists = Marking.all_objects.filter(pk=subject_id).exists()
            else:
                target_exists = Cover.all_objects.filter(pk=subject_id).exists()
            if not target_exists:
                raise serializers.ValidationError(
                    {"subject_id": f"{subject_type} {subject_id} does not exist."}
                )

        allowed_views = (
            Image.MARKING_VIEW_CHOICES
            if subject_type == Image.SUBJECT_MARKING
            else Image.COVER_VIEW_CHOICES
        )
        if image_view not in allowed_views:
            raise serializers.ValidationError(
                {
                    "image_view": (
                        f"Must be one of {allowed_views} when "
                        f"subject_type={subject_type}."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        """
        Support multipart upload: when `file` is provided, write it to MEDIA_ROOT
        and populate the Image metadata fields server-side.

        Clients may alternatively create Image rows by directly supplying
        storage_filename + metadata (e.g. for imported assets); in that case
        `file` can be omitted.
        """
        uploaded = validated_data.pop("file", None)
        if uploaded is None:
            return super().create(validated_data)

        # Ignore empty client hints so computed metadata always wins.
        for k in (
            "original_filename",
            "storage_filename",
            "mime_type",
            "image_width",
            "image_height",
            "file_size_bytes",
            "file_checksum",
        ):
            validated_data.pop(k, None)

        from django.conf import settings
        from common.images import extract_image_metadata
        import os
        import uuid

        content_type = (getattr(uploaded, "content_type", "") or "").strip().lower()
        try:
            uploaded.seek(0)
        except Exception:
            pass
        content = uploaded.read()
        if not content:
            raise serializers.ValidationError({"file": "Uploaded file is empty."})
        max_size_bytes = 100 * 1024 * 1024
        if len(content) > max_size_bytes:
            raise serializers.ValidationError({"file": "Uploaded file is too large (max 100MB)."})
        try:
            uploaded.seek(0)
        except Exception:
            pass

        # Browsers sometimes omit or mislabel Content-Type on multipart parts.
        if content_type not in {"image/png", "image/jpeg", "image/jpg", "image/tiff"}:
            if content[:8] == b"\x89PNG\r\n\x1a\n":
                content_type = "image/png"
            elif content[:2] == b"\xff\xd8":
                content_type = "image/jpeg"
            elif content[:4] in (b"II*\x00", b"MM\x00*"):
                content_type = "image/tiff"
            else:
                content_type = ""

        metadata = extract_image_metadata(content, content_type)
        if metadata is None:
            raise serializers.ValidationError({"file": "Unsupported image format."})

        if "png" in content_type:
            ext = "png"
        elif "tiff" in content_type:
            ext = "tiff"
        else:
            ext = "jpg"

        subdir = "uploads"
        storage_name = f"{subdir}/{uuid.uuid4().hex}.{ext}"
        os.makedirs(os.path.join(settings.MEDIA_ROOT, subdir), exist_ok=True)
        file_path = os.path.join(settings.MEDIA_ROOT, storage_name)
        with open(file_path, "wb") as f:
            f.write(content)

        validated_data["storage_filename"] = storage_name
        validated_data["original_filename"] = (
            (getattr(uploaded, "name", "") or "image")[:255]
        )
        validated_data["mime_type"] = metadata.get("mime_type") or content_type or "image/jpeg"
        validated_data["image_width"] = metadata.get("image_width")
        validated_data["image_height"] = metadata.get("image_height")
        validated_data["file_size_bytes"] = metadata.get("file_size_bytes") or len(content)
        validated_data["file_checksum"] = metadata.get("file_checksum")

        return super().create(validated_data)

    def get_image_url(self, obj):
        """
        Build the public URL for the stored image file.

        Current layout: contributor uploads live under
        MEDIA_ROOT/<region_abbrev>/<uuid>.<ext>, with storage_filename
        like 'va/<uuid>.png'. The public URL is MEDIA_URL + storage_filename,
        e.g. /media/va/<uuid>.png.

        Some imported storage_filename values include their media subdirectory;
        those files still live under MEDIA_ROOT at that exact path.
        """
        storage = (obj.storage_filename or "").lstrip("/")
        if not storage:
            return None
        request = self.context.get("request")
        if not request:
            return None
        from django.conf import settings
        media_url = settings.MEDIA_URL.rstrip("/")
        if storage.startswith("markings/"):
            # Legacy stored value: strip the markings/ prefix so files served
            # from the new MEDIA_ROOT/<abbrev>/ layout resolve correctly.
            storage = storage[len("markings/"):]
        path = f"{media_url}/{storage}"
        return request.build_absolute_uri(path)


###################################################################################################
## Citation (subject_type COVER | MARKING)
###################################################################################################
class CitationSerializer(serializers.ModelSerializer):
    # Nested read-only view of the linked ReferenceWork so callers (the
    # Marking detail endpoint, in particular) can render citation rows
    # without a second round-trip to /reference-works/. Mirrors the
    # `cover_details` pattern on CoverMarkingSerializer. The writable
    # `reference_work` FK field is still exposed for create/update.
    reference_work_details = ReferenceWorkSerializer(source="reference_work", read_only=True)

    class Meta:
        model = Citation
        fields = "__all__"
        read_only_fields = ["id", "created_date", "modified_date"]

    def validate_subject_type(self, value):
        if value not in {"COVER", "MARKING"}:
            raise serializers.ValidationError("subject_type must be COVER or MARKING.")
        return value


###################################################################################################
## Cover, DateSeen, CoverValuation, CoverMarking
###################################################################################################
class DateSeenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DateSeen
        # Explicit field list (mirrors CoverSerializer / CoverMarkingSerializer)
        # so DRF doesn't auto-generate `created_by` / `modified_by` as
        # required write fields. Those columns come from the
        # TimestampedModel base and are populated by the viewset's
        # perform_create / perform_update from request.user; if they're
        # exposed on the serializer the SPA gets a 400 like
        # `{created_by: ["This field is required."], modified_by: [...]}`
        # because validation runs before perform_create.
        fields = [
            "id",
            "subject_type",
            "subject_id",
            "date",
            "granularity",
            "date_year",
            "date_month",
            "date_day",
            "created_date",
            "modified_date",
        ]
        read_only_fields = ["id", "created_date", "modified_date"]
        extra_kwargs = {
            "date": {"required": False, "allow_null": True},
            "granularity": {"required": False},
            "date_year": {"required": False, "allow_null": True},
            "date_month": {"required": False, "allow_null": True},
            "date_day": {"required": False, "allow_null": True},
        }

    def validate_subject_type(self, value):
        if value not in {DateSeen.SUBJECT_COVER, DateSeen.SUBJECT_MARKING}:
            raise serializers.ValidationError("subject_type must be COVER or MARKING.")
        return value

    def validate(self, attrs):
        component_keys = {"date_year", "date_month", "date_day"}
        has_component_input = any(key in attrs for key in component_keys)
        has_legacy_date_input = "date" in attrs

        values = {}
        for key in (
            "subject_type",
            "subject_id",
            "date",
            "granularity",
            "date_year",
            "date_month",
            "date_day",
        ):
            if self.instance is not None:
                values[key] = getattr(self.instance, key)
            else:
                values[key] = None
            if key in attrs:
                values[key] = attrs[key]

        if has_legacy_date_input and not has_component_input:
            values["date_year"] = None
            values["date_month"] = None
            values["date_day"] = None

        row = DateSeen(**values)
        try:
            row.normalize_date_parts()
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError(exc.messages)

        attrs["date"] = row.date
        attrs["granularity"] = row.granularity
        attrs["date_year"] = row.date_year
        attrs["date_month"] = row.date_month
        attrs["date_day"] = row.date_day
        return attrs


class CoverSerializer(serializers.ModelSerializer):
    # `dates_seen` is not a reverse FK relation any more (DateSeen is polymorphic
    # via subject_type/subject_id), so we expose it via a SerializerMethodField
    # filtered to subject_type='COVER' and subject_id=cover.pk.
    color_name = serializers.CharField(source="color.name", read_only=True)
    dates_seen = serializers.SerializerMethodField()
    is_removed = serializers.SerializerMethodField()
    can_remove = serializers.SerializerMethodField()
    # Derived display name of the submitter (the cover's created_by, i.e. the
    # contributor). Returned ONLY when the submitter opted in -- privacy is
    # enforced here at the API boundary, not just hidden in the UI.
    submitter_name = serializers.SerializerMethodField()

    class Meta:
        model = Cover
        fields = [
            "id",
            "code",
            "color",
            "color_name",
            "type",
            "has_adhesive",
            "height",
            "is_institutional",
            "width",
            "display_submitter_name",
            "submitter_name",
            "description",
            "dates_seen",
            "is_removed",
            "can_remove",
            "created_date",
            "modified_date",
        ]
        # `code` is editor-writable so editors can override the auto-generated
        # C-{pk} value when importing catalogs that carry their own coding
        # systems. Cover.save() only auto-assigns when code is falsy, so an
        # editor-set value sticks across later saves. Contributor write access
        # is already blocked by the viewset's IsEditorOrAdminWrite permission.
        read_only_fields = ["id", "created_date", "modified_date"]

    def to_representation(self, instance):
        return _redact_catalog_code(self, super().to_representation(instance))

    def validate_code(self, value):
        code = str(value or "").strip()
        if not code:
            return None
        exclude_id = self.instance.pk if self.instance is not None else None
        try:
            return validate_unique_catalog_code(
                subject_type="COVER",
                code=code,
                exclude_id=exclude_id,
            )
        except CatalogCodeError as exc:
            raise serializers.ValidationError(str(exc))

    def get_submitter_name(self, obj):
        # Opt-out (the default) must never leak the name.
        if not obj.display_submitter_name:
            return None
        user = obj.created_by
        if user is None:
            return None
        full = (user.get_full_name() or "").strip()
        return full or user.get_username()

    def get_dates_seen(self, obj):
        qs = DateSeen.objects.filter(
            subject_type=DateSeen.SUBJECT_COVER,
            subject_id=obj.pk,
        ).order_by("date", "date_year", "date_month", "date_day", "pk")
        return DateSeenSerializer(qs, many=True).data

    def get_is_removed(self, obj):
        return CoverRecycleBin.objects.filter(cover_id=obj.pk).exists()

    def get_can_remove(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None:
            return False
        return _user_is_responsible_for_cover(user, obj)


class CoverValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoverValuation
        fields = [
            "id",
            "cover",
            "amt",
            "appraisal_date",
            "created_date",
            "modified_date",
        ]
        read_only_fields = ["id", "created_date", "modified_date"]


class CoverMarkingSerializer(serializers.ModelSerializer):
    cover_details = CoverSerializer(source="cover", read_only=True)
    reviewer_username = serializers.SerializerMethodField()
    contributor_comment = serializers.CharField(
        allow_blank=True,
        allow_null=True,
        required=False,
    )

    class Meta:
        model = CoverMarking
        fields = [
            "id",
            "cover",
            "cover_details",
            "marking",
            "is_backstamp",
            "placement",
            "contributor_comment",
            "review_status",
            "review_notes",
            "reviewed_at",
            "reviewer",
            "reviewer_username",
            "created_date",
            "modified_date",
        ]
        read_only_fields = [
            "id",
            "review_status",
            "review_notes",
            "reviewed_at",
            "reviewer",
            "reviewer_username",
            "created_date",
            "modified_date",
        ]

    def get_reviewer_username(self, obj):
        if obj.reviewer_id and obj.reviewer:
            return obj.reviewer.get_username()
        return ""


###################################################################################################
## Marking (unified TOWNMARK | RATEMARK | AUXMARK)
###################################################################################################
def _format_decimal(value):
    if value is None:
        return None
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return None


def _marking_resolved_region(marking):
    """Active Region for a marking via PostOffice.post_office_regions (not a Marking FK)."""
    if not getattr(marking, "post_office_id", None):
        return None
    post_office = getattr(marking, "post_office", None)
    if post_office is None:
        return None
    return post_office.region


def _marking_state_name(marking) -> str:
    region = _marking_resolved_region(marking)
    return (region.name or "") if region else ""


def _marking_state_abbrev(marking) -> str:
    region = _marking_resolved_region(marking)
    return (region.abbrev or "") if region else ""


def _marking_regions(marking):
    """All Regions for a marking's PostOffice (current-first), as serializable
    dicts. Empty list when there is no post office. Mirrors the single-region
    helpers above but exposes the town's full multi-territory history."""
    if not getattr(marking, "post_office_id", None):
        return []
    post_office = getattr(marking, "post_office", None)
    if post_office is None:
        return []
    return [
        {"id": r.id, "name": r.name, "abbrev": r.abbrev, "region_tier": r.region_tier}
        for r in post_office.regions
    ]


def _viewer_may_see_contribution_notes(user, contribution) -> bool:
    # contribution: Contribution | None. Returns True for any editor/admin/superuser
    # or the contributor who made it. False for anon and unrelated users. This is
    # intentionally NOT region-scoped (unlike _user_is_responsible_for_marking):
    # any editor may see the comment-for-editor and editor feedback.
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if contribution is None:
        return False
    if user.is_superuser or user.has_perm(REVIEW_CONTRIBUTION_PERM):
        return True
    return getattr(contribution, "contributor_id", None) == user.id


def _comment_for_editor_from(submitted_data) -> str:
    # The contributor's "comment for editor" lives in Contribution.submitted_data.
    # The submit form writes it under both contributor_comment and comment_for_editor;
    # fall back to plain "comment" for older rows.
    if not isinstance(submitted_data, dict):
        return ""
    for key in ("contributor_comment", "comment_for_editor", "comment"):
        val = submitted_data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class MarkingListSerializer(serializers.ModelSerializer):
    """
    Lightweight Marking row used by /api/v2/markings/ list/search.

    Returns the same nested-name fields the frontend already consumes
    (state, town, shape_name, color_name, ...), plus the unified `type`
    discriminator and aggregated earliest_seen / latest_seen.
    """
    state = serializers.SerializerMethodField()
    state_abbrev = serializers.SerializerMethodField()
    region_name = serializers.SerializerMethodField()
    town = serializers.CharField(source="post_office.name", read_only=True, default="")
    shape_name = serializers.CharField(source="shape.name", read_only=True, default="")
    lettering_name = serializers.CharField(source="lettering.name", read_only=True, default="")
    color_name = serializers.CharField(source="color.name", read_only=True, default="")
    post_office_name = serializers.CharField(source="post_office.name", read_only=True, default="")
    earliest_seen = serializers.DateField(read_only=True, allow_null=True, required=False)
    earliest_seen_granularity = serializers.CharField(read_only=True, allow_null=True, required=False)
    latest_seen = serializers.DateField(read_only=True, allow_null=True, required=False)
    latest_seen_granularity = serializers.CharField(read_only=True, allow_null=True, required=False)
    main_image = serializers.SerializerMethodField()
    second_image = serializers.SerializerMethodField()
    size_display = serializers.SerializerMethodField()
    is_reviewed = serializers.BooleanField(read_only=True)

    class Meta:
        model = Marking
        fields = [
            "id",
            "code",
            "type",
            "catalog_txt",
            "inscription_txt",
            "desc",
            "is_manuscript",
            "is_irreg",
            "width",
            "height",
            "size_display",
            "date_fmt",
            "impression",
            "rate_val",
            "post_office",
            "shape",
            "lettering",
            "color",
            "state",
            "state_abbrev",
            "town",
            "shape_name",
            "lettering_name",
            "color_name",
            "post_office_name",
            "region_name",
            "is_reviewed",
            "earliest_seen",
            "earliest_seen_granularity",
            "latest_seen",
            "latest_seen_granularity",
            "main_image",
            "second_image",
        ]

    def get_state(self, obj):
        return _marking_state_name(obj)

    def to_representation(self, instance):
        return _redact_catalog_code(self, super().to_representation(instance))

    def get_state_abbrev(self, obj):
        return _marking_state_abbrev(obj)

    def get_region_name(self, obj):
        return _marking_state_name(obj)

    def _images_for(self, obj):
        cached = getattr(obj, "_marking_images", None)
        if cached is not None:
            return cached
        rows = list(
            Image.objects.filter(
                subject_type=Image.SUBJECT_MARKING,
                subject_id=obj.pk,
            ).order_by("display_order", "image_id")
        )
        obj._marking_images = rows
        return rows

    def _image_payload(self, image):
        if not image:
            return None
        return ImageSerializer(image, context=self.context).data

    def get_main_image(self, obj):
        rows = self._images_for(obj)
        return self._image_payload(rows[0] if rows else None)

    def get_second_image(self, obj):
        rows = self._images_for(obj)
        return self._image_payload(rows[1] if len(rows) > 1 else None)

    def get_size_display(self, obj):
        w = _format_decimal(obj.width)
        h = _format_decimal(obj.height)
        if w and h:
            return f"{w}x{h}"
        return w or h or None


class MarkingSerializer(serializers.ModelSerializer):
    """
    Full Marking serializer used for retrieve / create / update.

    Includes images and citations attached to this marking, plus aggregated
    earliest_seen / latest_seen across covers it appears on.
    """
    state = serializers.SerializerMethodField()
    state_abbrev = serializers.SerializerMethodField()
    region_name = serializers.SerializerMethodField()
    regions = serializers.SerializerMethodField()
    town = serializers.CharField(source="post_office.name", read_only=True, default="")
    shape_name = serializers.CharField(source="shape.name", read_only=True, default="")
    lettering_name = serializers.CharField(source="lettering.name", read_only=True, default="")
    color_name = serializers.CharField(source="color.name", read_only=True, default="")
    post_office_name = serializers.CharField(source="post_office.name", read_only=True, default="")
    earliest_seen = serializers.DateField(read_only=True, allow_null=True, required=False)
    earliest_seen_granularity = serializers.CharField(read_only=True, allow_null=True, required=False)
    latest_seen = serializers.DateField(read_only=True, allow_null=True, required=False)
    latest_seen_granularity = serializers.CharField(read_only=True, allow_null=True, required=False)
    # Full list of MARKING-scoped DateSeen rows (one per observed date the catalog
    # records), not just the earliest/latest boundary. Exposed only on the detail
    # serializer so the UI can show a "Dates Seen" listing when a marking has
    # multiple dates (issue #25); kept off MarkingListSerializer to avoid an
    # N+1 query on search. Mirrors CoverSerializer.get_dates_seen.
    dates_seen = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    citations = serializers.SerializerMethodField()
    size_display = serializers.SerializerMethodField()
    created_by = UserSerializer(read_only=True)
    modified_by = UserSerializer(read_only=True)
    is_removed = serializers.SerializerMethodField()
    can_remove = serializers.SerializerMethodField()
    comment_for_editor = serializers.SerializerMethodField()
    editor_feedback = serializers.SerializerMethodField()

    class Meta:
        model = Marking
        fields = [
            "id",
            "code",
            "type",
            "catalog_txt",
            "inscription_txt",
            "desc",
            "is_manuscript",
            "is_irreg",
            "width",
            "height",
            "size_display",
            "date_fmt",
            "impression",
            "rate_val",
            "post_office",
            "shape",
            "lettering",
            "color",
            "state",
            "state_abbrev",
            "town",
            "shape_name",
            "lettering_name",
            "color_name",
            "post_office_name",
            "region_name",
            "regions",
            "is_reviewed",
            "earliest_seen",
            "earliest_seen_granularity",
            "latest_seen",
            "latest_seen_granularity",
            "dates_seen",
            "images",
            "citations",
            "created_date",
            "modified_date",
            "created_by",
            "modified_by",
            "is_removed",
            "can_remove",
            "comment_for_editor",
            "editor_feedback",
        ]
        read_only_fields = ["id", "created_date", "modified_date"]

    def to_representation(self, instance):
        return _redact_catalog_code(self, super().to_representation(instance))

    def validate_code(self, value):
        code = str(value or "").strip()
        if not code:
            return None
        exclude_id = self.instance.pk if self.instance is not None else None
        try:
            return validate_unique_catalog_code(
                subject_type="MARKING",
                code=code,
                exclude_id=exclude_id,
            )
        except CatalogCodeError as exc:
            raise serializers.ValidationError(str(exc))

    def get_is_removed(self, obj):
        return MarkingRecycleBin.objects.filter(marking_id=obj.pk).exists()

    def get_can_remove(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None:
            return False
        return _user_is_responsible_for_marking(user, obj)

    def get_editor_feedback(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        contribution = getattr(obj, "contribution", None)
        if not _viewer_may_see_contribution_notes(user, contribution):
            return ""
        return (contribution.review_notes or "").strip()

    def get_comment_for_editor(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        contribution = getattr(obj, "contribution", None)
        if not _viewer_may_see_contribution_notes(user, contribution):
            return ""
        return _comment_for_editor_from(contribution.submitted_data)

    def get_state(self, obj):
        return _marking_state_name(obj)

    def get_state_abbrev(self, obj):
        return _marking_state_abbrev(obj)

    def get_region_name(self, obj):
        return _marking_state_name(obj)

    def get_regions(self, obj):
        return _marking_regions(obj)

    def get_dates_seen(self, obj):
        qs = DateSeen.objects.filter(
            subject_type=DateSeen.SUBJECT_MARKING,
            subject_id=obj.pk,
        ).order_by("date", "date_year", "date_month", "date_day", "pk")
        return DateSeenSerializer(qs, many=True).data

    def get_images(self, obj):
        rows = Image.objects.filter(
            subject_type=Image.SUBJECT_MARKING,
            subject_id=obj.pk,
        ).order_by("display_order", "image_id")
        return ImageSerializer(rows, many=True, context=self.context).data

    def get_citations(self, obj):
        rows = Citation.objects.filter(
            subject_type="MARKING",
            subject_id=obj.pk,
        ).select_related("reference_work").order_by("reference_work_id")
        return CitationSerializer(rows, many=True, context=self.context).data

    def get_size_display(self, obj):
        w = _format_decimal(obj.width)
        h = _format_decimal(obj.height)
        if w and h:
            return f"{w}x{h}"
        return w or h or None

    def validate_type(self, value):
        valid = {c[0] for c in MarkingType.choices}
        if value not in valid:
            raise serializers.ValidationError(f"type must be one of {sorted(valid)}.")
        return value


###################################################################################################
## Contribution (moderation queue)
###################################################################################################
def _contribution_submitted_data_is_cover(sd) -> bool:
    if not isinstance(sd, dict):
        return False
    kind = str(sd.get("submission_kind") or sd.get("submissionKind") or "").strip().lower()
    if kind == "cover":
        return True
    if kind in {"marking", "townmark", "ratemark", "auxmark"}:
        return False
    type_value = str(sd.get("type") or "").strip().upper()
    has_cover_type = type_value in {"FC", "FL"}
    has_marking_type = type_value in {"TOWNMARK", "RATEMARK", "AUXMARK"}
    has_town = bool(str(sd.get("town") or "").strip())
    parent_raw = sd.get("parent_marking_id") or sd.get("marking_id")
    has_parent = parent_raw not in (None, "")
    has_cover_date = _submitted_cover_date_label(sd) != ""
    return bool(has_parent and (has_cover_type or has_cover_date) and not has_town and not has_marking_type)


_MONTH_LABELS = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}


def _submitted_cover_date_component(sd, key):
    raw = sd.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _submitted_cover_date_label(sd) -> str:
    if str(sd.get("cover_date_unknown") or "").strip().lower() in {"true", "1", "yes", "on"}:
        return "Date unknown"
    legacy = str(sd.get("cover_date") or sd.get("coverDate") or "").strip()
    if legacy:
        granularity = str(sd.get("cover_granularity") or sd.get("coverGranularity") or "DAY").strip().upper()
        parts = legacy.split("-")
        if len(parts) >= 1 and granularity == "YEAR":
            return parts[0]
        if len(parts) >= 2 and granularity == "MONTH":
            month = _MONTH_LABELS.get(_submitted_cover_date_component({"m": parts[1]}, "m"))
            return "{}, {}".format(month, parts[0]) if month else legacy
        return legacy
    year = _submitted_cover_date_component(sd, "cover_date_year")
    month = _submitted_cover_date_component(sd, "cover_date_month")
    day = _submitted_cover_date_component(sd, "cover_date_day")
    month_label = _MONTH_LABELS.get(month)
    if year is not None and month_label and day is not None:
        return "{:02d}/{:02d}/{}".format(month, day, year)
    if year is not None and month_label:
        return "{}, {}".format(month_label, year)
    if year is not None and day is not None:
        return "Day {}, {} (month unknown)".format(day, year)
    if month_label and day is not None:
        return "{} {} (year unknown)".format(month_label, day)
    if year is not None:
        return str(year)
    if month_label:
        return "{} (year unknown)".format(month_label)
    if day is not None:
        return "Day {} (month/year unknown)".format(day)
    return ""


def _contribution_target_marking_id(obj):
    sd = obj.submitted_data or {}
    if _contribution_submitted_data_is_cover(sd):
        raw = sd.get("parent_marking_id") or sd.get("marking_id")
        if raw in (None, ""):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    target = contribution_target(obj)
    if target is None or target.kind != "marking":
        return None
    return target.id


def _contribution_target_cover_id(obj):
    target = contribution_target(obj)
    if target is None or target.kind != "cover":
        return None
    return target.id


class ContributionListSerializer(serializers.ModelSerializer):
    """List view for contributions (moderation queue)."""
    contributor_username = serializers.CharField(source="contributor.username", read_only=True)
    reviewer_username = serializers.CharField(source="reviewer.username", read_only=True, allow_null=True)
    marking_id = serializers.SerializerMethodField()
    cover_id = serializers.SerializerMethodField()
    state_display = serializers.SerializerMethodField()
    town_display = serializers.SerializerMethodField()
    type_display = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    # Wire format kept as created_at / updated_at for frontend stability; the
    # underlying model fields are created_date / modified_date from
    # TimestampedModel.
    created_at = serializers.DateTimeField(source="created_date", read_only=True)
    updated_at = serializers.DateTimeField(source="modified_date", read_only=True)

    class Meta:
        model = Contribution
        fields = [
            "id",
            "contributor",
            "contributor_username",
            "marking",
            "marking_id",
            "cover_id",
            "collection",
            "status",
            "reviewer",
            "reviewer_username",
            "review_notes",
            "created_at",
            "updated_at",
            "submitted_data",
            "state_display",
            "town_display",
            "type_display",
            "display_name",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_marking_id(self, obj):
        return _contribution_target_marking_id(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request") if self.context else None
        user = getattr(request, "user", None)
        if not _viewer_may_see_catalog_code(user):
            submitted = data.get("submitted_data")
            if isinstance(submitted, dict):
                data["submitted_data"] = strip_catalog_code_keys(submitted)
        return data

    def get_cover_id(self, obj):
        return _contribution_target_cover_id(obj)

    def get_state_display(self, obj):
        return (obj.submitted_data or {}).get("state", "-")

    def get_town_display(self, obj):
        return (obj.submitted_data or {}).get("town", "-")

    def get_type_display(self, obj):
        return (obj.submitted_data or {}).get("type", "-")

    def get_display_name(self, obj):
        sd = obj.submitted_data or {}

        if _contribution_submitted_data_is_cover(sd):
            cover_types = {"FC": "Folded Cover", "FL": "Folded Letter"}
            type_code = str(sd.get("type") or "").strip().upper()
            type_label = cover_types.get(type_code, type_code or "Cover")
            date = _submitted_cover_date_label(sd)
            parent = sd.get("parent_marking_id") or sd.get("marking_id")
            parts = ["Cover draft", type_label]
            if date:
                parts.append(date)
            if parent not in (None, ""):
                parts.append(f"Marking #{parent}")
            label = " - ".join([p for p in parts if p])
            return label or f"Cover draft #{obj.id}"

        town = (sd.get("town") or "").strip()
        state = (sd.get("state") or "").strip()
        type_display = (sd.get("type") or "").strip()
        location = ", ".join([x for x in [town, state] if x])
        parts = [x for x in [location, type_display] if x and x.lower() != "unknown"]
        return " - ".join(parts) if parts else f"Submission #{obj.id}"


class ContributionDetailSerializer(serializers.ModelSerializer):
    contributor_username = serializers.CharField(source="contributor.username", read_only=True)
    reviewer_username = serializers.CharField(source="reviewer.username", read_only=True, allow_null=True)
    marking_id = serializers.SerializerMethodField()
    cover_id = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="created_date", read_only=True)
    updated_at = serializers.DateTimeField(source="modified_date", read_only=True)

    class Meta:
        model = Contribution
        fields = [
            "id",
            "contributor",
            "contributor_username",
            "marking",
            "marking_id",
            "cover_id",
            "collection",
            "submitted_data",
            "status",
            "reviewer",
            "reviewer_username",
            "review_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "contributor", "marking", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request") if self.context else None
        user = getattr(request, "user", None)
        if not _viewer_may_see_catalog_code(user):
            submitted = data.get("submitted_data")
            if isinstance(submitted, dict):
                data["submitted_data"] = strip_catalog_code_keys(submitted)
        return data

    def get_marking_id(self, obj):
        return _contribution_target_marking_id(obj)

    def get_cover_id(self, obj):
        return _contribution_target_cover_id(obj)


class ContributionApproveRejectSerializer(serializers.Serializer):
    """Payload for approve / reject actions."""
    review_notes = serializers.CharField(required=False, allow_blank=True)
    catalog_code = serializers.CharField(required=False, allow_blank=True)


###################################################################################################
## Collection (institutional unit, F7)
###################################################################################################
class _NestedRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = ["id", "name", "abbrev", "region_tier"]


class CollectionSerializer(serializers.ModelSerializer):
    region = _NestedRegionSerializer(read_only=True)
    region_id = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.all(), source="region", write_only=True,
    )
    editor_count = serializers.SerializerMethodField()

    class Meta:
        model = Collection
        fields = [
            "id",
            "name",
            "description",
            "region",
            "region_id",
            "is_active",
            "editor_count",
            "created_date",
            "modified_date",
        ]
        read_only_fields = ["id", "created_date", "modified_date", "editor_count"]

    def get_editor_count(self, obj):
        return obj.editor_assignments.count()


class CollectionAssignmentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = CollectionAssignment
        fields = [
            "id",
            "user",
            "username",
            "collection",
            "created_date",
        ]
        read_only_fields = ["id", "created_date"]


###################################################################################################
