"""Tests for the state-centered ASCC CLI.

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_ascc_cli.py'

Expected exit code: 0.
"""

import argparse
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
        names = ("REPO_ROOT", "WIP_DIR", "WIP_IN", "WIP_CACHE", "WIP_OUT", "BACKEND_MEDIA")
        self.saved = {name: getattr(ascc_cli, name) for name in names}
        ascc_cli.REPO_ROOT = self.root
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
    def ocr_run_args(self, state, source_pdf, **overrides):
        values = {
            "state": state,
            "pdf": source_pdf,
            "provider": None,
            "model": None,
            "pages": None,
            "reference_work": "ASCC5",
            "import_check": "never",
            "force": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_parser_accepts_run_public_options(self):
        args = ascc_cli.build_parser().parse_args([
            "run",
            "va",
            "--reference-work",
            "ASCC6",
            "--v1-image-root",
            "legacy-images",
            "--allow-missing-v1-images",
            "--dry-run",
            "--truncate",
            "--only",
            "markings,images",
            "--allow-missing",
            "--skip-report",
            "skips.csv",
        ])

        self.assertEqual(args.command, "run")
        self.assertEqual(args.state, "va")
        self.assertEqual(args.reference_work, "ASCC6")
        self.assertEqual(str(args.v1_image_root), "legacy-images")
        self.assertTrue(args.allow_missing_v1_images)
        self.assertTrue(args.dry_run)
        self.assertTrue(args.truncate)
        self.assertEqual(args.only, "markings,images")
        self.assertTrue(args.allow_missing)
        self.assertEqual(args.skip_report, "skips.csv")

    def test_parser_accepts_ocr_public_options(self):
        args = ascc_cli.build_parser().parse_args([
            "ocr",
            "va",
            "--pdf",
            "input.pdf",
            "--provider",
            "anthropic",
            "--pages",
            "419-420",
            "--reference-work",
            "ASCC5",
            "--import-check",
            "never",
            "--force",
        ])

        self.assertEqual(args.command, "ocr")
        self.assertEqual(args.state, "va")
        self.assertEqual(str(args.pdf), "input.pdf")
        self.assertEqual(args.provider, "anthropic")
        self.assertEqual(args.pages, "419-420")
        self.assertEqual(args.import_check, "never")
        self.assertTrue(args.force)

    def test_parser_accepts_doctor_public_options(self):
        args = ascc_cli.build_parser().parse_args([
            "doctor",
            "va",
            "--v1-image-root",
            "legacy-images",
            "--allow-missing-v1-images",
        ])

        self.assertEqual(args.command, "doctor")
        self.assertEqual(args.state, "va")
        self.assertEqual(str(args.v1_image_root), "legacy-images")
        self.assertTrue(args.allow_missing_v1_images)

    def test_parser_accepts_munge_public_options(self):
        args = ascc_cli.build_parser().parse_args([
            "munge",
            "va",
            "--reference-work",
            "ASCC6",
            "--v1-image-root",
            "legacy-images",
            "--allow-missing-v1-images",
        ])

        self.assertEqual(args.command, "munge")
        self.assertEqual(args.state, "va")
        self.assertEqual(args.reference_work, "ASCC6")
        self.assertEqual(str(args.v1_image_root), "legacy-images")
        self.assertTrue(args.allow_missing_v1_images)

    def test_parser_v1_commands_default_to_missing_image_tolerance(self):
        parser = ascc_cli.build_parser()

        doctor = parser.parse_args(["doctor", "va"])
        munge = parser.parse_args(["munge", "va"])
        run = parser.parse_args(["run", "va"])

        self.assertTrue(doctor.allow_missing_v1_images)
        self.assertTrue(munge.allow_missing_v1_images)
        self.assertTrue(run.allow_missing_v1_images)

    def test_parser_v1_strict_images_disables_missing_image_tolerance(self):
        args = ascc_cli.build_parser().parse_args([
            "run",
            "va",
            "--strict-v1-images",
        ])

        self.assertFalse(args.allow_missing_v1_images)

    def test_parser_munge_defaults_to_ascc6(self):
        args = ascc_cli.build_parser().parse_args([
            "munge",
            "va",
        ])

        self.assertEqual(args.command, "munge")
        self.assertEqual(args.reference_work, "ASCC6")

    def test_parser_ocr_defaults_to_ascc5(self):
        args = ascc_cli.build_parser().parse_args([
            "ocr",
            "va",
            "--import-check",
            "never",
        ])

        self.assertEqual(args.command, "ocr")
        self.assertEqual(args.reference_work, "ASCC5")

    def test_parser_accepts_import_passthrough_options(self):
        args = ascc_cli.build_parser().parse_args([
            "import",
            "tools/wip/out/v1_va",
            "--dry-run",
            "--only",
            "markings,images",
        ])

        self.assertEqual(args.command, "import")
        self.assertEqual(
            args.import_args,
            ["tools/wip/out/v1_va", "--dry-run", "--only", "markings,images"],
        )

    def test_parser_accepts_drop_region_code_and_dry_run(self):
        args = ascc_cli.build_parser().parse_args([
            "drop",
            "USA-MI1",
            "--dry-run",
        ])

        self.assertEqual(args.command, "drop")
        self.assertEqual(args.region_code, "USA-MI1")
        self.assertTrue(args.dry_run)

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

    def test_v1_state_paths_use_separate_cache_and_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                paths = ascc_cli.v1_state_paths("va")

        self.assertEqual(paths.catalog_rows.as_posix().split("/")[-4:], ["cache", "v1", "VA", "catalog_rows.csv"])
        self.assertEqual(paths.bundle_dir.as_posix().split("/")[-3:], ["wip", "out", "v1_va"])
        self.assertEqual(paths.warnings.name, "v1_pipeline_warnings.csv")

    def test_v1_state_paths_are_state_specific_for_mi(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                paths = ascc_cli.v1_state_paths("mi")

        self.assertEqual(paths.catalog_rows.as_posix().split("/")[-4:], ["cache", "v1", "MI", "catalog_rows.csv"])
        self.assertEqual(paths.bundle_dir.as_posix().split("/")[-3:], ["wip", "out", "v1_mi"])

    def test_ocr_state_paths_are_state_specific_for_wv(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                paths = ascc_cli.state_paths("wv")

        self.assertEqual(paths.pdf.name, "WV.pdf")
        self.assertEqual(paths.catalog_rows.name, "WV_catalog_rows.csv")
        self.assertEqual(paths.bundle_dir.as_posix().split("/")[-3:], ["wip", "out", "wv"])

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

    def test_v1_doctor_does_not_require_pdf_or_llm(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                for name in ("reference_works.csv", "regions.csv", "tblStates.csv", "tblRawStateData.csv", "tblTownmarkImages.csv"):
                    (ascc_cli.WIP_IN / name).write_text("", encoding="utf-8")
                with patch.object(ascc_cli, "check_db", return_value=(False, "not available")):
                    checks = ascc_cli.v1_doctor_checks(
                        "VA",
                        ascc_cli.WIP_IN / "missing-images",
                        allow_missing_images=True,
                    )

        by_name = {check["name"]: check for check in checks}
        self.assertNotIn("pdf", by_name)
        self.assertNotIn("OPENROUTER_API_KEY", by_name)
        self.assertFalse(by_name["v1 image root"]["ok"])
        self.assertFalse(by_name["v1 image root"]["required"])
        self.assertFalse(by_name["database"]["required"])

    def test_v1_doctor_requires_bpm2_for_massachusetts(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                for name in ("regions.csv", "tblStates.csv", "tblRawStateData.csv", "tblTownmarkImages.csv"):
                    (ascc_cli.WIP_IN / name).write_text("", encoding="utf-8")
                ref_path = ascc_cli.WIP_IN / "reference_works.csv"
                ref_path.write_text("code\nASCC6\n", encoding="utf-8")
                with patch.object(ascc_cli, "check_db", return_value=(False, "not available")):
                    missing_checks = ascc_cli.v1_doctor_checks(
                        "ma",
                        ascc_cli.WIP_IN / "missing-images",
                        allow_missing_images=True,
                    )
                    ref_path.write_text("code\nASCC6\nBPM2\n", encoding="utf-8")
                    present_checks = ascc_cli.v1_doctor_checks(
                        "ma",
                        ascc_cli.WIP_IN / "missing-images",
                        allow_missing_images=True,
                    )

        missing = {check["name"]: check for check in missing_checks}
        present = {check["name"]: check for check in present_checks}
        self.assertFalse(missing["reference work BPM2"]["ok"])
        self.assertTrue(present["reference work BPM2"]["ok"])

    def test_v1_image_root_defaults_to_backup_state_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with _PatchedRoots(root):
                backup = root / "backups" / "images" / "virginia"
                backup.mkdir(parents=True)
                (ascc_cli.WIP_IN / "tblStates.csv").write_text(
                    "nStateID,txtStateAbv,txtState\n46,VA,Virginia\n",
                    encoding="utf-8",
                )

                image_root = ascc_cli.resolve_v1_image_root(None, "VA")

        self.assertEqual(image_root, backup)

    def test_ocr_orchestrates_stage_commands_with_canonical_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                args = self.ocr_run_args(
                    "VA",
                    source_pdf,
                    provider="anthropic",
                    pages="419",
                )
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_ocr_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertTrue(commands[0][1].endswith("ascc_page_processor.py"))
        self.assertIn("--output-csv", commands[1])
        self.assertIn("VA_ocr_rows.csv", " ".join(commands[1]))
        self.assertIn("--catalog-rows-out", commands[2])
        self.assertIn("VA_catalog_rows.csv", " ".join(commands[2]))
        self.assertTrue(commands[3][1].endswith("ascc_data_munger.py"))

    def test_main_dispatches_ocr_command(self):
        with patch.object(ascc_cli, "command_ocr_run", return_value=0) as command_ocr_run:
            rc = ascc_cli.main([
                "ocr",
                "VA",
                "--pdf",
                "input.pdf",
                "--import-check",
                "never",
            ])

        self.assertEqual(rc, 0)
        args = command_ocr_run.call_args.args[0]
        self.assertEqual(args.command, "ocr")
        self.assertEqual(args.state, "VA")
        self.assertEqual(str(args.pdf), "input.pdf")

    def test_ocr_skips_to_image_verification_when_ocr_rows_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                (ascc_cli.WIP_CACHE / "VA_ocr_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                args = self.ocr_run_args("VA", source_pdf)
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_ocr_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertTrue(commands[0][1].endswith("ascc_image_extract.py"))
        self.assertTrue(commands[1][1].endswith("ascc_data_munger.py"))

    def test_ocr_skips_to_munger_when_catalog_rows_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF")
            with _PatchedRoots(root):
                (ascc_cli.WIP_CACHE / "VA_catalog_rows.csv").write_text(
                    "listing_text,catalog_page,chunk_number,image_count,row_type,is_manuscript,default_shape,institutional_owner\n",
                    encoding="utf-8",
                )
                args = self.ocr_run_args("VA", source_pdf)
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_ocr_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0][1].endswith("ascc_data_munger.py"))

    def test_ocr_force_rebuilds_existing_catalog_rows(self):
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
                args = self.ocr_run_args("VA", source_pdf, force=True)
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "copy_marking_images", return_value=0):
                            with patch.object(ascc_cli, "clean_bundle_dir"):
                                with patch.object(ascc_cli, "maybe_import_check", return_value={"status": "skipped"}):
                                    with patch.object(ascc_cli, "write_run_manifest"):
                                        rc = ascc_cli.command_ocr_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertTrue(commands[0][1].endswith("ascc_page_processor.py"))
        self.assertTrue(commands[1][1].endswith("ascc_page_extract.py"))

    def test_munge_orchestrates_v1_only_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with _PatchedRoots(root):
                image_root = root / "legacy-images"
                image_root.mkdir()
                args = ascc_cli.build_parser().parse_args([
                    "munge",
                    "VA",
                    "--v1-image-root",
                    str(image_root),
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "v1_doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "clean_bundle_dir"):
                            with patch.object(ascc_cli, "write_v1_run_manifest"):
                                rc = ascc_cli.command_munge(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertTrue(commands[0][1].endswith("v1_catalog_rows.py"))
        self.assertTrue(commands[1][1].endswith("ascc_data_munger.py"))
        self.assertTrue(commands[2][1].endswith("v1_attach_images.py"))
        self.assertTrue(commands[3][1].endswith("v1_bundle_overlay.py"))
        self.assertIn("--reference-work-code", commands[1])
        self.assertEqual(
            commands[1][commands[1].index("--reference-work-code") + 1],
            "ASCC6",
        )
        self.assertIn("--region-abbrev", commands[1])
        self.assertEqual(commands[1][commands[1].index("--region-abbrev") + 1], "VA")
        self.assertIn("--v1-image-root", commands[2])
        self.assertIn("--media-dir", commands[2])
        self.assertIn("--allow-missing-v1-images", commands[2])
        self.assertIn("--allow-missing-v1-images", commands[3])
        self.assertIn("--preserve-images", commands[3])
        self.assertIn("--slice", commands[3])
        self.assertIn("--bundle-dir", commands[3])
        self.assertNotIn("ascc_page_extract.py", " ".join(" ".join(c) for c in commands))

    def test_run_orchestrates_munge_then_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with _PatchedRoots(root):
                image_root = root / "legacy-images"
                image_root.mkdir()
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--v1-image-root",
                    str(image_root),
                    "--dry-run",
                    "--allow-missing",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "v1_doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "clean_bundle_dir"):
                            with patch.object(ascc_cli, "write_v1_run_manifest"):
                                rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertEqual(len(commands), 5)
        self.assertTrue(commands[0][1].endswith("v1_catalog_rows.py"))
        self.assertTrue(commands[1][1].endswith("ascc_data_munger.py"))
        self.assertTrue(commands[2][1].endswith("v1_attach_images.py"))
        self.assertTrue(commands[3][1].endswith("v1_bundle_overlay.py"))
        self.assertTrue(commands[4][1].endswith("backend/manage.py"))
        self.assertIn("--allow-missing-v1-images", commands[2])
        self.assertIn("--allow-missing-v1-images", commands[3])
        self.assertEqual(commands[4][2], "import_ascc_bundle")
        self.assertTrue(commands[4][3].endswith("tools/wip/out/v1_va"))
        self.assertIn("--dry-run", commands[4])
        self.assertIn("--allow-missing", commands[4])

    def test_run_strict_v1_images_omits_missing_image_tolerance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with _PatchedRoots(root):
                image_root = root / "legacy-images"
                image_root.mkdir()
                args = ascc_cli.build_parser().parse_args([
                    "run",
                    "VA",
                    "--v1-image-root",
                    str(image_root),
                    "--strict-v1-images",
                    "--dry-run",
                ])
                ok_checks = [{"name": "ok", "ok": True, "required": True, "detail": ""}]
                with patch.object(ascc_cli, "v1_doctor_checks", return_value=ok_checks):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        with patch.object(ascc_cli, "clean_bundle_dir"):
                            with patch.object(ascc_cli, "write_v1_run_manifest"):
                                rc = ascc_cli.command_run(args)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertNotIn("--allow-missing-v1-images", commands[2])
        self.assertNotIn("--allow-missing-v1-images", commands[3])

    def test_import_delegates_to_ascc_bundle_management_command(self):
        args = ascc_cli.build_parser().parse_args([
            "import",
            "tools/wip/out/v1_va",
            "--dry-run",
            "--allow-missing",
        ])
        with patch.object(ascc_cli, "run_command") as run_command:
            rc = ascc_cli.command_import(args)

        self.assertEqual(rc, 0)
        cmd = run_command.call_args.args[0]
        self.assertTrue(cmd[1].endswith("backend/manage.py"))
        self.assertEqual(cmd[2], "import_ascc_bundle")
        self.assertEqual(cmd[3:], ["tools/wip/out/v1_va", "--dry-run", "--allow-missing"])

    def test_drop_delegates_exact_region_code_to_management_command(self):
        args = ascc_cli.build_parser().parse_args([
            "drop",
            "USA-MI1",
            "--dry-run",
        ])
        with patch.object(ascc_cli, "run_command") as run_command:
            rc = ascc_cli.command_drop(args)

        self.assertEqual(rc, 0)
        cmd = run_command.call_args.args[0]
        self.assertTrue(cmd[1].endswith("backend/manage.py"))
        self.assertEqual(cmd[2:], [
            "drop_ascc_state",
            "--region-code",
            "USA-MI1",
            "--dry-run",
        ])

    def test_import_check_uses_additive_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                paths = ascc_cli.state_paths("VA")
                with patch.object(ascc_cli, "check_db", return_value=(True, "available")):
                    with patch.object(ascc_cli, "run_command") as run_command:
                        result = ascc_cli.maybe_import_check(paths, "always")

        self.assertEqual(
            result,
            {"mode": "always", "status": "passed", "check": "dry-run-additive"},
        )
        run_command.assert_called_once()
        cmd = run_command.call_args.args[0]
        self.assertEqual(cmd[2], "import_ascc_bundle")
        self.assertEqual(cmd[3], str(paths.bundle_dir))
        self.assertEqual(cmd[-1], "--dry-run")
        self.assertNotIn("--truncate", cmd)

    def test_count_csv_rows_handles_quoted_multiline_cells(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rows.csv"
            path.write_text('id,text\n1,"a\\nb"\n2,c\n', encoding="utf-8")

            self.assertEqual(ascc_cli.count_csv_rows(path), 2)

    def test_clean_state_removes_matching_cache_and_bundle_only(self):
        with tempfile.TemporaryDirectory() as td:
            with _PatchedRoots(td):
                keep_pdf = ascc_cli.WIP_IN / "VA.pdf"
                keep_pdf.write_bytes(b"%PDF")
                remove_paths = [
                    ascc_cli.WIP_CACHE / "VA_ocr_rows.csv",
                    ascc_cli.WIP_CACHE / "VA_catalog_rows.csv",
                    ascc_cli.WIP_CACHE / "VA-ASCC-CTLG_chunks",
                    ascc_cli.WIP_CACHE / "v1" / "VA",
                    ascc_cli.WIP_OUT / "va",
                    ascc_cli.WIP_OUT / "v1_va",
                ]
                keep_paths = [
                    ascc_cli.WIP_CACHE / "WV_ocr_rows.csv",
                    ascc_cli.WIP_CACHE / "WV-ASCC-CTLG_chunks",
                    ascc_cli.WIP_CACHE / "compare" / "VA",
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
