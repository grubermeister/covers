###################################################################################################
## WoCo Commons - Media integrity check
## Reese: 2026/08/15
##
## Answers the question a database backup cannot: does every Image row actually
## resolve to a file on disk, and is that file the one we recorded?
##
## MEDIA_ROOT is a directory on disk (settings.py: MEDIA_ROOT = BASE_DIR/"media"),
## so a SQL dump contains the `images` rows but none of the bytes they point at.
## Restoring only the database yields a complete-looking catalog in which every
## image link is broken. This command is what turns "we copied 1.3 GB and the
## byte totals matched" into "every image row in this backup resolves to a file
## with the right sha256" -- run it against a restored media tree to rehearse the
## half of the backup that a DB restore never exercises.
##
## Also useful against LIVE media on a schedule: it catches drift the moment it
## happens rather than at the worst possible time.
###################################################################################################
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.models import Image

CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class Command(BaseCommand):
    help = (
        "Verify that every Image row resolves to a file under MEDIA_ROOT, and "
        "optionally that each file's sha256 matches the recorded checksum."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--media-root",
            default=None,
            help="Directory to check instead of settings.MEDIA_ROOT (e.g. a restored snapshot).",
        )
        parser.add_argument(
            "--check-checksums",
            action="store_true",
            help="Also hash every file and compare against Image.file_checksum. Slower.",
        )
        parser.add_argument(
            "--json",
            dest="json_out",
            default=None,
            help="Write the full report as JSON to this path.",
        )
        parser.add_argument(
            "--fail-on-orphans",
            action="store_true",
            help="Exit non-zero if files exist on disk with no Image row (off by default).",
        )

    def handle(self, *args, **options):
        media_root = Path(options["media_root"] or settings.MEDIA_ROOT)
        if not media_root.is_dir():
            raise CommandError(f"Not a directory: {media_root}")

        check_sums = options["check_checksums"]

        missing, corrupt, size_drift = [], [], []
        # storage_filename is deliberately NOT unique -- one file on disk can be
        # referenced by several Image rows (the ASCC colour fan-out produces
        # exactly this). So group by filename and hash each file once.
        by_file: dict[str, list[Image]] = {}
        for img in Image.objects.all().only(
            "image_id", "storage_filename", "file_checksum", "file_size_bytes"
        ):
            by_file.setdefault(img.storage_filename, []).append(img)

        total_rows = sum(len(v) for v in by_file.values())
        self.stdout.write(
            f"{total_rows} image rows referencing {len(by_file)} distinct files under {media_root}"
        )

        for storage_filename, rows in sorted(by_file.items()):
            path = media_root / storage_filename
            if not path.is_file():
                missing.append(
                    {
                        "storage_filename": storage_filename,
                        "image_ids": [r.image_id for r in rows],
                    }
                )
                continue

            actual_size = path.stat().st_size
            for row in rows:
                if row.file_size_bytes and row.file_size_bytes != actual_size:
                    size_drift.append(
                        {
                            "storage_filename": storage_filename,
                            "image_id": row.image_id,
                            "recorded": row.file_size_bytes,
                            "actual": actual_size,
                        }
                    )

            if check_sums:
                digest = _sha256(path)
                for row in rows:
                    if row.file_checksum and row.file_checksum != digest:
                        corrupt.append(
                            {
                                "storage_filename": storage_filename,
                                "image_id": row.image_id,
                                "recorded": row.file_checksum,
                                "actual": digest,
                            }
                        )

        referenced = set(by_file)
        on_disk = {
            str(p.relative_to(media_root))
            for p in media_root.rglob("*")
            # Same exclusions the backup rsync uses, so "orphan" means the same
            # thing here as it does in a snapshot.
            if p.is_file() and p.name not in (".DS_Store", "_DELETE.ME")
        }
        orphans = sorted(on_disk - referenced)

        report = {
            "media_root": str(media_root),
            "checked_checksums": check_sums,
            "image_rows": total_rows,
            "distinct_files_referenced": len(by_file),
            "files_on_disk": len(on_disk),
            "missing": missing,
            "corrupt": corrupt,
            "size_drift": size_drift,
            "orphans": orphans,
        }

        if options["json_out"]:
            Path(options["json_out"]).write_text(json.dumps(report, indent=2))
            self.stdout.write(f"report written to {options['json_out']}")

        def _report(label, items, style, limit=10):
            if not items:
                self.stdout.write(self.style.SUCCESS(f"  {label}: 0"))
                return
            self.stdout.write(style(f"  {label}: {len(items)}"))
            for item in items[:limit]:
                name = item if isinstance(item, str) else item["storage_filename"]
                self.stdout.write(f"    {name}")
            if len(items) > limit:
                self.stdout.write(f"    ... and {len(items) - limit} more")

        # A row with no file is a broken image link -- the exact failure a
        # database-only restore produces, and the reason this command exists.
        _report("missing (DB row, no file)", missing, self.style.ERROR)
        _report("corrupt (sha256 mismatch)", corrupt, self.style.ERROR)
        _report("size drift", size_drift, self.style.WARNING)
        _report("orphans (file, no DB row)", orphans, self.style.WARNING)

        if not check_sums:
            self.stdout.write(
                self.style.WARNING(
                    "  note: run with --check-checksums to detect silent corruption"
                )
            )

        failed = bool(missing or corrupt)
        if options["fail_on_orphans"] and orphans:
            failed = True
        if failed:
            raise CommandError("media integrity check FAILED")

        self.stdout.write(self.style.SUCCESS("media integrity OK"))
