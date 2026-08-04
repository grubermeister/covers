"""
Seed a volume-test database by clone-amplifying real catalog data (issue #59).

Workflow (never against a real catalog DB -- see the guard below):

    DB_NAME=worldcovers_voltest ./woco migrate
    DB_NAME=worldcovers_voltest ./woco shell -c "..."          # create user id=1
    DB_NAME=worldcovers_voltest ./woco ascc import tools/wip/cutover --truncate
    DB_NAME=worldcovers_voltest ./woco seed_volume_test --target-count 100000

Each "generation" clones the entire imported base set -- markings with their
DateSeen and Citation children, plus a cloned copy of every PostOffice and its
region links -- so per-marking child ratios, catalog_txt duplication, and
town/region fan-out all scale together the way real state imports would.
Covers do not come from ASCC bundles, so a configurable fraction of markings
gets a synthesized Cover + CoverMarking + COVER DateSeen, exercising the
cover-mediated arm of MarkingQuerySet.with_date_range().

Inserts use bulk_create and deliberately bypass the audit stack
(SubmissionTransaction / MarkingVersion / reversion): this seeder exists to
measure READ scaling, not write amplification.
"""
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Max

from common.models import (
    Citation,
    Cover,
    CoverMarking,
    DateSeen,
    Marking,
    PostOffice,
    PostOfficeRegion,
)

