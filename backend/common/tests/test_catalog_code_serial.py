"""Catalog serial allocation (`catalog_codes._next_serial`).

The serial is what makes a catalog code permanent and unique, and it is minted
inside the approval path -- so a wrong answer here is a one-way door. These
tests pin the behaviour that a Python-loop-to-SQL-aggregate rewrite could
plausibly change, not every permutation of input.

The load-bearing case is `test_serial_above_padding_width`: serials are
zero-padded to four digits, so text ordering and numeric ordering agree only
below 9,999. Anything that compares the suffix as a string passes every other
test in this file and silently re-issues live codes once a prefix crosses 10k.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from common.catalog_codes import _next_serial
from common.models import Collection, Contribution, Marking, PostOffice, Region

PREFIX = "ASCC6-VA-M"


def _serial(**kw):
    return _next_serial(subject_type="MARKING", prefix=PREFIX, exclude_id=None, **kw)


class NextSerialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("editor", password="x")
        cls.region = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=cls.user, modified_by=cls.user)
        cls.post_office = PostOffice.objects.create(
            name="Richmond", created_by=cls.user, modified_by=cls.user)
        cls.collection = Collection.objects.create(
            name="Virginia", region=cls.region, is_active=True,
            created_by=cls.user, modified_by=cls.user)

    def marking(self, code):
        return Marking.objects.create(
            code=code, type="TOWNMARK", catalog_txt="RICHMOND/VA.",
            inscription_txt="RICHMOND VA", is_manuscript=False, is_irreg=False,
            post_office=self.post_office,
            created_by=self.user, modified_by=self.user)

    def contribution(self, payload, status=Contribution.STATUS_PENDING):
        return Contribution.objects.create(
            contributor=self.user, status=status, submitted_data=payload,
            collection=self.collection, created_by=self.user, modified_by=self.user)

    def test_empty_catalog_starts_at_one(self):
        self.assertEqual(_serial(), 1)

    def test_reads_existing_markings(self):
        self.marking(f"{PREFIX}0007")
        self.marking(f"{PREFIX}0003")
        self.assertEqual(_serial(), 8)

    def test_reads_pending_contributions(self):
        """A queued code is reserved even though no Marking row exists yet."""
        self.contribution({"catalog_code": f"{PREFIX}0042"})
        self.assertEqual(_serial(), 43)

    def test_takes_the_max_across_both_sources(self):
        self.marking(f"{PREFIX}0100")
        self.contribution({"catalog_code": f"{PREFIX}0250"})
        self.assertEqual(_serial(), 251)

    def test_serial_above_padding_width(self):
        """Serials past 9,999 must compare numerically, not as text.

        "10000" < "9999" lexically, so a string comparison returns 10000 here
        and hands out a serial that is already taken.
        """
        self.marking(f"{PREFIX}9999")
        self.marking(f"{PREFIX}10000")
        self.assertEqual(_serial(), 10001)

    def test_ignores_other_prefixes_and_malformed_suffixes(self):
        self.marking("ASCC6-WV-M0900")       # different region
        self.marking("ASCC6-VA-C0800")       # different subject letter
        self.marking(f"{PREFIX}0700A")       # trailing letter
        self.marking(f"{PREFIX}012")         # fewer than four digits
        self.assertEqual(_serial(), 1)

    def test_payload_key_priority(self):
        """catalog_code wins over code, and a blank key falls through.

        code_value_from_payload reads catalog_code, catalogCode, then code, and
        treats whitespace as blank. The SQL has to agree on both counts.
        """
        self.contribution({"catalog_code": f"{PREFIX}0050", "code": f"{PREFIX}0900"})
        self.assertEqual(_serial(), 51)

        self.contribution({"catalog_code": "   ", "code": f"{PREFIX}0060"})
        self.assertEqual(_serial(), 61)

    def test_ignores_approved_and_rejected_contributions(self):
        """Only DRAFT / PENDING / NEEDS_REVISION reserve a code.

        An approved contribution already has its Marking row, which the first
        half counts; counting it twice would be harmless, but a rejected one
        must release its serial.
        """
        self.contribution({"catalog_code": f"{PREFIX}0500"},
                          status=Contribution.STATUS_REJECTED)
        self.assertEqual(_serial(), 1)

    def test_exclusions_release_a_serial(self):
        """Re-suggesting for a row must not collide with that row's own code."""
        marking = self.marking(f"{PREFIX}0080")
        contrib = self.contribution({"catalog_code": f"{PREFIX}0090"})

        self.assertEqual(_serial(), 91)
        self.assertEqual(
            _next_serial(subject_type="MARKING", prefix=PREFIX,
                         exclude_id=marking.pk, exclude_contribution_id=contrib.pk),
            1,
        )
