"""
Migrate Contribution onto the TimestampedModel abstract base.

Renames created_at -> created_date, updated_at -> modified_date, and adds
created_by / modified_by FKs to AUTH_USER_MODEL. Backfill rules:

    created_by_id  = contributor_id
    modified_by_id = COALESCE(reviewer_id, contributor_id)

The contributor/reviewer FKs are workflow roles (who authored the submission,
who reviewed it) and are preserved as-is. created_by/modified_by are the
infrastructural "which user performed this DB write" fields contributed by
TimestampedModel.
"""

from django.conf import settings
from django.db import migrations, models


def backfill_contribution_audit_users(apps, schema_editor):
    Contribution = apps.get_model("common", "Contribution")
    # Strip default ordering: at this point in the migration the historical
    # Meta still references the old created_at field name (AlterModelOptions
    # runs after this RunPython), which would otherwise raise FieldError.
    rows = Contribution.objects.order_by().only("id", "contributor_id", "reviewer_id")
    for c in rows:
        c.created_by_id = c.contributor_id
        c.modified_by_id = c.reviewer_id or c.contributor_id
        c.save(update_fields=["created_by_id", "modified_by_id"])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name="contribution",
            old_name="created_at",
            new_name="created_date",
        ),
        migrations.RenameField(
            model_name="contribution",
            old_name="updated_at",
            new_name="modified_date",
        ),
        migrations.AddField(
            model_name="contribution",
            name="created_by",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="%(class)s_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="modified_by",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="%(class)s_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_contribution_audit_users, noop_reverse),
        migrations.AlterField(
            model_name="contribution",
            name="created_by",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="%(class)s_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="contribution",
            name="modified_by",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="%(class)s_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name="contribution",
            options={
                "ordering": ["-created_date"],
                "permissions": [
                    ("review_contribution", "Can review (approve / reject) contributions"),
                ],
                "verbose_name": "Contribution",
                "verbose_name_plural": "Contributions",
            },
        ),
    ]
