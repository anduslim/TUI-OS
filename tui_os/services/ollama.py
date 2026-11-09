"""Async streaming client for the Ollama HTTP API.

``POST /api/generate`` with ``"stream": true`` returns newline-delimited JSON,
one object per token batch::

    {"model": "...", "response": "Hel", "done": false}
    {"model": "...", "response": "lo", "done": false}
    {"model": "...", "response": "", "done": true, "context": [...], "eval_count": 42, ...}

:meth:`OllamaClient.generate` turns that into an async iterator of
:class:`GenerateChunk`, so callers can ``async for`` over tokens while the
event loop stays free to repaint the TUI and handle keys.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Raised for API-level errors (bad model name, server error, ...).

    Also raised by the OpenAI-compatible client, so callers handle one type.
    """


LLMError = OllamaError


@dataclass(frozen=True)
class GenerateChunk:
    text: str
    done: bool = False
    #: Opaque conversation state returned on the final chunk; pass it back on
    #: the next request to keep a multi-turn session. (Ollama: token ids;
    #: OpenAI-compatible: the message history.)
    context: Any = None
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens_per_second(self) -> float | None:
        count = self.stats.get("eval_count")
        duration = self.stats.get("eval_duration")  # nanoseconds
        if not count or not duration:
            return None
        return count / (duration / 1e9)


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        connect_timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        # No read timeout: a cold model load on the server can take a while
        # before the first token arrives. Cancellation is handled by the
        # caller (the Textual worker), not by the socket.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(None, connect=connect_timeout),
            transport=transport,
        )

    async def generate(
        self,
        prompt: str,
        *,
        context: list[int] | None = None,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[GenerateChunk]:
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "stream": True}
        if context:
            body["context"] = context
        if system:
            body["system"] = system
        if options:
            body["options"] = options

        async with self._client.stream("POST", "/api/generate", json=body) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                raise OllamaError(_error_message(raw) or f"HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise OllamaError(f"malformed stream line: {line[:80]!r}") from exc
                if "error" in obj:
                    raise OllamaError(str(obj["error"]))
                done = bool(obj.get("done"))
                stats = (
                    {k: v for k, v in obj.items() if k.endswith(("_count", "_duration"))}
                    if done
                    else {}
                )
                yield GenerateChunk(
                    text=obj.get("response", ""),
                    done=done,
                    context=obj.get("context") if done else None,
                    stats=stats,
                )
                if done:
                    return

    async def list_models(self) -> list[str]:
        resp = await self._client.get("/api/tags", timeout=5.0)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_message(raw: bytes) -> str | None:
    try:
        return str(json.loads(raw).get("error")) or None
    except (ValueError, AttributeError):
        text = raw.decode(errors="replace").strip()
        return text[:200] or None
