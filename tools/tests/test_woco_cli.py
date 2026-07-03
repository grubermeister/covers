"""Tests for the top-level woco command dispatcher.

Run from repo root:
    PYTHONPATH=. .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_woco_cli.py'

Expected exit code: 0.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import woco_cli


class WocoCliAliasTests(unittest.TestCase):
    def run_alias(self, *argv):
        with patch.object(sys, "argv", ["woco", *argv]):
            with patch.object(woco_cli.os, "execv", side_effect=SystemExit(0)) as execv:
                with self.assertRaises(SystemExit) as raised:
                    woco_cli.main()
        self.assertEqual(raised.exception.code, 0)
        return execv.call_args.args

    def test_push_execs_push_data_script_with_remaining_args(self):
        path_arg, argv_arg = self.run_alias("push", "--import", "--state", "VA")

        self.assertEqual(Path(path_arg).name, "push_data.sh")
        self.assertEqual(Path(path_arg).parent.name, "tools")
        self.assertEqual(argv_arg, [path_arg, "--import", "--state", "VA"])

    def test_reload_execs_reload_data_script_with_remaining_args(self):
        path_arg, argv_arg = self.run_alias("reload", "tools/wip/out/v1_va")

        self.assertEqual(Path(path_arg).name, "reload_data.sh")
        self.assertEqual(Path(path_arg).parent.name, "tools")
        self.assertEqual(argv_arg, [path_arg, "tools/wip/out/v1_va"])


if __name__ == "__main__":
    unittest.main()
