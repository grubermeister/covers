"""Tests for the state-centered ASCC CLI.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_ascc_cli.py'

Expected exit code: 0.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ascc_cli


class _PatchedRoots:
    def __init__(self, root):
        self.root = Path(root)
        self.saved = {}

    def __enter__(self):
        names = ("WIP_DIR", "WIP_IN", "WIP_CACHE", "WIP_OUT", "BACKEND_MEDIA")
        self.saved = {name: getattr(ascc_cli, name) for name in names}
        ascc_cli.WIP_DIR = self.root / "tools" / "wip"
        ascc_cli.WIP_IN = ascc_cli.WIP_DIR / "in"
        ascc_cli.WIP_CACHE = ascc_cli.WIP_DIR / "cache"
        ascc_cli.WIP_OUT = ascc_cli.WIP_DIR / "out"
        ascc_cli.BACKEND_MEDIA = self.root / "backend" / "media"
        for path in (ascc_cli.WIP_IN, ascc_cli.WIP_CACHE, ascc_cli.WIP_OUT):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        for name, value in self.saved.items():
            setattr(ascc_cli, name, value)


class AsccCliTests(unittest.TestCase):
    def test_parser_accepts_run_public_options(self):
        args = ascc_cli.build_parser().parse_args([
            "run",
            "va",
            "--pdf",
            "input.pdf",
            "--provider",
            "anthropic",
            "--pages",
            "419-420",
            "--reference-work",
            "ASCC1",
            "--legacy-status",
            "active",
            "--import-check",
            "never",
            "--force",
        ])

        self.assertEqual(args.command, "run")
        self.assertEqual(args.state, "va")
        self.assertEqual(args.provider, "anthropic")
        self.assertEqual(args.import_check, "never")
        self.assertTrue(args.force)

    def test_parser_accepts_clean_with_optional_state(self):
        all_args = ascc_cli.build_parser().parse_args(["clean"])
        state_args = ascc_cli.build_parser().parse_args(["clean", "va"])

        self.assertEqual(all_args.command, "clean")
        self.assertIsNone(all_args.state)
        self.assertEqual(state_args.state, "va")

    def test_state_paths_use_existing_wip_roots(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                paths = ascc_cli.state_paths("va")

        self.assertEqual(paths.pdf.as_posix().split("/")[-3:], ["wip", "in", "VA.pdf"])
        self.assertEqual(paths.catalog_rows.name, "VA_catalog_rows.csv")
        self.assertEqual(paths.bundle_dir.as_posix().split("/")[-3:], ["wip", "out", "va"])

    def test_discover_state_pdf_accepts_unique_state_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                pdf = ascc_cli.WIP_IN / "VA-ASCC-CTLG.pdf"
                pdf.write_bytes(b"%PDF")

                found, error = ascc_cli.discover_state_pdf("VA")

        self.assertEqual(found.name, "VA-ASCC-CTLG.pdf")
        self.assertIsNone(error)

    def test_doctor_reports_missing_required_files_and_skips_db(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                (ascc_cli.WIP_IN / "VA.pdf").write_bytes(b"%PDF")
                with patch.dict(os.environ, {
                    "OPENROUTER_API_KEY": "x",
                    "PIPELINE_LLM_PROVIDER": "openrouter",
                }):
                    with patch.object(ascc_cli.shutil, "which", return_value="/usr/bin/pdftoppm"):
                        with patch.object(ascc_cli, "check_db", return_value=(False, "not available")):
                            checks = ascc_cli.doctor_checks("VA")

        by_name = {check["name"]: check for check in checks}
        self.assertFalse(by_name["reference works"]["ok"])
        self.assertFalse(by_name["legacy rows"]["ok"])
        self.assertFalse(by_name["database"]["ok"])
        self.assertFalse(by_name["database"]["required"])

    def test_run_orchestrates_stage_commands_with_canonical_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--pdf",
                    str(source_pdf),
                    "--provider",
                    "anthropic",
                    "--pages",
                    "419",
                    "--import-check",
                    "never",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 5)
        self.assertTrue(commands[0][1].endswith("ascc_page_processor.py"))
        self.assertIn("--output-csv", commands[1])
        self.assertIn("VA_ocr_rows.csv", " ".join(commands[1]))
        self.assertIn("--catalog-rows-out", commands[2])
        self.assertIn("VA_catalog_rows.csv", " ".join(commands[2]))
        self.assertTrue(commands[3][1].endswith("ascc_data_munger.py"))
        self.assertTrue(commands[4][1].endswith("ascc_compare.py"))
        self.assertIn("--bundle-dir", commands[4])

    def test_run_skips_to_image_verification_when_ocr_rows_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                (ascc_cli.WIP_CACHE / "VA_ocr_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--pdf",
                    str(source_pdf),
                    "--import-check",
                    "never",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertTrue(commands[0][1].endswith("ascc_image_extract.py"))
        self.assertTrue(commands[1][1].endswith("ascc_data_munger.py"))
        self.assertTrue(commands[2][1].endswith("ascc_compare.py"))

    def test_run_skips_to_munger_when_catalog_rows_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                (ascc_cli.WIP_CACHE / "VA_catalog_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--pdf",
                    str(source_pdf),
                    "--import-check",
                    "never",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertTrue(commands[0][1].endswith("ascc_data_munger.py"))
        self.assertTrue(commands[1][1].endswith("ascc_compare.py"))

    def test_run_force_rebuilds_existing_catalog_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                (ascc_cli.WIP_CACHE / "VA_ocr_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                (ascc_cli.WIP_CACHE / "VA_catalog_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--pdf",
                    str(source_pdf),
                    "--import-check",
                    "never",
                    "--force",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 5)
        self.assertTrue(commands[0][1].endswith("ascc_page_processor.py"))
        self.assertTrue(commands[1][1].endswith("ascc_page_extract.py"))

    def test_clean_state_removes_matching_cache_and_bundle_only(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                keep_pdf = ascc_cli.WIP_IN / "VA.pdf"
                keep_pdf.write_bytes(b"%PDF")
                remove_paths = [
                    ascc_cli.WIP_CACHE / "VA_ocr_rows.csv",
                    ascc_cli.WIP_CACHE / "VA_catalog_rows.csv",
                    ascc_cli.WIP_CACHE / "VA-ASCC-CTLG_chunks",
                    ascc_cli.WIP_CACHE / "compare" / "VA",
                    ascc_cli.WIP_OUT / "va",
                ]
                keep_paths = [
                    ascc_cli.WIP_CACHE / "WV_ocr_rows.csv",
                    ascc_cli.WIP_CACHE / "WV-ASCC-CTLG_chunks",
                    ascc_cli.WIP_CACHE / "compare" / "WV",
                    ascc_cli.WIP_OUT / "wv",
                ]
                for path in remove_paths + keep_paths:
                    if path.suffix:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("x", encoding="utf-8")
                    else:
                        path.mkdir(parents=True, exist_ok=True)

                removed = ascc_cli.clean_generated("VA")

                self.assertEqual(removed, len(remove_paths))
                self.assertTrue(keep_pdf.exists())
                for path in remove_paths:
                    self.assertFalse(path.exists(), path)
                for path in keep_paths:
                    self.assertTrue(path.exists(), path)

    def test_clean_all_clears_cache_and_out_but_keeps_inputs_and_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                keep_pdf = ascc_cli.WIP_IN / "VA.pdf"
                keep_pdf.write_bytes(b"%PDF")
                keep_placeholder = ascc_cli.WIP_OUT / "_DELETE.ME"
                keep_placeholder.write_text("", encoding="utf-8")
                remove_paths = [
                    ascc_cli.WIP_CACHE / "VA_ocr_rows.csv",
                    ascc_cli.WIP_CACHE / "compare",
                    ascc_cli.WIP_OUT / "va",
                ]
                for path in remove_paths:
                    if path.suffix:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("x", encoding="utf-8")
                    else:
                        path.mkdir(parents=True, exist_ok=True)

                removed = ascc_cli.clean_generated()

                self.assertEqual(removed, len(remove_paths))
                self.assertTrue(keep_pdf.exists())
                self.assertTrue(keep_placeholder.exists())
                for path in remove_paths:
                    self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
