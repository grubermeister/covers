###################################################################################################
## WoCo Commons - Resource classes for Auth
## MPC: 2025/10/24
###################################################################################################
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget

from allauth.account.models import EmailAddress

from common.models import Collection, CollectionAssignment, Region



User = get_user_model()
AUTH_BACKUP_SCHEMA = "worldcovers.auth_backup.v1"


def _fallback_audit_user():
    return (
        User.objects.filter(is_superuser=True).order_by("pk").first()
        or User.objects.order_by("pk").first()
    )


class ExportOnlyIdMixin:
    id = fields.Field(column_name="id", attribute="id", readonly=True)

    def before_import_row(self, row, **kwargs):
        row.pop("id", None)
        super().before_import_row(row, **kwargs)


class TimestampedRestoreMixin:
    def before_save_instance(self, instance, row, **kwargs):
        now = timezone.now()
        actor = _fallback_audit_user()
        if actor is None:
            raise ValueError(
                f"Cannot restore {instance.__class__.__name__}: no user exists "
                "for required audit fields."
            )

        if getattr(instance, "created_date", None) is None:
            instance.created_date = now
        instance.modified_date = now

        if getattr(instance, "created_by_id", None) is None:
            instance.created_by = actor
        instance.modified_by = actor

        super().before_save_instance(instance, row, **kwargs)


class UserResource(ExportOnlyIdMixin, resources.ModelResource):
    groups = fields.Field(
        column_name="groups",
        attribute="groups",
        widget=ManyToManyWidget(Group, field="name", separator=";"),
    )
    user_permissions = fields.Field(
        column_name="user_permissions",
        attribute="user_permissions",
        widget=ManyToManyWidget(Permission, field="codename", separator=";"),
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "password",      # hashed password
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "groups",
            "user_permissions",
        )
        import_id_fields = ("username",)


class GroupResource(ExportOnlyIdMixin, resources.ModelResource):
    permissions = fields.Field(
        column_name="permissions",
        attribute="permissions",
        widget=ManyToManyWidget(
            Permission,
            field="codename",   # will use codename strings like "add_user"
            separator=";",
        ),
    )

    class Meta:
        model = Group
        fields = ("id", "name", "permissions")
        import_id_fields = ("name",)



class EmailAddressResource(ExportOnlyIdMixin, resources.ModelResource):
    """
    Backup/restore for django-allauth email addresses.
    We link to users by username for portability.
    """

    user = fields.Field(
        column_name="user",
        attribute="user",
        widget=ForeignKeyWidget(User, "username"),  # map by username
    )

    class Meta:
        model = EmailAddress
        fields = (
            "id",
            "user",
            "email",
            "verified",
            "primary",
        )
        import_id_fields = ("user", "email")


class CollectionResource(TimestampedRestoreMixin, ExportOnlyIdMixin, resources.ModelResource):
    """
    Backup/restore for state Collections. Region is linked by name for
    portability across environments. Regions are core seed data and are
    expected to exist on the destination before restore.
    """

    region = fields.Field(
        column_name="region",
        attribute="region",
        widget=ForeignKeyWidget(Region, "name"),
    )

    class Meta:
        model = Collection
        fields = ("id", "name", "description", "region", "is_active")
        import_id_fields = ("name",)

    def before_save_instance(self, instance, row, **kwargs):
        if instance.pk:
            existing = Collection.objects.only("region_id").get(pk=instance.pk)
            instance.region_id = existing.region_id
        super().before_save_instance(instance, row, **kwargs)

    def skip_row(self, instance, original, row, import_validation_errors=None):
        if (
            instance.pk is None
            and getattr(instance, "region_id", None)
            and Collection.objects.filter(region_id=instance.region_id).exists()
        ):
            return True
        return super().skip_row(
            instance,
            original,
            row,
            import_validation_errors=import_validation_errors,
        )


class CollectionAssignmentResource(
    TimestampedRestoreMixin,
    ExportOnlyIdMixin,
    resources.ModelResource,
):
    """
    Backup/restore for editor -> Collection assignments. We link users by
    username and Collections by name so the file is portable across
    environments where PKs may differ. import_id_fields uses the natural
    unique key (user, collection) -- matching the model's unique_together --
    so re-running restore is idempotent.
    """

    user = fields.Field(
        column_name="user",
        attribute="user",
        widget=ForeignKeyWidget(User, "username"),
    )
    collection = fields.Field(
        column_name="collection",
        attribute="collection",
        widget=ForeignKeyWidget(Collection, "name"),
    )

    class Meta:
        model = CollectionAssignment
        fields = ("id", "user", "collection")
        import_id_fields = ("user", "collection")


class AuthDatasetSpec:
    def __init__(self, name, filename, label, resource_class, required=False):
        self.name = name
        self.filename = filename
        self.label = label
        self.resource_class = resource_class
        self.required = required


AUTH_DATASET_SPECS = (
    AuthDatasetSpec("groups", "groups.csv", "groups", GroupResource),
    AuthDatasetSpec("users", "users.csv", "users", UserResource, required=True),
    AuthDatasetSpec("emails", "emails.csv", "email addresses", EmailAddressResource),
    AuthDatasetSpec(
        "collections",
        "collections.csv",
        "state collections",
        CollectionResource,
    ),
    AuthDatasetSpec(
        "assignments",
        "assignments.csv",
        "collection assignments",
        CollectionAssignmentResource,
    ),
)


def auth_dataset_specs():
    return AUTH_DATASET_SPECS

###################################################################################################
