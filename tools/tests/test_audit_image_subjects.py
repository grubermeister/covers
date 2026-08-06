"""Tests for the mis-slotted-image audit (issue #78).

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_audit_image_subjects.py'

Expected exit code: 0.
"""

import base64
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_image_subjects as audit


def image_row(**overrides):
    """A MARKING-subject image row shaped like /api/v2/images/ returns."""
    row = {
        "image_id": 1,
        "subject_type": "MARKING",
        "subject_id": 100,
        "original_filename": "Marking-148-12359.jpg",
        "storage_filename": "wv/Marking-148-12359.jpg",
        "image_width": 458,
        "image_height": 465,
        "image_description": "",
        "image_url": "https://example.invalid/img.jpg",
    }
    row.update(overrides)
    return row


class ClassifyTests(unittest.TestCase):
    def test_v1_view_label_beats_the_heuristic(self):
        """The importer stranded txtView in image_description; it is ground truth.

        Deliberately checked on an image whose shape says "marking", to prove
        the label wins rather than merely agreeing.
        """
        verdict, confidence, reason = audit.classify_row(
            image_row(image_description="Front", image_width=458, image_height=465)
        )
        self.assertEqual((verdict, confidence), ("COVER", "certain"))
        self.assertIn("front", reason)

    def test_details_label_routes_to_marking(self):
        verdict, confidence, _ = audit.classify_row(
            image_row(image_description="Details")
        )
        self.assertEqual((verdict, confidence), ("MARKING", "certain"))

    def test_cover_shaped_image_is_a_likely_cover(self):
        # prod image 2417: a Fetterman VA envelope filed as a marking image.
        verdict, confidence, _ = audit.classify_row(
            image_row(image_width=2631, image_height=1290)
        )
        self.assertEqual((verdict, confidence), ("COVER", "likely"))

    def test_marking_shaped_image_is_a_likely_marking(self):
        # prod image 2288: a correctly slotted Berkeley Springs CDS.
        verdict, confidence, _ = audit.classify_row(
            image_row(image_width=458, image_height=465)
        )
        self.assertEqual((verdict, confidence), ("MARKING", "likely"))

    def test_ambiguous_middle_band_yields_no_verdict(self):
        """No signal must mean no row, not a coin flip in the review list."""
        self.assertIsNone(
            audit.classify_row(image_row(image_width=900, image_height=400))
        )

    def test_wide_handstamp_is_not_called_a_cover(self):
        # Observed marking aspect ratios reach 8.6; only size separates them.
        self.assertIsNone(
            audit.classify_row(image_row(image_width=1720, image_height=200))
        )

    def test_origin_separates_legacy_import_from_live_upload(self):
        self.assertEqual(audit.origin_of(image_row()), "v1-legacy")
        self.assertEqual(
            audit.origin_of(image_row(original_filename="6f9fcc0dab6d4538.jpg")),
            "user-upload",
        )

    def test_state_comes_from_the_storage_path(self):
        """Editors are region-scoped, so the review list has to partition."""
        self.assertEqual(audit.state_of(image_row()), "wv")
        self.assertEqual(audit.state_of(image_row(storage_filename="loose.jpg")), "unknown")


class ReportTests(unittest.TestCase):
    def test_only_mismatched_rows_reach_the_review_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / "images.json"
            images.write_text(
                __import__("json").dumps([
                    # Filed as MARKING but shaped like a cover -> needs review.
                    image_row(image_id=1, image_width=2631, image_height=1290),
                    # Filed as MARKING and shaped like one -> agrees, skipped.
                    image_row(image_id=2),
                    # Ambiguous -> no verdict, skipped.
                    image_row(image_id=3, image_width=900, image_height=400),
                ]),
                encoding="utf-8",
            )
            out_dir = root / "out"
            rc = audit.main([
                "--images-json", str(images),
                "--out-dir", str(out_dir),
            ])
            with (out_dir / "all-states.csv").open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual(rc, 0)
        self.assertEqual([r["image_id"] for r in rows], ["1"])
        self.assertEqual(rows[0]["verdict"], "COVER")

    def test_certain_rows_sort_above_heuristic_ones(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / "images.json"
            images.write_text(
                __import__("json").dumps([
                    image_row(image_id=1, image_width=2631, image_height=1290),
                    image_row(image_id=2, image_description="Front"),
                ]),
                encoding="utf-8",
            )
            out_dir = root / "out"
            audit.main(["--images-json", str(images), "--out-dir", str(out_dir)])
            with (out_dir / "all-states.csv").open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual([r["confidence"] for r in rows], ["certain", "likely"])

    def test_per_state_csvs_are_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / "images.json"
            images.write_text(
                __import__("json").dumps([
                    image_row(image_id=1, image_width=2631, image_height=1290,
                              storage_filename="wv/a.jpg"),
                    image_row(image_id=2, image_width=2631, image_height=1290,
                              storage_filename="va/b.jpg"),
                ]),
                encoding="utf-8",
            )
            out_dir = root / "out"
            audit.main(["--images-json", str(images), "--out-dir", str(out_dir)])

            self.assertTrue((out_dir / "wv.csv").is_file())
            self.assertTrue((out_dir / "va.csv").is_file())


class VisionParseTests(unittest.TestCase):
    """The model is told to answer in JSON but does not always comply."""

    class _FakeLLM:
        def __init__(self, reply):
            self.reply = reply
            self.received_b64 = None

        def vision_text(self, **kwargs):
            self.received_b64 = kwargs.get("image_b64")
            return self.reply

    @staticmethod
    def jpeg_bytes():
        """A real JPEG: catalog images are JPEG, and vision_verdict converts."""
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.new("RGB", (40, 30), color=(200, 180, 150)).save(
            buffer, format="JPEG"
        )
        return buffer.getvalue()

    def verdict_for(self, reply):
        return audit.vision_verdict(self._FakeLLM(reply), "m", self.jpeg_bytes())[0]

    def test_jpeg_is_converted_to_png_before_sending(self):
        """pipeline_llm hardcodes an image/png media type; providers 400 on a
        JPEG declared as PNG, which is how this was found."""
        llm = self._FakeLLM('{"kind": "cover"}')
        audit.vision_verdict(llm, "m", self.jpeg_bytes())
        sent = base64.b64decode(llm.received_b64)
        self.assertEqual(sent[:8], b"\x89PNG\r\n\x1a\x0a")

    def test_large_images_are_downscaled(self):
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.new("RGB", (2631, 1290)).save(buffer, format="JPEG")
        llm = self._FakeLLM('{"kind": "cover"}')
        audit.vision_verdict(llm, "m", buffer.getvalue())
        with PILImage.open(io.BytesIO(base64.b64decode(llm.received_b64))) as img:
            self.assertLessEqual(max(img.size), audit.VISION_MAX_EDGE)

    def test_parses_clean_json(self):
        self.assertEqual(self.verdict_for('{"kind": "cover"}'), "COVER")
        self.assertEqual(self.verdict_for('{"kind": "marking"}'), "MARKING")

    def test_parses_json_wrapped_in_prose(self):
        self.assertEqual(
            self.verdict_for('Looking at it, {"kind": "cover"} seems right.'),
            "COVER",
        )

    def test_falls_back_to_substring_when_json_is_absent(self):
        self.assertEqual(self.verdict_for("This is a whole cover."), "COVER")

    def test_ambiguous_reply_is_unclear_not_a_guess(self):
        self.assertEqual(
            self.verdict_for("Could be a cover or a marking."), "UNCLEAR"
        )
        self.assertEqual(self.verdict_for(""), "UNCLEAR")


if __name__ == "__main__":
    unittest.main()
