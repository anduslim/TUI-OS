"""LLM endpoint selection, the OpenAI-compatible client and config overrides."""

from __future__ import annotations

import json

import httpx
import pytest

from tui_os.config import Settings
from tui_os.services.llm import make_llm_client
from tui_os.services.ollama import LLMError, OllamaClient
from tui_os.services.openai_chat import OpenAIChatClient, api_base


def sse(*objs) -> bytes:
    lines = [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]", ""]
    return "\n\n".join(lines).encode()


def llama_cpp_transport(tokens, *, seen: list, models=("unsloth/Qwen3-Coder:Q4_K_M",), status=200):
    """Mimics llama-server: SSE deltas, then a final chunk with timings."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": m} for m in models]})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            seen.append((body, request.headers.get("authorization")))
            if status != 200:
                return httpx.Response(status, json={"error": {"code": status, "message": "model is loading"}})
            chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
            chunks += [{"choices": [{"index": 0, "delta": {"content": t}}]} for t in tokens]
            chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "timings": {"predicted_n": 40, "predicted_ms": 2000.0}})
            return httpx.Response(200, content=sse(*chunks),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_api_base_accepts_with_or_without_v1():
    assert api_base("http://h:8080") == "http://h:8080/v1"
    assert api_base("http://h:8080/v1/") == "http://h:8080/v1"
    assert api_base("https://llama.anduslim.olares.com") == "https://llama.anduslim.olares.com/v1"


async def test_openai_client_streams_and_keeps_history():
    seen: list = []
    client = OpenAIChatClient("http://olares:8080", "", api_key="sk-local",
                              transport=llama_cpp_transport(["Hel", "lo"], seen=seen))
    chunks = [c async for c in client.generate("hi", system="be terse")]
    assert "".join(c.text for c in chunks) == "Hello"
    final = chunks[-1]
    assert final.done and final.tokens_per_second == pytest.approx(20.0)
    assert final.context == [{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "Hello"}]
    body, auth = seen[0]
    assert auth == "Bearer sk-local" and body["stream"] is True
    assert body["messages"] == [{"role": "system", "content": "be terse"},
                                {"role": "user", "content": "hi"}]

    # Second turn sends the history back (the server is stateless).
    [c async for c in client.generate("again", context=final.context, system="be terse")]
    assert [m["content"] for m in seen[1][0]["messages"]] == ["be terse", "hi", "Hello", "again"]
    assert await client.list_models() == ["unsloth/Qwen3-Coder:Q4_K_M"]
    await client.aclose()


async def test_openai_client_error_message():
    client = OpenAIChatClient("http://olares:8080", "m",
                              transport=llama_cpp_transport([], seen=[], status=503))
    with pytest.raises(LLMError, match="model is loading"):
        [c async for c in client.generate("hi")]
    await client.aclose()


def test_make_llm_client_picks_protocol():
    ollama = make_llm_client(Settings(olares_host="10.0.0.5"))
    assert isinstance(ollama, OllamaClient)
    assert ollama.base_url == "http://10.0.0.5:11434" and ollama.model == "llama3.1:8b"

    oa = make_llm_client(Settings(olares_host="10.0.0.5", llm_api="OpenAI", llm_url="http://10.0.0.5:8080"))
    assert isinstance(oa, OpenAIChatClient)
    assert oa.base_url == "http://10.0.0.5:8080/v1"
    assert oa.model == ""  # Ollama's default model name isn't sent to llama.cpp

    with pytest.raises(ValueError, match="llm_api"):
        make_llm_client(Settings(llm_api="vllm"))


def test_override_file_wins_over_bundled_config(tmp_path, monkeypatch):
    bundled = tmp_path / "config.toml"
    bundled.write_text('olares_host = "192.168.1.50"\nollama_model = "llama3.1:8b"\n')
    override = tmp_path / "external" / "config.toml"
    monkeypatch.setenv("TUI_OS_CONFIG_OVERRIDE", str(override))

    s = Settings.load(bundled)  # no override file yet
    assert s.olares_host == "192.168.1.50" and s.resolved_llm_api == "ollama"

    override.parent.mkdir()
    override.write_text('llm_api = "openai"\nllm_url = "https://llama.example.olares.com"\n')
    s = Settings.load(bundled)
    assert s.olares_host == "192.168.1.50"  # untouched keys keep the bundled value
    assert s.resolved_llm_api == "openai"
    assert s.resolved_llm_url == "https://llama.example.olares.com"

    monkeypatch.setenv("LLM_MODEL", "qwen")  # env still beats files
    assert Settings.load(bundled).resolved_llm_model == "qwen"

    override.write_text('llm_api = "openai\n')
    with pytest.raises(ValueError, match="invalid TOML in .*external"):
        Settings.load(bundled)


async def test_terminal_tab_chats_over_openai(settings):
    """End to end: the Terminal tab streams from a llama.cpp-style server."""
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.terminal import TerminalPane

    seen: list = []
    client = OpenAIChatClient("http://olares:8080", "",
                              transport=llama_cpp_transport(["Hello", " there"], seen=seen))
    app = TuiOS(settings, telemetry_source=DemoTelemetry(seed=1), ollama_client=client)
    async with app.run_test(size=(147, 46)) as pilot:
        pane = app.query_one(TerminalPane)
        await pilot.pause(0.2)
        await pilot.press(*"hi", "enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if pane.context:
                break
        assert pane.context[-1] == {"role": "assistant", "content": "Hello there"}
        assert seen and seen[0][0]["messages"][-1] == {"role": "user", "content": "hi"}