# Never amplify a real catalog database. The importer's per-state siblings are
# protected too; only names containing "voltest" (or an explicit override) run.
PROTECTED_DB_HINT = "voltest"


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Command(BaseCommand):
    help = "Clone-amplify the imported catalog to --target-count markings for volume testing."

    def add_arguments(self, parser):
        parser.add_argument("--target-count", type=int, required=True,
                            help="Total Marking rows to reach (e.g. 100000).")
        parser.add_argument("--batch-size", type=int, default=2000)
        parser.add_argument("--covers-percent", type=float, default=5.0,
                            help="Synthesize covers for this percent of markings (default 5).")
        parser.add_argument("--allow-db", default="",
                            help="Exact DB name to allow when it does not contain 'voltest'.")

    def handle(self, *args, **opts):
        db_name = connection.settings_dict["NAME"]
        if PROTECTED_DB_HINT not in db_name and opts["allow_db"] != db_name:
            raise CommandError(
                f"Refusing to run against '{db_name}'. Use a dedicated volume-test DB "
                f"(name containing '{PROTECTED_DB_HINT}') or pass --allow-db {db_name}."
            )

        user = get_user_model().objects.order_by("id").first()
        if user is None:
            raise CommandError("No user exists; create one first (the importer needs id=1 anyway).")

        batch = opts["batch_size"]
        target = opts["target_count"]

        base = list(
            Marking.all_objects.order_by("id").values(
                "id", "type", "catalog_txt", "inscription_txt", "desc", "is_manuscript",
                "shape_id", "lettering_id", "color_id", "is_irreg", "width", "height",
                "date_fmt", "impression", "rate_val", "post_office_id",
            )
        )
        if not base:
            raise CommandError("Target DB has no markings; import a bundle first.")

        current = len(base)
        base_ids = [m["id"] for m in base]

        ds_map = defaultdict(list)
        for row in DateSeen.objects.filter(
            subject_type="MARKING", subject_id__in=base_ids
        ).values("subject_id", "date", "granularity"):
            ds_map[row["subject_id"]].append(row)

        cit_map = defaultdict(list)
        for row in Citation.objects.filter(
            subject_type="MARKING", subject_id__in=base_ids
        ).values("subject_id", "reference_work_id", "citation_detail"):
            cit_map[row["subject_id"]].append(row)

        base_pos = list(PostOffice.objects.order_by("id").values("id", "name"))
        po_region_map = defaultdict(list)
        for row in PostOfficeRegion.objects.filter(
            post_office_id__in=[p["id"] for p in base_pos]
        ).values("post_office_id", "region_id"):
            po_region_map[row["post_office_id"]].append(row["region_id"])

        audit = {"created_by_id": user.id, "modified_by_id": user.id}
        generation = 0
        self.stdout.write(f"Base: {len(base)} markings, {len(base_pos)} post offices. "
                          f"Current total {current}, target {target}.")

        while current < target:
            generation += 1
            remaining = target - current
            subset = base if remaining >= len(base) else base[:remaining]

            po_id_map = self._clone_post_offices(base_pos, po_region_map, generation, audit, batch)

            for chunk in _chunks(subset, batch):
                with transaction.atomic():
                    new_ids = self._insert_returning_ids(
                        Marking.all_objects,
                        [
                            Marking(
                                code=None,
                                type=m["type"],
                                catalog_txt=m["catalog_txt"],
                                inscription_txt=m["inscription_txt"],
                                desc=m["desc"],
                                is_manuscript=m["is_manuscript"],
                                shape_id=m["shape_id"],
                                lettering_id=m["lettering_id"],
                                color_id=m["color_id"],
                                is_irreg=m["is_irreg"],
                                width=m["width"],
                                height=m["height"],
                                date_fmt=m["date_fmt"],
                                impression=m["impression"],
                                rate_val=m["rate_val"],
                                post_office_id=po_id_map[m["post_office_id"]],
                                **audit,
                            )
                            for m in chunk
                        ],
                        batch,
                    )
                    ds_objs, cit_objs = [], []
                    for m, new_id in zip(chunk, new_ids):
                        for d in ds_map.get(m["id"], ()):
                            ds_objs.append(DateSeen(
                                subject_type="MARKING", subject_id=new_id,
                                date=d["date"], granularity=d["granularity"], **audit,
                            ))
                        for c in cit_map.get(m["id"], ()):
                            cit_objs.append(Citation(
                                subject_type="MARKING", subject_id=new_id,
                                reference_work_id=c["reference_work_id"],
                                citation_detail=c["citation_detail"], **audit,
                            ))
                    DateSeen.objects.bulk_create(ds_objs, batch_size=batch)
                    Citation.objects.bulk_create(cit_objs, batch_size=batch)
                current += len(chunk)
            self.stdout.write(f"Generation {generation}: total now {current} markings.")

        self._synthesize_covers(opts["covers_percent"], audit, batch)
        self.stdout.write(self.style.SUCCESS(
            f"Done: {Marking.all_objects.count()} markings, "
            f"{Cover.all_objects.count()} covers, "
            f"{DateSeen.objects.count()} dates_seen, "
            f"{Citation.objects.count()} citations, "
            f"{PostOffice.objects.count()} post offices in {db_name}."
        ))

    def _clone_post_offices(self, base_pos, po_region_map, generation, audit, batch):
        """Clone every base PostOffice for this generation; returns old id -> new id."""
        with transaction.atomic():
            new_ids = self._insert_returning_ids(
                PostOffice.objects,
                [PostOffice(code=None, name=f"{p['name']} VT{generation}", **audit)
                 for p in base_pos],
                batch,
            )
            po_id_map = {p["id"]: new_id for p, new_id in zip(base_pos, new_ids)}
            PostOfficeRegion.objects.bulk_create(
                [
                    PostOfficeRegion(post_office_id=po_id_map[old_id], region_id=region_id, **audit)
                    for old_id, region_ids in po_region_map.items()
                    for region_id in region_ids
                ],
                batch_size=batch,
            )
        return po_id_map

    def _synthesize_covers(self, covers_percent, audit, batch):
        """Give covers_percent of markings a Cover + CoverMarking + COVER DateSeen."""
        total_markings = Marking.all_objects.count()
        want = int(total_markings * covers_percent / 100.0)
        have = Cover.all_objects.count()
        if want <= have:
            self.stdout.write(f"Covers: have {have}, want {want} -- skipping.")
            return
        need = want - have
        stride = max(1, total_markings // need)
        marking_ids = list(
            Marking.all_objects.order_by("id").values_list("id", flat=True)[::stride][:need]
        )
        covered = set(
            CoverMarking.objects.filter(marking_id__in=marking_ids)
            .values_list("marking_id", flat=True)
        )
        marking_ids = [m for m in marking_ids if m not in covered]

        date_by_marking = {}
        for row in DateSeen.objects.filter(
            subject_type="MARKING", subject_id__in=marking_ids
        ).order_by("subject_id", "date").values("subject_id", "date"):
            date_by_marking.setdefault(row["subject_id"], row["date"])

        for chunk in _chunks(marking_ids, batch):
            with transaction.atomic():
                cover_ids = self._insert_returning_ids(
                    Cover.all_objects,
                    [Cover(code=None, has_adhesive=False, **audit) for _ in chunk],
                    batch,
                )
                CoverMarking.objects.bulk_create(
                    [CoverMarking(cover_id=c, marking_id=m, **audit)
                     for c, m in zip(cover_ids, chunk)],
                    batch_size=batch,
                )
                DateSeen.objects.bulk_create(
                    [
                        DateSeen(subject_type="COVER", subject_id=c,
                                 date=date_by_marking[m], granularity="DAY", **audit)
                        for c, m in zip(cover_ids, chunk)
                        if m in date_by_marking
                    ],
                    batch_size=batch,
                )
        self.stdout.write(f"Covers: synthesized {len(marking_ids)} (target {want}).")

    @staticmethod
    def _insert_returning_ids(manager, objs, batch):
        """
        bulk_create then read back the new ids. MySQL has no RETURNING, but on a
        single connection with the default innodb_autoinc_lock_mode the ids of a
        bulk insert are consecutive and in insert order, so id > pre-insert max
        ordered by id maps 1:1 onto objs. Asserted, since the seeder is the only
        writer on a volume-test DB.
        """
        pre_max = manager.aggregate(m=Max("id"))["m"] or 0
        manager.bulk_create(objs, batch_size=batch)
        new_ids = list(
            manager.filter(id__gt=pre_max).order_by("id").values_list("id", flat=True)
        )
        if len(new_ids) != len(objs):
            raise CommandError(
                f"Expected {len(objs)} new rows, found {len(new_ids)} -- "
                "is something else writing to this DB?"
            )
        return new_ids
