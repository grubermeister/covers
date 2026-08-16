"""Tests for the backup shell tooling.

Deliberately narrow, per the project's Pareto testing rule: these cover the
load-bearing logic only -- the retention selector (which decides what gets
DELETED), the tag validation (which is what makes the NOPASSWD sudo grant safe),
and the engine-family gate (which is ISSUE-2026-08-10-01 as a machine check).

Everything here is a pure function over strings: no server, no database, no
filesystem state beyond a tmp_path. The parts that need a real MySQL are
exercised by tools/rehearse_restore.sh instead.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SH = REPO_ROOT / "deploy" / "worldcovers-backup.sh"
RESTORE_SH = REPO_ROOT / "deploy" / "worldcovers-restore.sh"

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def _name(days_ago: float, tag: str | None = None) -> str:
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{stamp}-{tag}" if tag else stamp


def select_prunable(names: list[str], keep_latest: str = "") -> set[str]:
    """Run the script's internal retention selector over `names`."""
    proc = subprocess.run(
        ["bash", str(BACKUP_SH), "--select-prunable"],
        input="\n".join(names),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "WOCO_BACKUP_NOW": str(int(NOW.timestamp())),
            "WOCO_KEEP_LATEST": keep_latest,
        },
    )
    assert proc.returncode == 0, proc.stderr
    return {line for line in proc.stdout.splitlines() if line}


class TestRetention:
    def test_keeps_seven_dailies_four_weeklies_and_tagged(self):
        names = [_name(i) for i in range(40)]
        names += [_name(14, "pre-import-v1-va"), _name(45, "pre-wipe")]

        pruned = select_prunable(names, keep_latest=_name(0))
        kept = set(names) - pruned

        # the last 7 days survive
        for i in range(7):
            assert _name(i) in kept

        # one weekly per ISO week for the last 4 weeks
        weeklies = {n for n in kept if n not in {_name(i) for i in range(7)} and "-" not in n[17:]}
        assert 3 <= len(weeklies) <= 5, f"expected ~4 weeklies, got {sorted(weeklies)}"

        # tagged snapshots are never rotated away by age alone
        assert _name(14, "pre-import-v1-va") in kept
        assert _name(45, "pre-wipe") in kept

        assert pruned, "a 40-day history should prune something"

    def test_never_prunes_below_two_snapshots(self):
        # Every one of these is old enough to be prunable by date.
        names = [_name(200), _name(201), _name(202)]
        assert select_prunable(names) == set()

    def test_junk_directories_are_never_deleted_and_do_not_satisfy_the_floor(self):
        # Two unparseable names must not count toward "keep at least 2" and so
        # license pruning every real snapshot.
        names = ["not-a-snapshot", "db_export", _name(200), _name(201), _name(202)]
        pruned = select_prunable(names)
        assert "not-a-snapshot" not in pruned
        assert "db_export" not in pruned
        assert pruned == set()

    def test_latest_is_never_pruned(self):
        ancient = _name(300)
        names = [ancient] + [_name(200 + i) for i in range(5)]
        assert ancient not in select_prunable(names, keep_latest=ancient)


class TestTagValidation:
    """The NOPASSWD sudo grant is only safe because these are rejected."""

    @pytest.mark.parametrize(
        "tag",
        ["../etc/passwd", "UPPERCASE", "has space", "semi;rm -rf /", "$(whoami)", "-leading-dash",
         "x" * 42],
    )
    def test_rejects_unsafe_tags(self, tag):
        proc = subprocess.run(
            ["bash", str(BACKUP_SH), "--tag", tag],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode != 0
        assert "invalid tag" in proc.stderr

    @pytest.mark.parametrize("tag", ["pre-import-v1-va", "pre-wipe", "a", "0", "a-b-c-1"])
    def test_accepts_safe_tags(self, tag):
        # Rejected later for other reasons (no mysql.cnf here); the point is
        # that it is not rejected AS A TAG.
        proc = subprocess.run(
            ["bash", str(BACKUP_SH), "--tag", tag],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
        )
        assert "invalid tag" not in proc.stderr


class TestRestoreEngineGate:
    """A MariaDB dump aborts halfway through a MySQL restore. Refuse up front."""

    def _snapshot(self, tmp_path: Path, family: str) -> Path:
        dest = tmp_path / "snapshots" / "2026-08-15T023007Z"
        (dest / "db").mkdir(parents=True)
        (dest / "MANIFEST.json").write_text(
            '{"schema":1,"engine":{"family":"%s","version":"x"},'
            '"db":{"file":"db/worldcovers.sql.zst"}}' % family
        )
        (dest / "db" / "worldcovers.sql.zst").write_bytes(b"")
        (dest / "SHA256SUMS").write_text("")
        return dest

    def test_refuses_live_restore_without_confirmation(self, tmp_path):
        self._snapshot(tmp_path, "mysql")
        proc = subprocess.run(
            ["bash", str(RESTORE_SH), "--snapshot", "2026-08-15T023007Z", "--into", "worldcovers"],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "WOCO_BACKUP_DIR": str(tmp_path),
                 "WOCO_DB_NAME": "worldcovers"},
        )
        assert proc.returncode != 0
        assert "refusing to overwrite live database" in proc.stderr

    @pytest.mark.parametrize("dbname", ["evil;DROP", "a b", "../x", ""])
    def test_rejects_bad_database_names(self, tmp_path, dbname):
        self._snapshot(tmp_path, "mysql")
        proc = subprocess.run(
            ["bash", str(RESTORE_SH), "--snapshot", "2026-08-15T023007Z", "--into", dbname],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "WOCO_BACKUP_DIR": str(tmp_path)},
        )
        assert proc.returncode != 0
