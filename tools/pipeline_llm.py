"""Provider selection for ASCC pipeline vision calls.

The API-dependent ASCC tools use this module so provider choice stays
consistent across page processing and chunk text extraction.
"""

import os
from dataclasses import dataclass


PROVIDER_OPENROUTER = "openrouter"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDERS = (PROVIDER_OPENROUTER, PROVIDER_ANTHROPIC)

DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.6"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"


def resolve_provider(provider_arg=None):
    """Resolve provider from CLI, env, then backward-compatible default."""
    provider = (provider_arg or os.environ.get("PIPELINE_LLM_PROVIDER")
                or PROVIDER_OPENROUTER)
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        choices = ", ".join(PROVIDERS)
        raise ValueError(f"unknown provider {provider!r}; expected one of {choices}")
    return provider


def default_model_for_provider(provider):
    if provider == PROVIDER_OPENROUTER:
        return DEFAULT_OPENROUTER_MODEL
    if provider == PROVIDER_ANTHROPIC:
        return DEFAULT_ANTHROPIC_MODEL
    raise ValueError(f"unknown provider {provider!r}")


def resolve_model(provider, model_arg=None):
    """Resolve model from CLI, env, then provider-specific default."""
    model = model_arg or os.environ.get("PIPELINE_LLM_MODEL")
    if model:
        return model
    return default_model_for_provider(provider)


@dataclass(frozen=True)
class PipelineLLM:
    provider: str
    client: object

    def vision_text(self, model, system_prompt, user_text, image_b64,
                    max_tokens):
        """Send one PNG plus text prompt and return assistant text.

        OpenRouter uses:
            from openai import OpenAI
            client.chat.completions.create(
                *, messages, model, max_tokens, ...
            ) -> ChatCompletion

        Anthropic uses:
            from anthropic import Anthropic
            client.messages.create(
                *, max_tokens, messages, model, system, ...
            ) -> Message
        """
        if self.provider == PROVIDER_OPENROUTER:
            return _openrouter_vision_text(
                self.client,
                model,
                system_prompt,
                user_text,
                image_b64,
                max_tokens,
            )
        if self.provider == PROVIDER_ANTHROPIC:
            return _anthropic_vision_text(
                self.client,
                model,
                system_prompt,
                user_text,
                image_b64,
                max_tokens,
            )
        raise ValueError(f"unknown provider {self.provider!r}")


def make_pipeline_llm(provider):
    """Create the configured provider client, validating required API key."""
    if provider == PROVIDER_OPENROUTER:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set in .env")
        from openai import OpenAI
        return PipelineLLM(
            provider=provider,
            client=OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
            ),
        )

    if provider == PROVIDER_ANTHROPIC:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        from anthropic import Anthropic
        return PipelineLLM(provider=provider, client=Anthropic(api_key=key))

    raise ValueError(f"unknown provider {provider!r}")


def _openrouter_vision_text(client, model, system_prompt, user_text, image_b64,
                            max_tokens):
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{image_b64}",
                }},
                {"type": "text", "text": user_text},
            ]},
        ],
    )
    choice = resp.choices[0]
    return choice.message.content or ""


def _anthropic_vision_text(client, model, system_prompt, user_text, image_b64,
                           max_tokens):
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                }},
                {"type": "text", "text": user_text},
            ]},
        ],
    )
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)
