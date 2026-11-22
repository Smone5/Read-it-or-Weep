"""The one place that talks to the AI provider.

Three capabilities are used by the rest of the app:

    parse()   – ask a model for a typed (schema-checked) answer, with optional images
    text()    – ask a model for a short piece of text
    embed()   – turn texts into vectors

Swapping provider (OpenAI ↔ Azure OpenAI today; anything else later) means
changing this module only. Model names come from configuration.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Sequence, TypeVar

import numpy as np
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncAzureOpenAI, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from config import config

log = logging.getLogger("llm")

T = TypeVar("T", bound=BaseModel)

_client: AsyncOpenAI | None = None
_semaphore: asyncio.Semaphore | None = None


class AIUnavailable(RuntimeError):
    """Raised when no AI provider is configured or it keeps failing."""


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not config.ai_configured:
            raise AIUnavailable("No AI provider is configured (set OPENAI_API_KEY or the AZURE_OPENAI_* settings).")
        if config.llm_provider == "azure":
            _client = AsyncAzureOpenAI(
                azure_endpoint=config.azure_openai_endpoint,
                api_key=config.azure_openai_api_key,
                api_version=config.azure_openai_api_version,
                max_retries=2,
                timeout=120,
            )
        else:
            _client = AsyncOpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url or None,
                max_retries=2,
                timeout=120,
            )
    return _client


def _gate() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, config.llm_concurrency))
    return _semaphore


def image_part(data: bytes, mime: str = "image/png", detail: str = "high") -> dict[str, Any]:
    encoded = base64.b64encode(data).decode()
    return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": detail}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "input_text", "text": text}


def _input(system: str, user: str | Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    content = [text_part(user)] if isinstance(user, str) else list(user)
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


async def parse(
    *,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    schema: type[T],
    effort: str = "low",
    max_output_tokens: int = 6000,
    attempts: int = 3,
) -> T:
    """Return an instance of ``schema`` produced by the model (Structured Outputs)."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with _gate():
                response = await client().responses.parse(
                    model=model,
                    input=_input(system, user),
                    text_format=schema,
                    reasoning={"effort": effort},
                    max_output_tokens=max_output_tokens,
                )
            parsed = response.output_parsed
            if parsed is not None:
                return parsed
            last_error = AIUnavailable(f"{model} returned no structured output (status={response.status}).")
        except _RETRYABLE as exc:
            last_error = exc
        except APIStatusError as exc:
            if exc.status_code < 500:
                raise
            last_error = exc
        if attempt < attempts:
            await asyncio.sleep(1.5 * attempt)
    raise AIUnavailable(str(last_error) if last_error else "AI request failed")


def final_text(response: Any) -> str:
    """The model's answer: the text of its last final message.

    Not ``response.output_text``, which joins every message in the reply - and a model may send a draft or a
    remark (a "commentary" message) before its final answer, or the final answer twice. Joined, that made the
    host say his line twice in a row, sometimes followed by the model's own word count.
    """
    messages = [item for item in (getattr(response, "output", None) or []) if getattr(item, "type", "") == "message"]
    finals = [m for m in messages if getattr(m, "phase", None) in (None, "final_answer")]
    chosen = (finals or messages)[-1:]
    if not chosen:
        return (getattr(response, "output_text", "") or "").strip()
    return "".join(getattr(part, "text", "") or "" for part in (getattr(chosen[0], "content", None) or [])).strip()


async def text(
    *,
    model: str,
    system: str,
    user: str,
    effort: str = "low",
    max_output_tokens: int = 400,
) -> str:
    async with _gate():
        response = await client().responses.create(
            model=model,
            input=_input(system, user),
            reasoning={"effort": effort},
            max_output_tokens=max_output_tokens,
        )
    return final_text(response)


async def embed(texts: Sequence[str], batch_size: int = 64, attempts: int = 3) -> np.ndarray:
    """Return an (n, d) float32 array of L2-normalised embeddings."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = [t.replace("\n", " ")[:8000] or " " for t in texts[start:start + batch_size]]
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with _gate():
                    response = await client().embeddings.create(model=config.model_embedding, input=batch)
                vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
                last_error = None
                break
            except _RETRYABLE as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code < 500:
                    raise
                last_error = exc
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
        if last_error is not None:
            raise AIUnavailable(str(last_error)) from last_error
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-9, None)


def cosine_top(query: np.ndarray, matrix: np.ndarray, k: int, exclude: int | None = None) -> list[tuple[int, float]]:
    """Indices of the k rows of ``matrix`` most similar to ``query`` (all rows L2-normalised)."""
    if matrix.size == 0:
        return []
    scores = matrix @ query
    if exclude is not None and 0 <= exclude < len(scores):
        scores = scores.copy()
        scores[exclude] = -1.0
    order = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in order]
