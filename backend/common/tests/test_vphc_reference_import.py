"""Postmaster/county schema and the VPHC reference importer.

Covers the things that are load-bearing for the ingest and would be silent if
broken: the county tier finally being used, a town belonging to two counties
across time, and the importer being safe to run twice. Deliberately not every
permutation -- the source CSVs are verified upstream by tools/vphc_crossexam.py.
"""
import csv
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.models import (
    PostOffice,
    PostOfficeRegion,
    Postmaster,
    PostmasterTenure,
    Region,
)

T1_FIELDS = ["src", "state", "county", "town", "town_key", "population",
             "is_manuscript"]
T3_FIELDS = ["src", "state", "county", "town", "town_key", "postmaster",
             "event", "appointed_date", "appointed_granularity"]
PO_FIELDS = ["town_key", "town", "state", "county", "population", "needed_for",
             "exists"]


def write(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


class VphcReferenceImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="importer", password="x")
        self.va = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=self.user, modified_by=self.user)
        self.wv = Region.objects.create(
            code="USA-WV1", name="West Virginia", abbrev="WV",
            region_tier="STATE", created_by=self.user, modified_by=self.user)

        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "extract"))
        os.makedirs(os.path.join(self.dir, "crossexam"))
        write(os.path.join(self.dir, "extract", "t1_markings.csv"), T1_FIELDS, [
            {"src": "T1:r4", "state": "VA", "county": "Washington",
             "town": "ABINGDON", "town_key": "ABINGDON", "population": "1306",
             "is_manuscript": ""},
        ])
        write(os.path.join(self.dir, "extract", "t3_postmasters.csv"), T3_FIELDS, [
            {"src": "T3:r15904", "state": "VA", "county": "Washington",
             "town": "ABINGDON", "town_key": "ABINGDON",
             "postmaster": "Gerrard T. Conn", "event": "appointment",
             "appointed_date": "1793-04-25", "appointed_granularity": "DAY"},
            # the same person and date twice -- the source really does this
            {"src": "T3:r27075", "state": "VA", "county": "Washington",
             "town": "ABINGDON", "town_key": "ABINGDON",
             "postmaster": "Gerrard T. Conn", "event": "appointment",
             "appointed_date": "1793-04-25", "appointed_granularity": "DAY"},
            {"src": "T3:r15905", "state": "WV", "county": "Berkeley",
             "town": "MARTINSBURG", "town_key": "MARTINSBURG",
             "postmaster": "John Doe", "event": "discontinued",
             "appointed_date": "", "appointed_granularity": ""},
        ])
        write(os.path.join(self.dir, "crossexam", "post_offices_to_create.csv"),
              PO_FIELDS, [
            {"town_key": "ABINGDON", "town": "ABINGDON", "state": "VA",
             "county": "Washington", "population": "1306",
             "needed_for": "markings;postmasters", "exists": ""},
            {"town_key": "MARTINSBURG", "town": "MARTINSBURG", "state": "WV",
             "county": "Berkeley", "population": "",
             "needed_for": "postmasters", "exists": ""},
        ])

    def run_import(self, **kw):
        call_command("import_vphc_reference", vphc_dir=self.dir,
                     actor=self.user.pk, verbosity=0, **kw)

    def test_loads_counties_post_offices_and_tenures(self):
        self.run_import()

        counties = Region.objects.filter(region_tier="COUNTY")
        self.assertEqual(counties.count(), 2)
        washington = counties.get(code="USA-VA1-C-WASHINGTON")
        self.assertEqual(washington.parent_region, self.va)
        self.assertEqual(washington.name, "Washington")

        abingdon = PostOffice.objects.get(name="Abingdon")
        self.assertEqual(abingdon.population, 1306)
        self.assertEqual(abingdon.code, "USA-VA1-1")
        # both the state and the county, which is the point of the county tier
        self.assertEqual(
            sorted(r.region.region_tier
                   for r in abingdon.post_office_regions.all()),
            ["COUNTY", "STATE"])

        self.assertEqual(Postmaster.objects.count(), 2)
        self.assertEqual(Postmaster.objects.get(name="Gerrard T. Conn").sort_name,
                         "Conn, Gerrard T.")
        # three source rows, but two are the same appointment
        self.assertEqual(PostmasterTenure.objects.count(), 2)

        closure = PostmasterTenure.objects.get(event="discontinued")
        self.assertIsNone(closure.date_appointed)
        self.assertEqual(closure.source_ref, "T3:r15905")

        # Martinsburg exists only in the postmaster table. Most new towns do,
        # so a county has to be readable from there too.
        martinsburg = PostOffice.objects.get(name="Martinsburg")
        self.assertEqual(
            sorted(r.region.region_tier
                   for r in martinsburg.post_office_regions.all()),
            ["COUNTY", "STATE"])

    def test_running_twice_changes_nothing(self):
        self.run_import()
        before = (Region.objects.count(), PostOffice.objects.count(),
                  Postmaster.objects.count(), PostmasterTenure.objects.count())
        self.run_import()
        after = (Region.objects.count(), PostOffice.objects.count(),
                 Postmaster.objects.count(), PostmasterTenure.objects.count())
        self.assertEqual(before, after)

    def test_dry_run_writes_nothing(self):
        self.run_import(dry_run=True)
        self.assertEqual(PostOffice.objects.count(), 0)
        self.assertEqual(PostmasterTenure.objects.count(), 0)
        self.assertFalse(Region.objects.filter(region_tier="COUNTY").exists())

    def test_post_office_without_a_state_is_refused(self):
        """Rule P1: never invent a home for a town that has none."""
        write(os.path.join(self.dir, "crossexam", "post_offices_to_create.csv"),
              PO_FIELDS, [
            {"town_key": "POTOMACSTEAMBOAT", "town": "POTOMAC STEAMBOAT",
             "state": "", "county": "", "population": "",
             "needed_for": "markings", "exists": ""},
        ])
        with self.assertRaises(CommandError) as ctx:
            self.run_import()
        self.assertIn("quarantined", str(ctx.exception))


class PostOfficeRegionTemporalTests(TestCase):
    """A town can leave a county that still exists -- the case the junction
    could not express before valid_from/valid_to."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="editor", password="x")
        self.culpeper = Region.objects.create(
            code="USA-VA1-C-CULPEPER", name="Culpeper", abbrev="VA",
            region_tier="COUNTY", created_by=self.user, modified_by=self.user)
        self.office = PostOffice.objects.create(
            code="USA-VA1-9001", name="Washington",
            created_by=self.user, modified_by=self.user)

    def test_two_spans_in_the_same_county(self):
        for start in ("1800-01-01", "1840-01-01"):
            PostOfficeRegion.objects.create(
                post_office=self.office, region=self.culpeper,
                valid_from=start, created_by=self.user, modified_by=self.user)
        self.assertEqual(self.office.post_office_regions.count(), 2)

    def test_the_same_span_twice_is_rejected(self):
        PostOfficeRegion.objects.create(
            post_office=self.office, region=self.culpeper,
            valid_from="1833-02-08", created_by=self.user, modified_by=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PostOfficeRegion.objects.create(
                    post_office=self.office, region=self.culpeper,
                    valid_from="1833-02-08",
                    created_by=self.user, modified_by=self.user)
