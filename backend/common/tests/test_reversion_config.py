from datetime import timedelta

import reversion
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from reversion.admin import VersionAdmin
from reversion.errors import RegistrationError
from reversion.models import Revision, Version

from common.models import (
    Color,
    Cover,
    CoverRecycleBin,
    CoverVersion,
    Marking,
    MarkingRecycleBin,
    MarkingVersion,
    PostOffice,
    Region,
    SubmissionTransaction,
)


User = get_user_model()


class ReversionConfigTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self.color = Color.objects.create(
            name="Black",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.region = Region.objects.create(
            name="Virginia",
            abbrev="VA",
            region_tier="STATE",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.post_office = PostOffice.objects.create(
            name="Richmond",
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.marking = Marking.objects.create(
            type="TOWNMARK",
            inscription_txt="RICHMOND VA",
            is_manuscript=True,
            color=self.color,
            post_office=self.post_office,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_excluded_models_not_registered(self):
        for model in (
            SubmissionTransaction,
            MarkingVersion,
            CoverVersion,
            MarkingRecycleBin,
            CoverRecycleBin,
        ):
            self.assertFalse(reversion.is_registered(model), model.__name__)

        for model in (Marking, Cover, Region):
            self.assertTrue(reversion.is_registered(model), model.__name__)

    def test_marking_admin_is_version_admin(self):
        self.assertIsInstance(admin.site._registry[Marking], VersionAdmin)

    def test_marking_version_admin_is_read_only(self):
        request = self.factory.get("/")
        request.user = self.admin
        model_admin = admin.site._registry[MarkingVersion]
        readonly = set(model_admin.get_readonly_fields(request))
        concrete_fields = {field.name for field in MarkingVersion._meta.fields}

        self.assertEqual(readonly, concrete_fields)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertTrue(model_admin.has_view_permission(request))

    def test_admin_save_creates_version(self):
        self.client.force_login(self.admin)
        before = Version.objects.get_for_model(Marking).count()

        response = self.client.post(
            reverse("admin:common_marking_change", args=[self.marking.pk]),
            {
                "code": "M-1",
                "type": "TOWNMARK",
                "post_office": str(self.post_office.pk),
                "shape": "",
                "lettering": "",
                "color": str(self.color.pk),
                "is_manuscript": "on",
                "impression": "",
                "is_irreg": "",
                "width": "",
                "height": "",
                "date_fmt": "",
                "rate_val": "",
                "catalog_txt": "",
                "inscription_txt": "RICHMOND VA UPDATED",
                "desc": "",
                "cover_markings-TOTAL_FORMS": "0",
                "cover_markings-INITIAL_FORMS": "0",
                "cover_markings-MIN_NUM_FORMS": "0",
                "cover_markings-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertGreater(Version.objects.get_for_model(Marking).count(), before)

        history_response = self.client.get(
            reverse("admin:common_marking_history", args=[self.marking.pk])
        )
        self.assertEqual(history_response.status_code, 200)

    def test_prune_purges_legacy_versions(self):
        self._create_marking_revision("RICHMOND VA BEFORE")
        self._create_legacy_marking_version()

        call_command("prune_revisions", verbosity=0)

        self.assertEqual(self._version_count_for_unregistered(MarkingVersion), 0)
        self.assertGreater(Version.objects.get_for_model(Marking).count(), 0)
        self.assertFalse(Revision.objects.filter(version__isnull=True).exists())

    def test_prune_dry_run_deletes_nothing(self):
        self._create_marking_revision("RICHMOND VA BEFORE")
        self._create_legacy_marking_version()
        counts = self._revision_counts()

        call_command("prune_revisions", dry_run=True, verbosity=0)

        self.assertEqual(self._revision_counts(), counts)

    def test_prune_keeps_per_object_versions_in_shared_revision(self):
        old_date = timezone.now() - timedelta(days=365)
        for index in range(5):
            revision = self._create_marking_revision(f"RICHMOND VA {index}")
            Revision.objects.filter(pk=revision.pk).update(date_created=old_date)

        shared_revision = self._create_shared_marking_region_revision()
        Revision.objects.filter(pk=shared_revision.pk).update(date_created=old_date)

        call_command("prune_revisions", days=180, keep=3, verbosity=0)

        self.assertEqual(Version.objects.get_for_model(Marking).count(), 3)
        self.assertEqual(Version.objects.get_for_model(Region).count(), 1)
        self.assertFalse(Revision.objects.filter(version__isnull=True).exists())

    def _create_marking_revision(self, inscription):
        with reversion.create_revision():
            self.marking.inscription_txt = inscription
            self.marking.save()
        return Revision.objects.latest("pk")

    def _create_shared_marking_region_revision(self):
        with reversion.create_revision():
            self.marking.inscription_txt = "RICHMOND VA SHARED"
            self.marking.save()
            self.region.abbrev = "VAA"
            self.region.save()
        return Revision.objects.latest("pk")

    def _create_legacy_marking_version(self):
        registered_here = False
        if not reversion.is_registered(MarkingVersion):
            reversion.register(MarkingVersion)
            registered_here = True
        try:
            with reversion.create_revision():
                MarkingVersion.objects.create(
                    marking=self.marking,
                    version_no=1,
                    snapshot={"code": self.marking.code},
                    created_by=self.admin,
                )
            return Revision.objects.latest("pk")
        finally:
            if registered_here:
                try:
                    reversion.unregister(MarkingVersion)
                except RegistrationError:
                    pass

    def _revision_counts(self):
        return {
            "marking": Version.objects.get_for_model(Marking).count(),
            "marking_version": self._version_count_for_unregistered(MarkingVersion),
            "revision": Revision.objects.count(),
            "empty_revision": Revision.objects.filter(version__isnull=True).count(),
        }

    def _version_count_for_unregistered(self, model):
        content_type = ContentType.objects.get_for_model(
            model,
            for_concrete_model=True,
        )
        return Version.objects.filter(content_type=content_type).count()
