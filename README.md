# TUI-OS

A text-mode "personal OS" for doing development from an **Onyx Boox Tab Ultra C**
(colour E-ink, Android 11) against an **Olares One** home server. It is built with
[Textual](https://textual.textualize.io/) and has four apps in one workspace:

| Tab | Key | What it is |
|-----|-----|------------|
| 1 Terminal | `F1` / `Alt+1` | Prompt environment that streams from the Ollama server on the box (`/api/generate`) |
| 2 Mail     | `F2` / `Alt+2` | Index and pager split laid out like neomutt (mock mailbox) |
| 3 Calendar | `F3` / `Alt+3` | Appointments, month view and TODO laid out like calcurse (mock data) |
| 4 Browser  | `F4` / `Alt+4` | Text viewport like w3m: real HTTP fetches, numbered links, `about:` pages |

On the left, a 30-column **telemetry panel** shows CPU and GPU temperature, VRAM, and
network RX/TX from Prometheus. **`Ctrl+B`** hides or shows it, and the workspace
reflows to the full width straight away.

```
 TUI-OS   olares://192.168.1.50                                   Sat 07 Nov  18:03
┌ OLARES ONE ─────────┐  1 Terminal   2 Mail   3 Calendar   4 Browser
│ CPU TEMP            │ ● qwen2.5-coder:14b  @ http://olares:11434  ready
│       50 °C         │
│ ██████████········· │ ❯ show me asyncio
│ GPU TEMP            │   ▌ Here is a quick asyncio example:
│       49 °C         │   ▌  python
│ ██████████········· │   ▌ import asyncio
│ VRAM                │   ▌ ...
│   9.8/24 GB         │
│ ████████··········· │
│ NET  RX ↓  1.2 MB/s │ ┌──────────────────────────────────────────────────────┐
│ NET  TX ↑  387 KB/s │ │ ask the local model…  (/help)                        │
└──────────── DEMO ───┘ └──────────────────────────────────────────────────────┘
```

## Quick start

```bash
pip install -e .            # or: pip install textual httpx
tui-os --demo               # synthetic telemetry, no server needed
tui-os --host 192.168.1.50  # real Olares box: Ollama :11434, Prometheus :9090
```

Two common ways to run it on the Boox:

* **SSH (recommended).** Install TUI-OS on the Olares box, then from Termux or any SSH
  client on the tablet run `ssh olares -t tui-os --host 127.0.0.1`. The tablet only
  draws the terminal; all network calls stay on the box.
* **Local in Termux.** `pkg install python && pip install tui-os`, then
  `tui-os --host <olares-ip>`.

For the cleanest refresh, set the terminal app's Boox refresh mode to **Fast** or
**Regal**. Press `Ctrl+R` at any time to force a full repaint and clear ghosting.

## Configuration

Settings are applied in this order, and later ones win: defaults, then
`~/.config/tui-os/config.toml` (or `$TUI_OS_CONFIG`), then environment variables,
then CLI flags.

```toml
olares_host = "192.168.1.50"
ollama_model = "qwen2.5-coder:14b"
ollama_system_prompt = "You are a terse senior engineer."
telemetry_interval = 5        # seconds between Prometheus polls
stream_flush_interval = 0.35  # seconds between LLM repaints (0 = every token)

[queries]                     # PromQL; each must reduce to one scalar
cpu_temp = 'max(node_hwmon_temp_celsius{chip=~".*k10temp.*"})'
gpu_temp = 'max(DCGM_FI_DEV_GPU_TEMP)'
```

| Env var | Flag | Default |
|---------|------|---------|
| `OLARES_HOST` | `--host` | `127.0.0.1` |
| `OLLAMA_URL` | `--ollama-url` | `http://$HOST:11434` |
| `OLLAMA_MODEL` | `--model` | `llama3.1:8b` |
| `PROMETHEUS_URL` | `--prometheus-url` | `http://$HOST:9090` |
| `TUI_OS_TELEMETRY_INTERVAL` | `--interval` | `5` |
| `TUI_OS_STREAM_FLUSH` | `--flush` | `0.35` |
| `TUI_OS_DEMO` | `--demo` | off |

The default PromQL queries assume **node-exporter** (CPU temperature, network) and the
**NVIDIA DCGM exporter** (GPU temperature, VRAM). If your box exposes different metric
names, override them under `[queries]`. The panel lists any metric that returns no data.

## Architecture

```
tui_os/
├── app.py               App shell: layout, Ctrl+B toggle, tab keys, telemetry worker
├── app.tcss             E-ink stylesheet (design tokens at the top)
├── theme.py             "eink-dark" Textual theme: pure #000/#FFF + solid accents
├── config.py            Settings (defaults → TOML → env → CLI)
├── services/            Network code with no Textual imports
│   ├── ollama.py        Async NDJSON streaming client for /api/generate
│   ├── telemetry.py     Prometheus /api/v1/query poller + demo source
│   └── web.py           HTML → Rich Text renderer with numbered links
├── widgets/
│   ├── telemetry_panel.py
│   ├── terminal.py      Streaming worker, batched repaint, /commands
│   ├── mail.py  calendar.py  browser.py
└── mock_data.py         Mailbox, schedule, about: pages
```

**Telemetry loop.** `TuiOS.telemetry_loop` is an exclusive async `@work` worker. Each
cycle it awaits `source.fetch()` (all PromQL queries run at once with
`asyncio.gather`), posts a `TelemetryUpdated` message, and sleeps for the rest of the
interval. A failed poll shows as `OFFLINE` in the panel and does not stop the loop.

**LLM streaming.** `OllamaClient.generate()` is an async generator over httpx's
`aiter_lines()`, so tokens arrive without blocking the event loop.
`TerminalPane._stream_reply` (an exclusive worker) adds each token to the active
`ResponseBlock` buffer. An interval timer repaints that block every
`stream_flush_interval` seconds, but only while a reply is streaming. `Esc` cancels the
worker, which closes the HTTP stream. The `context` returned by Ollama is passed back
on the next prompt, so a conversation keeps its history across turns.

### E-ink design rules (enforced in code)

* **No animation.** `App.animation_level = "none"`, and every scroll call passes
  `animate=False`. Hiding the sidebar is a single layout change, with no slide.
* **Paging, not smooth scrolling.** Use Space/PgUp/PgDn in the transcript, pager and
  browser.
* **Fewer repaints.** Tokens are repainted in batches. Telemetry values are rounded
  (whole °C, 0.1 GB, 20-cell bars) and a row repaints only when its rendered text
  changes. The clock updates once a minute, and the input cursor does not blink.
* **Pure colours only.** Black `#000000` backgrounds, white `#FFFFFF` text, and solid
  green `#00FF00`, blue `#0000FF`, yellow `#FFFF00` and red `#FF0000`.
  `luminosity_spread=0` so Textual does not generate grey shades, there are no
  `░▒▓` dithered glyphs, and hover effects are turned off.

## Development

```bash
pip install -e '.[dev]'
pytest                       # services (httpx MockTransport) + Textual pilot tests
textual run --dev tui_os.app:TuiOS   # live CSS reload with textual-dev
```

## Roadmap

* IMAP/SMTP provider for Mail and a CalDAV provider (e.g. Radicale on Olares) for
  Calendar. Both tabs already read from `mock_data`-shaped objects.
* A real PTY shell tab next to the LLM terminal.
