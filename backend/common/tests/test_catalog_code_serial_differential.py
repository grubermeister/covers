"""Differential test: the SQL serial aggregate against the Python it replaced.

test_catalog_code_serial.py pins nine hand-picked cases, and every one of them
passes against the old implementation too -- so on its own it cannot show the
rewrite is faithful, only that it is not obviously wrong.

This carries the pre-rewrite algorithm as a reference oracle and asserts the two
agree over randomised data, including the shapes a hand-written case tends to
miss: codes near the zero-padding boundary, mixed-case prefixes, whitespace-only
payload keys, non-string JSON values, and keys that disagree with each other.

The oracle is a verbatim copy of origin/staging@4edcaba's `_next_serial` body.
It must not be "improved" -- its bugs, if any, are the behaviour under test.
"""
import random
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from common.catalog_codes import _next_serial, code_value_from_payload
from common.models import Collection, Contribution, Marking, PostOffice, Region

PREFIX = "ASCC6-VA-M"
_CODE_NUMBER_RE = re.compile(r"^(\d{4,})$")


def oracle_next_serial(*, subject_type, prefix, exclude_id=None,
                       exclude_contribution_id=None):
    """origin/staging's implementation, kept verbatim as the reference."""
    max_seen = 0
    qs = Marking.all_objects.filter(code__startswith=prefix).only("id", "code")
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    for row in qs:
        suffix = (row.code or "")[len(prefix):]
        match = _CODE_NUMBER_RE.match(suffix)
        if match is None:
            continue
        max_seen = max(max_seen, int(match.group(1)))

    pending_qs = Contribution.objects.filter(
        status__in=(
            Contribution.STATUS_DRAFT,
            Contribution.STATUS_PENDING,
            Contribution.STATUS_NEEDS_REVISION,
        ),
    ).only("id", "submitted_data")
    if exclude_contribution_id is not None:
        pending_qs = pending_qs.exclude(pk=exclude_contribution_id)
    for contrib_row in pending_qs:
        code = code_value_from_payload(dict(contrib_row.submitted_data or {}))
        if not code or not code.startswith(prefix):
            continue
        suffix = code[len(prefix):]
        match = _CODE_NUMBER_RE.match(suffix)
        if match is None:
            continue
        max_seen = max(max_seen, int(match.group(1)))

    return max_seen + 1


# Suffixes chosen to sit on the edges: the padding boundary, the 10k crossing
# where text and numeric order diverge, too-short runs, and non-digit tails.
SUFFIXES = [
    "0001", "0009", "0010", "0099", "0100", "0999", "1000",
    "9998", "9999", "10000", "10001", "99999",
    "1", "12", "123",            # shorter than four digits -- ignored
    "0100A", "12x4", "", "abcd",  # not all digits -- ignored
    "00001234",                   # leading zeros beyond the pad width
]

OTHER_PREFIXES = ["ASCC6-WV-M", "ASCC6-VA-C", "APMC-VA-M", "ascc6-va-m"]


class SerialDifferentialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("editor", password="x")
        region = Region.objects.create(
            code="USA-VA1", name="Virginia", abbrev="VA", region_tier="STATE",
            created_by=cls.user, modified_by=cls.user)
        cls.post_office = PostOffice.objects.create(
            name="Richmond", created_by=cls.user, modified_by=cls.user)
        cls.collection = Collection.objects.create(
            name="Virginia", region=region, is_active=True,
            created_by=cls.user, modified_by=cls.user)

    def _marking(self, code):
        return Marking.objects.create(
            code=code, type="TOWNMARK", catalog_txt="RICHMOND/VA.",
            inscription_txt="RICHMOND VA", is_manuscript=False, is_irreg=False,
            post_office=self.post_office,
            created_by=self.user, modified_by=self.user)

    def _contribution(self, payload, status=Contribution.STATUS_PENDING):
        return Contribution.objects.create(
            contributor=self.user, collection=self.collection, status=status,
            submitted_data=payload, created_by=self.user, modified_by=self.user)

    def _assert_agrees(self, label, **kw):
        kw.setdefault("exclude_id", None)
        expected = oracle_next_serial(subject_type="MARKING", prefix=PREFIX, **kw)
        actual = _next_serial(subject_type="MARKING", prefix=PREFIX, **kw)
        self.assertEqual(
            actual, expected,
            "{}: SQL returned {} but the Python oracle returned {}".format(
                label, actual, expected))
        return actual

    def test_agrees_on_randomised_populations(self):
        """50 randomised populations, rebuilt from scratch each round."""
        rng = random.Random(20260819)
        statuses = [
            Contribution.STATUS_DRAFT, Contribution.STATUS_PENDING,
            Contribution.STATUS_NEEDS_REVISION, Contribution.STATUS_APPROVED,
            Contribution.STATUS_REJECTED,
        ]
        code_keys = ["catalog_code", "catalogCode", "code"]
        blanks = ["", "   ", "\t", None]

        for round_no in range(50):
            Marking.all_objects.all().delete()
            Contribution.objects.all().delete()

            used = set()
            for _ in range(rng.randint(0, 8)):
                prefix = rng.choice([PREFIX] + OTHER_PREFIXES)
                code = prefix + rng.choice(SUFFIXES)
                # MySQL's unique index on `code` is case-insensitive, so
                # "ASCC6-VA-M" and "ascc6-va-m" collide. Dedupe the way the
                # database does, not the way Python would.
                if not code or code.lower() in used:
                    continue
                used.add(code.lower())
                self._marking(code)

            for _ in range(rng.randint(0, 8)):
                payload = {}
                # Populate a random subset of the three keys, sometimes with a
                # blank that must fall through to the next one.
                for key in rng.sample(code_keys, rng.randint(0, 3)):
                    if rng.random() < 0.25:
                        payload[key] = rng.choice(blanks)
                    else:
                        payload[key] = rng.choice([PREFIX] + OTHER_PREFIXES) \
                            + rng.choice(SUFFIXES)
                if rng.random() < 0.1:
                    payload["catalog_code"] = rng.randint(1000, 99999)  # not a str
                self._contribution(payload, status=rng.choice(statuses))

            self._assert_agrees("round {}".format(round_no))

    def test_agrees_with_exclusions(self):
        """The exclude_* arguments are how a row avoids colliding with itself."""
        marking = self._marking(PREFIX + "0400")
        contrib = self._contribution({"catalog_code": PREFIX + "0500"})
        self._marking(PREFIX + "0300")

        self._assert_agrees("no exclusions")
        self._assert_agrees("exclude marking", exclude_id=marking.pk)
        self._assert_agrees("exclude contribution",
                            exclude_contribution_id=contrib.pk)
        self._assert_agrees("exclude both", exclude_id=marking.pk,
                            exclude_contribution_id=contrib.pk)

    def test_agrees_on_empty_population(self):
        self.assertEqual(self._assert_agrees("empty"), 1)
