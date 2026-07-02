"""Tests for .github/scripts/review.py (Claude PR review bot).

Run from repo root:
    PYTHONPATH=tools .venv/bin/python -m unittest discover \
        -s tools/tests -p 'test_pr_review_script.py'

Expected exit code: 0.

Only the pure functions are tested; the GitHub/Anthropic network calls are
exercised in the workflow itself.
"""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / ".github" / "scripts" / "review.py"
)
spec = importlib.util.spec_from_file_location("review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class BuildPrompt(unittest.TestCase):
    def test_small_diff_untruncated(self):
        prompt, truncated = review.build_prompt("+ hello")
        self.assertFalse(truncated)
        self.assertIn("+ hello", prompt)
        self.assertIn("<diff>", prompt)

    def test_oversize_diff_truncated_at_limit(self):
        diff = "x" * (review.MAX_DIFF_CHARS + 100)
        prompt, truncated = review.build_prompt(diff)
        self.assertTrue(truncated)
        # the diff body inside the prompt is capped at exactly the limit
        body = prompt.split("<diff>\n")[1].rsplit("\n</diff>")[0]
        self.assertEqual(len(body), review.MAX_DIFF_CHARS)


class FindExistingComment(unittest.TestCase):
    def test_finds_marked_comment(self):
        comments = [
            {"id": 1, "body": "unrelated"},
            {"id": 2, "body": f"{review.MARKER}\nold review"},
        ]
        self.assertEqual(review.find_existing_comment(comments), 2)

    def test_none_when_absent_or_bodyless(self):
        comments = [{"id": 1, "body": None}, {"id": 2}]
        self.assertIsNone(review.find_existing_comment(comments))


if __name__ == "__main__":
    unittest.main()
