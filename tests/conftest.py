from __future__ import annotations

import json

import httpx
import pytest

from tui_os.config import Settings
from tui_os.services.ollama import OllamaClient
from tui_os.services.telemetry import DemoTelemetry


def ollama_transport(tokens: list[str], *, models=("llama3.1:8b",), status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/generate":
            if status != 200:
                return httpx.Response(status, json={"error": "model 'nope' not found"})
            body = json.loads(request.content)
            assert body["stream"] is True
            lines = [json.dumps({"response": t, "done": False}) for t in tokens]
            lines.append(json.dumps({
                "response": "", "done": True, "context": [1, 2, 3],
                "eval_count": len(tokens), "eval_duration": 500_000_000,
            }))
            return httpx.Response(200, content=("\n".join(lines) + "\n").encode())
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def settings() -> Settings:
    # Most tests drive the LLM terminal; tests/test_shell.py covers the SSH shell.
    return Settings(demo_mode=True, telemetry_interval=1.0, stream_flush_interval=0.05,
                    browser_home="about:home", start_tab="terminal",
                    shell_autoconnect=False)


@pytest.fixture
def make_app(settings):
    from tui_os.app import TuiOS

    def factory(tokens=("Hello", ", ", "world", "!"), **kw):
        client = OllamaClient("http://olares:11434", "llama3.1:8b",
                              transport=ollama_transport(list(tokens), **kw))
        return TuiOS(settings, telemetry_source=DemoTelemetry(seed=1), ollama_client=client)

    return factory
