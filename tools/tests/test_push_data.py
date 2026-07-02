"""Regression tests for tools/push_data.sh host resolution.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_push_data.py'

Expected exit code: 0.

The script is copied into a temp repo root so the tests never read the
developer's real .env or touch the network: rsync is stubbed out on PATH
and just records the arguments it was called with.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "push_data.sh"

RSYNC_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$RSYNC_LOG"
exit 0
"""


class PushDataHostResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "tools").mkdir()
        (self.tmp / "tools" / "wip").mkdir()
        (self.tmp / "backend" / "media").mkdir(parents=True)
        self.script = self.tmp / "tools" / "push_data.sh"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        # rsync stub that records its argv instead of talking to a server
        bindir = self.tmp / "bin"
        bindir.mkdir()
        self.rsync_log = self.tmp / "rsync.log"
        stub = bindir / "rsync"
        stub.write_text(RSYNC_STUB)
        stub.chmod(0o755)
        self.env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "RSYNC_LOG": str(self.rsync_log),
            "HOME": str(self.tmp),
        }

    def run_script(self, *args, env_extra=None):
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [str(self.script), *args],
            env=env, capture_output=True, text=True,
        )

    def test_fails_loudly_when_no_user_configured(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("WOCO_DEPLOY_USER", result.stderr)

    def test_help_works_without_any_config(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("push_data.sh", result.stdout)

    def test_env_file_user_builds_host(self):
        (self.tmp / ".env").write_text(
            "DEBUG=True\n"
            "DEFAULT_FROM_EMAIL=WorldCovers <no-reply@hellowoco.app>\n"
            "WOCO_DEPLOY_USER=alice\n"
        )
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alice@hellowoco.app:", self.rsync_log.read_text())

    def test_dry_run_refuses_import(self):
        (self.tmp / ".env").write_text("WOCO_DEPLOY_USER=alice\n")
        result = self.run_script("--dry-run", "--import")
        self.assertEqual(result.returncode, 2)
        self.assertIn("never a dry run", result.stderr)
        self.assertFalse(self.rsync_log.exists(), "no rsync may run")

    def test_woco_host_env_overrides_env_file(self):
        (self.tmp / ".env").write_text("WOCO_DEPLOY_USER=alice\n")
        result = self.run_script(env_extra={"WOCO_HOST": "bob@example.org"})
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.rsync_log.read_text()
        self.assertIn("bob@example.org:", log)
        self.assertNotIn("alice@", log)


if __name__ == "__main__":
    unittest.main()
