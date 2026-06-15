"""Tests for pipeline LLM provider selection.

Run from repo root:
    .venv/bin/python -m unittest discover -s tools -p 'test_pipeline_llm.py'

Expected exit code: 0.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import ascc_page_extract
from pipeline_llm import (
    DEFAULT_OPENROUTER_MODEL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
    PipelineLLM,
    make_pipeline_llm,
    resolve_model,
    resolve_provider,
)


class _OpenRouterCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        choice = SimpleNamespace(
            message=SimpleNamespace(content="openrouter text")
        )
        return SimpleNamespace(choices=[choice])


class _OpenRouterClient:
    def __init__(self):
        self.completions = _OpenRouterCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _AnthropicMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = SimpleNamespace(type="text", text="anthropic text")
        return SimpleNamespace(content=[block])


class _AnthropicClient:
    def __init__(self):
        self.messages = _AnthropicMessages()


class PipelineLLMTests(unittest.TestCase):
    def test_openrouter_payload_and_text(self):
        client = _OpenRouterClient()
        llm = PipelineLLM(PROVIDER_OPENROUTER, client)

        text = llm.vision_text(
            model="anthropic/claude-sonnet-4.6",
            system_prompt="system",
            user_text="user",
            image_b64="abc123",
            max_tokens=99,
        )

        self.assertEqual(text, "openrouter text")
        kwargs = client.completions.kwargs
        self.assertEqual(kwargs["model"], "anthropic/claude-sonnet-4.6")
        self.assertEqual(kwargs["max_tokens"], 99)
        user_content = kwargs["messages"][1]["content"]
        self.assertEqual(user_content[0]["type"], "image_url")
        self.assertEqual(
            user_content[0]["image_url"]["url"],
            "data:image/png;base64,abc123",
        )
        self.assertEqual(user_content[1], {"type": "text", "text": "user"})

    def test_anthropic_payload_and_text(self):
        client = _AnthropicClient()
        llm = PipelineLLM(PROVIDER_ANTHROPIC, client)

        text = llm.vision_text(
            model="claude-sonnet-4-6",
            system_prompt="system",
            user_text="user",
            image_b64="abc123",
            max_tokens=99,
        )

        self.assertEqual(text, "anthropic text")
        kwargs = client.messages.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        self.assertEqual(kwargs["max_tokens"], 99)
        self.assertEqual(kwargs["system"], "system")
        user_content = kwargs["messages"][0]["content"]
        self.assertEqual(user_content[0]["type"], "image")
        self.assertEqual(user_content[0]["source"]["type"], "base64")
        self.assertEqual(user_content[0]["source"]["media_type"], "image/png")
        self.assertEqual(user_content[0]["source"]["data"], "abc123")
        self.assertEqual(user_content[1], {"type": "text", "text": "user"})

    def test_missing_keys_raise_exact_messages(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(
                AssertionError,
                "OPENROUTER_API_KEY not set in .env",
            ):
                make_pipeline_llm(PROVIDER_OPENROUTER)
            with self.assertRaisesRegex(
                AssertionError,
                "ANTHROPIC_API_KEY not set in .env",
            ):
                make_pipeline_llm(PROVIDER_ANTHROPIC)

    def test_provider_and_model_resolution(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = resolve_provider(None)
            self.assertEqual(provider, PROVIDER_OPENROUTER)
            self.assertEqual(
                resolve_model(provider, None),
                DEFAULT_OPENROUTER_MODEL,
            )

        env = {
            "PIPELINE_LLM_PROVIDER": PROVIDER_ANTHROPIC,
            "PIPELINE_LLM_MODEL": "claude-custom",
        }
        with patch.dict("os.environ", env, clear=True):
            provider = resolve_provider(None)
            self.assertEqual(provider, PROVIDER_ANTHROPIC)
            self.assertEqual(resolve_model(provider, None), "claude-custom")
            self.assertEqual(resolve_model(provider, "cli-model"), "cli-model")

    def test_extract_cache_accepts_old_openrouter_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cache.json"
            path.write_text(json.dumps({
                "model": DEFAULT_OPENROUTER_MODEL,
                "prompt_version": "v9",
                "responses": {"page-0001-0001": {"ok": True}},
            }))

            cache = ascc_page_extract.load_cache(
                path,
                PROVIDER_OPENROUTER,
                DEFAULT_OPENROUTER_MODEL,
                "v9",
            )

            self.assertEqual(cache["provider"], PROVIDER_OPENROUTER)
            self.assertEqual(cache["responses"]["page-0001-0001"], {"ok": True})

    def test_extract_cache_invalidates_on_provider_change(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cache.json"
            path.write_text(json.dumps({
                "model": DEFAULT_OPENROUTER_MODEL,
                "prompt_version": "v9",
                "responses": {"page-0001-0001": {"ok": True}},
            }))

            cache = ascc_page_extract.load_cache(
                path,
                PROVIDER_ANTHROPIC,
                DEFAULT_OPENROUTER_MODEL,
                "v9",
            )

            self.assertEqual(cache["provider"], PROVIDER_ANTHROPIC)
            self.assertEqual(cache["responses"], {})


if __name__ == "__main__":
    unittest.main()
