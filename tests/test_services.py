from __future__ import annotations

import httpx
import pytest

from tui_os.config import DEFAULT_QUERIES, Settings
from tui_os.services.ollama import OllamaClient, OllamaError
from tui_os.services.telemetry import PrometheusTelemetry
from tui_os.services.web import normalise_url, render_html
from tui_os.widgets.terminal import render_reply

from .conftest import ollama_transport


async def test_ollama_streams_tokens_and_context():
    client = OllamaClient("http://x:11434", "m", transport=ollama_transport(["a", "b", "c"]))
    chunks = [c async for c in client.generate("hi")]
    assert "".join(c.text for c in chunks) == "abc"
    assert chunks[-1].done and chunks[-1].context == [1, 2, 3]
    assert chunks[-1].tokens_per_second == pytest.approx(6.0)
    await client.aclose()


async def test_ollama_error_surfaces():
    client = OllamaClient("http://x:11434", "nope", transport=ollama_transport([], status=404))
    with pytest.raises(OllamaError, match="not found"):
        async for _ in client.generate("hi"):
            pass
    await client.aclose()


def _prom_transport(values: dict[str, str | None]):
    def handler(request: httpx.Request) -> httpx.Response:
        expr = request.url.params["query"]
        key = next(k for k, q in DEFAULT_QUERIES.items() if q == expr)
        value = values.get(key)
        result = [] if value is None else [{"metric": {}, "value": [0, value]}]
        return httpx.Response(200, json={"status": "success",
                                         "data": {"resultType": "vector", "result": result}})
    return httpx.MockTransport(handler)


async def test_prometheus_snapshot_parses_and_reports_missing():
    src = PrometheusTelemetry("http://p:9090", DEFAULT_QUERIES, transport=_prom_transport({
        "cpu_temp": "61.5", "gpu_temp": None, "vram_used": str(8 * 1024**3),
        "vram_total": str(24 * 1024**3), "net_rx": "1000", "net_tx": "NaN",
    }))
    snap = await src.fetch()
    assert snap.source == "prometheus"
    assert snap.cpu_temp == 61.5
    assert snap.vram_ratio == pytest.approx(1 / 3)
    assert set(snap.missing) == {"gpu_temp", "net_tx"}
    await src.aclose()


async def test_prometheus_offline_when_unreachable():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    src = PrometheusTelemetry("http://p:9090", DEFAULT_QUERIES, transport=httpx.MockTransport(boom))
    snap = await src.fetch()
    assert snap.source == "offline" and snap.error == "connection refused"
    await src.aclose()


def test_settings_layering(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('olares_host = "10.0.0.5"\ntelemetry_interval = 3\n[queries]\ncpu_temp = "x"\n')
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder")
    s = Settings.load(cfg, telemetry_interval=7.0)
    assert s.resolved_ollama_url == "http://10.0.0.5:11434"
    assert s.resolved_prometheus_url == "http://10.0.0.5:9090"
    assert s.ollama_model == "qwen2.5-coder"
    assert s.telemetry_interval == 7.0
    assert s.queries["cpu_temp"] == "x" and "gpu_temp" in s.queries


def test_html_render_numbers_links():
    page = render_html('<title>T</title><h1>Hi</h1><p>see <a href="/a">A</a> and '
                       '<a href="https://b.test/">B</a></p><script>x</script>', "https://site.test/x")
    assert page.title == "T"
    assert page.links == ["https://site.test/a", "https://b.test/"]
    assert "A[1]" in page.body.plain and "B[2]" in page.body.plain
    assert "x" not in page.body.plain.replace("x", "", 0).split("Hi")[0]


def test_normalise_url():
    assert normalise_url("example.com") == "https://example.com"
    # Tailscale: MagicDNS (no dot), tailnet IPs, *.ts.net and ports → http
    assert normalise_url("olares-one") == "http://olares-one"
    assert normalise_url("olares-one:8080/grafana") == "http://olares-one:8080/grafana"
    assert normalise_url("100.101.102.103:9090") == "http://100.101.102.103:9090"
    assert normalise_url("olares-one.tail1234.ts.net") == "http://olares-one.tail1234.ts.net"
    assert normalise_url("localhost:11434") == "http://localhost:11434"
    assert normalise_url("kubernetes").startswith("https://lite.duckduckgo.com")
    assert normalise_url("about:help") == "about:help"
    assert normalise_url("textual docs").startswith("https://lite.duckduckgo.com/lite/?q=textual+docs")


def test_render_reply_highlights_code():
    text = render_reply("Use `ls`:\n```bash\nls -la\n```\ndone")
    styles = {text.plain[s.start:s.end]: str(s.style) for s in text.spans}
    assert styles["ls"] == "#00FF00"
    assert styles["ls -la"] == "#00FF00"
