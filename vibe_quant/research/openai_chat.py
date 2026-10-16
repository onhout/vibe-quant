"""OpenAI-compatible chat-completions backend for the research LLM calls.

``claude -p`` (the default) is swapped for any OpenAI-compatible endpoint by
setting ``VQ_EXTRACTOR=openai`` plus ``VQ_OPENAI_BASE_URL`` / ``VQ_OPENAI_MODEL``
(and ``VQ_OPENAI_API_KEY`` when the endpoint needs one). Extraction
(:class:`vibe_quant.research.extractor.OpenAIChatExtractor`), indicator codegen
and the ``trader_methods`` source all go through :func:`chat_completion`.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ENV_EXTRACTOR = "VQ_EXTRACTOR"
ENV_OPENAI_BASE_URL = "VQ_OPENAI_BASE_URL"
ENV_OPENAI_MODEL = "VQ_OPENAI_MODEL"
ENV_OPENAI_API_KEY = "VQ_OPENAI_API_KEY"

DEFAULT_OPENAI_MODEL = "gpt-5.6-sol"


class ChatCompletionError(ValueError):
    """The endpoint failed or answered with something other than a message."""


@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str
    model: str
    api_key: str | None

    @property
    def label(self) -> str:
        return f"openai:{self.model}"


def use_openai() -> bool:
    """True when research LLM calls should go to the OpenAI-compatible endpoint."""
    return os.getenv(ENV_EXTRACTOR, "").strip().lower() == "openai"


def config_from_env() -> OpenAIConfig:
    base_url = os.getenv(ENV_OPENAI_BASE_URL, "").strip().rstrip("/")
    if not base_url:
        raise FileNotFoundError(
            f"{ENV_EXTRACTOR}=openai needs {ENV_OPENAI_BASE_URL} (e.g. http://host:port/v1)"
        )
    return OpenAIConfig(
        base_url=base_url,
        model=os.getenv(ENV_OPENAI_MODEL, "").strip() or DEFAULT_OPENAI_MODEL,
        api_key=os.getenv(ENV_OPENAI_API_KEY) or None,
    )


def _image_part(path: str) -> dict[str, Any]:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def chat_completion(
    prompt: str,
    *,
    config: OpenAIConfig,
    timeout_seconds: float,
    image_paths: list[str] | None = None,
) -> str:
    """Send one user message (plus optional local images) and return the reply text.

    Images are sent as base64 data URLs; if the endpoint rejects them (a
    text-only model) the call is retried once without them.
    """
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    images = [p for p in (image_paths or []) if Path(p).is_file()]

    def _post(with_images: bool) -> httpx.Response:
        content: str | list[dict[str, Any]] = prompt
        if with_images:
            content = [{"type": "text", "text": prompt}, *(_image_part(p) for p in images)]
        body = {"model": config.model, "messages": [{"role": "user", "content": content}]}
        return httpx.post(
            f"{config.base_url}/chat/completions",
            json=body,
            headers=headers,
            timeout=timeout_seconds,
        )

    try:
        response = _post(bool(images))
        if images and response.status_code in (400, 415, 422):
            response = _post(False)
    except httpx.TimeoutException as e:
        raise ChatCompletionError(f"timeout after {timeout_seconds}s") from e
    except httpx.HTTPError as e:
        raise ChatCompletionError(f"{type(e).__name__}: {e}") from e
    if response.status_code >= 400:
        raise ChatCompletionError(f"HTTP {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ChatCompletionError(f"unexpected chat response: {response.text[:300]}") from e
    if not isinstance(content, str) or not content.strip():
        raise ChatCompletionError("empty chat response")
    return content
