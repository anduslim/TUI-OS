"""Async streaming client for OpenAI-compatible chat servers.

llama.cpp's ``llama-server`` (the Olares "llama.cpp Engine" app), vLLM,
LM Studio and Ollama's own ``/v1`` all speak this protocol::

    POST /v1/chat/completions  {"model": ..., "messages": [...], "stream": true}
    data: {"choices": [{"delta": {"content": "Hel"}}]}
    data: {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
           "usage": {...}, "timings": {...}}      # timings: llama.cpp only
    data: [DONE]

:class:`OpenAIChatClient` has the same interface as
:class:`~tui_os.services.ollama.OllamaClient`, so the terminal tab works with
either. The server is stateless, so the conversation is kept client-side:
``context`` is the message history, returned on the final chunk.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .ollama import GenerateChunk, LLMError, _error_message


def api_base(url: str) -> str:
    """Accept ``http://host:8080`` or ``http://host:8080/v1``."""
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


class OpenAIChatClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        connect_timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = api_base(base_url)
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # No read timeout: loading a big GGUF can take a while before the
        # first token. The caller (a Textual worker) handles cancellation.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(None, connect=connect_timeout),
            transport=transport,
        )

    async def generate(
        self,
        prompt: str,
        *,
        context: list[dict[str, str]] | None = None,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[GenerateChunk]:
        history = [m for m in (context or []) if m.get("role") != "system"]
        messages = ([{"role": "system", "content": system}] if system else []) + history
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model or "default",
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if options:
            body.update(options)

        reply: list[str] = []
        stats: dict[str, Any] = {}
        async with self._client.stream("POST", "/chat/completions", json=body) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                raise LLMError(_openai_error(raw) or f"HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise LLMError(f"malformed stream line: {data[:80]!r}") from exc
                if "error" in obj:
                    raise LLMError(_message_of(obj["error"]))
                stats.update(_stats(obj))
                for choice in obj.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content") or ""
                    if text:
                        reply.append(text)
                        yield GenerateChunk(text=text)

        history = messages[1:] if system else messages
        history = [*history, {"role": "assistant", "content": "".join(reply)}]
        yield GenerateChunk(text="", done=True, context=history, stats=stats)

    async def list_models(self) -> list[str]:
        resp = await self._client.get("/models", timeout=5.0)
        resp.raise_for_status()
        return sorted(str(m.get("id")) for m in resp.json().get("data", []) if m.get("id"))

    async def aclose(self) -> None:
        await self._client.aclose()


def _stats(obj: dict[str, Any]) -> dict[str, Any]:
    """Map usage/timings onto the Ollama-style keys GenerateChunk reads."""
    out: dict[str, Any] = {}
    timings = obj.get("timings") or {}
    if timings.get("predicted_n") and timings.get("predicted_ms"):
        out["eval_count"] = timings["predicted_n"]
        out["eval_duration"] = int(timings["predicted_ms"] * 1e6)  # ns
    usage = obj.get("usage") or {}
    if usage.get("completion_tokens") and "eval_count" not in out:
        out["completion_tokens"] = usage["completion_tokens"]
    return out


def _message_of(err: Any) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


def _openai_error(raw: bytes) -> str | None:
    try:
        obj = json.loads(raw)
    except ValueError:
        return _error_message(raw)
    if isinstance(obj, dict) and "error" in obj:
        return _message_of(obj["error"])
    return _error_message(raw)
