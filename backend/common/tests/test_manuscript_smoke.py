"""End-to-end smoke of the REAL VPHC manuscript ledger. Phase 6 pre-flight.

The other tests in this directory use three hand-built fixture rows. This one
runs the actual markings produced by `tools/vphc_manuscript_ledger.py` -- 449
West Virginia, 2,931 Virginia -- through `apply_vphc_ledger` and then approves
every one of them through the same `_approve_contribution` path the dashboard
and `tools/vphc_bulk_approve.py` use, in an isolated test database. It is the
closest thing to the live run that can be had without touching production, and
it is worth re-running immediately before the live run.

⚠ It reads its data from the **workspace**, not the repo
(`ian-projects/docs/vphc/manuscripts/`), so it SKIPS wherever that is absent --
CI included. A green CI run is not evidence this passed; run it locally.

⚠ It is also SKIPPED BY DEFAULT, because it takes **511 seconds** and would
otherwise quintuple the runtime of `manage.py test common`. Opt in:

    VPHC_SMOKE=1 uv run python backend/manage.py test \\
        common.tests.test_manuscript_smoke

It found two defects the fixture tests could not:

  * `apply_contribution_to_catalog` does not mint a catalog code -- that
    happens in `_approve_contribution` -- so approving the other way yields
    markings with `code=NULL` and no unique-constraint complaint, because MySQL
    does not collide NULLs in a unique index.
  * `_write_ledger` appended to the shipped `LEDGER.jsonl`, the audit trail of
    the August run. Every Phase 6 rehearsal would have corrupted it. Hence
    `--applied-log`.
"""
import json
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from common.api.v2.views import _approve_contribution
from common.models import (
    Citation, Collection, Color, Contribution, Marking, PostOffice,
    ReferenceWork, Region,
)

VPHC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "docs", "vphc"))
COLOURS = ["BLACK", "BLUE", "RED", "BROWN", "GREEN", "ORANGE", "PURPLE",
           "GRAY", "MAGENTA", "YELLOW"]


class RealManuscriptLedgerSmoke(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("importer", password="x")
        for name, abbrev in (("Virginia", "VA"), ("West Virginia", "WV")):
            region = Region.objects.create(
                code=f"USA-{abbrev}1", name=name, abbrev=abbrev,
                region_tier="STATE", created_by=self.user, modified_by=self.user)
            Collection.objects.create(
                name=name, region=region, is_active=True,
                created_by=self.user, modified_by=self.user)
        ReferenceWork.objects.create(
            code="VPHC1", title="Virginia Postal History Catalog",
            publisher="Virginia Postal History Society", publication_year=1982,
            created_by=self.user, modified_by=self.user)
        for name in COLOURS:
            Color.objects.create(name=name, created_by=self.user,
                                 modified_by=self.user)

    def test_the_real_wv_manuscripts_load_and_approve(self):
        self.load_and_approve("wv")

    def test_the_real_va_manuscripts_load_and_approve(self):
        self.load_and_approve("va")

    def load_and_approve(self, state):
        if not os.environ.get("VPHC_SMOKE"):
            self.skipTest("set VPHC_SMOKE=1 to run (511 s over the real data)")
        ledger_rel = f"manuscripts/ledger-{state}.jsonl"
        path = os.path.join(VPHC_DIR, ledger_rel)
        if not os.path.exists(path):
            self.skipTest(f"not present: {path}")
        lines = [json.loads(l) for l in open(path, encoding="utf-8")]
        log_rel = f"manuscripts/LEDGER-smoketest-{state}.jsonl"
        # This test writes into the workspace, so it takes the workspace back
        # to how it found it -- via addCleanup, so a failing assertion cannot
        # leave the artifact behind.
        self.addCleanup(
            lambda: os.path.exists(os.path.join(VPHC_DIR, log_rel))
            and os.remove(os.path.join(VPHC_DIR, log_rel)))

        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                call_command(
                    "apply_vphc_ledger", vphc_dir=VPHC_DIR, actor=self.user.pk,
                    ledger=ledger_rel,
                    crosswalk=f"manuscripts/crosswalk-{state}.csv",
                    # ⛔ NOT the shipped LEDGER.jsonl -- a rehearsal must never
                    # append to the audit trail of the August run.
                    applied_log=log_rel,
                    only="create", verbosity=1)

                contributions = list(Contribution.objects.all())
                print(f"\n  ledger lines            {len(lines)}")
                print(f"  contributions created   {len(contributions)}")

                skipped = os.path.join(VPHC_DIR, "manuscripts", "skipped.csv")
                if os.path.exists(skipped):
                    print(f"  !! SKIPPED FILE WRITTEN: {skipped}")

                # Every one is a manuscript, and none carries is_irreg.
                self.assertTrue(all(c.submitted_data["is_manuscript"]
                                    for c in contributions))
                self.assertFalse(any("is_irreg" in c.submitted_data
                                     for c in contributions))
                self.assertTrue(all(c.status == Contribution.STATUS_PENDING
                                    for c in contributions))

                # Approve every one through the SAME path the dashboard and
                # vphc_bulk_approve.py use. apply_contribution_to_catalog on
                # its own does NOT mint a catalog code -- final_code_for_
                # contribution runs in _approve_contribution -- so approving
                # the other way silently yields 449 markings with code=NULL.
                for c in contributions:
                    _approve_contribution(c, actor=self.user, review_notes="")
                markings = list(Marking.objects.all())

        self.assertEqual(len(markings), len(contributions))
        print(f"  markings approved       {len(markings)}")
        print(f"  post offices created    {PostOffice.objects.count()}")
        print(f"  citations               {Citation.objects.count()}")

        # marking_manuscript_consistency, on every single row.
        for m in markings:
            self.assertTrue(m.is_manuscript)
            self.assertIsNone(m.shape)
            self.assertIsNone(m.lettering)
            self.assertIsNone(m.is_irreg)

        codes = [m.code for m in markings]
        self.assertEqual(len(codes), len(set(codes)), "duplicate codes minted")
        prefix = f"VPHC1-{state.upper()}-M"
        self.assertTrue(all(c.startswith(prefix) for c in codes),
                        f"unexpected code prefix: {sorted(codes)[:5]}")
        print(f"  codes                   {min(codes)} .. {max(codes)}")

        # Every marking cites the society, with the cancel number as locator.
        self.assertEqual(
            Citation.objects.filter(subject_type="MARKING").count(), len(markings))

        # Nothing leaked the internal notation into public text.
        self.assertFalse([m for m in markings if "[VPHC:" in (m.desc or "")])
        self.assertFalse([m for m in markings if "T1:r" in (m.desc or "")])

        types = {}
        for m in markings:
            types[m.type] = types.get(m.type, 0) + 1
        print(f"  types                   {types}")
        print(f"  with a colour           "
              f"{sum(1 for m in markings if m.color_id)}")
        print(f"  with a date range       "
              f"{sum(1 for m in markings if m.earliest_seen)}")
