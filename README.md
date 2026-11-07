# TUI-OS

A text-mode "personal OS" for doing development from an **Onyx Boox Tab Ultra C**
(colour E-ink, Android 11) against an **Olares One** home server. It is built with
[Textual](https://textual.textualize.io/) and has four apps in one workspace:

| Tab | Key | What it is |
|-----|-----|------------|
| 1 Terminal | `F1` / `Alt+1` | Prompt environment that streams from the Ollama server on the box (`/api/generate`) |
| 2 Mail     | `F2` / `Alt+2` | Index and pager split laid out like neomutt (mock mailbox) |
| 3 Calendar | `F3` / `Alt+3` | Appointments, month view and TODO laid out like calcurse (mock data) |
| 4 Browser  | `F4` / `Alt+4` | **APK:** native Android WebView restyled to the E-ink palette. **Desktop:** text viewport like w3m |
| 5 K9s (Cluster) | `F5` / `Alt+5` | `k9s` on the Olares One over SSH, in an E-ink skin |

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

## Quick start (desktop development)

```bash
pip install -e .            # or: pip install textual httpx
tui-os --demo               # synthetic telemetry, no server needed
tui-os --host 192.168.1.50  # real Olares box: Ollama :11434, Prometheus :9090
python main.py              # the same app inside the Kivy host the APK uses (pip install kivy pyte)
```

## Android APK (Boox Tab Ultra C)

On the tablet TUI-OS ships as a standalone APK, **not** as a script run in Termux.
`buildozer.spec` packages it with Buildozer / python-for-android (Kivy SDL2 bootstrap),
targets API 30 (Android 11) on `arm64-v8a`, and locks the app to full-screen landscape.

```bash
# 1. Point the APK at your box (copied to private storage on first launch)
$EDITOR android/config.toml

# 2. Build (Linux or WSL; downloads the Android SDK/NDK on first run, ~30 min)
pip install buildozer "cython<3.1"
buildozer android debug            # -> bin/olares_workspace_tui-1.0.0-arm64-v8a-debug.apk

# 3. Install on the tablet over USB
adb install -r bin/*.apk
```

Or run the **Android APK** workflow in GitHub Actions (`.github/workflows/apk.yml`),
which builds the same spec and uploads the APK as an artifact.

### How a Textual app runs without a terminal

Textual needs a terminal and an Android app process doesn't have one, so the APK
puts a small virtual terminal in between (`tui_os/host/`):

```
TuiOS (Textual, own thread + asyncio loop)
   │ ANSI out                      ▲ events.Key / Resize / Mouse
   ▼                               │
EmbeddedDriver ──▶ pyte.Screen ──▶ TerminalBridge.take_dirty_rows()
                                   │
                         Kivy TerminalView (paints changed rows only,
                         JetBrains Mono, 6-colour palette) ◀── hardware keyboard, taps
```

* **No shell, no Termux, no subprocess.** Textual runs in-process with a custom driver
  (`bridge.py`). The Kivy UI thread never blocks on it.
* **Hardware keyboard.** The app holds the system keyboard claim (and takes it back if it
  is released), so SDL keeps sending key events. With the keyboard cover attached,
  Android keeps the soft keyboard hidden. Special keys and Ctrl/Alt combinations go
  straight to Textual (`keymap.py`); printable characters come from layout-aware text
  events, with a US-layout fallback. `Esc` / BACK never closes the app.
* **Screen.** Locked to landscape, full screen with sticky immersive mode (status and
  navigation bars hidden), black presplash, and `singleTask` so relaunching returns to
  the running session.
* **E-ink.** Only rows that changed are repainted, and an idle TUI draws nothing. A
  vertical swipe sends PgUp/PgDn, a tap is a click, and returning from sleep triggers one
  full clean repaint.

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

## Browser tab (WebView) and Tailscale

In the APK, the Browser tab is a real Android **WebView** placed exactly over the
tab's panel (cell-grid aligned). Every page gets an injected user style
(`tui_os/host/eink_web.py`): black surfaces, white text, green links and focus
rings, white-on-blue selection, and no shadows, gradients, transitions or smooth
scrolling.

* **Tailscale:** install Tailscale on the Boox and on the Olares One. WebView and
  app traffic then route over the tailnet automatically. The URL bar understands
  tailnet names: `olares-one`, `olares-one:8080/grafana`, `100.x.y.z:9090` and
  `*.ts.net` open over `http://` (cleartext is allowed for these; WireGuard
  already encrypts the tailnet). Dotted public names open over `https://`, and
  anything with spaces is a search. Set `olares_host = "olares-one"` and every
  service (Ollama, Prometheus, k9s SSH) uses MagicDNS.
* **Keys:** F4 or Enter sends keys to the page (typing, Tab, arrows, PgUp/PgDn).
  **Esc** hands keys back to the TUI: `u` URL bar, `b` back, `r` reload, Enter back
  into the page. F1–F5, Ctrl+B, Ctrl+L and Ctrl+Q always reach the TUI. Android
  BACK walks page history, then releases.
* **Known limit:** the WebView is a native view on top of the TUI, so a toast
  notification can't draw over the page area.

On a desktop terminal the tab stays the text-mode browser.

## K9s (Cluster) tab

`k9s` runs **on the Olares One** in a remote PTY over SSH (asyncssh). A built-in
terminal emulator draws it inside the panel, sized to the panel's exact cell grid.
Resizes, such as Ctrl+B widening the workspace, are sent to the PTY, so nothing
clips.

Setup on the Olares One:

1. Install k9s (for example from the k9s GitHub releases) and make sure the SSH
   user can read a kubeconfig. If it isn't `~/.kube/config`, set `k9s_kubeconfig`.
2. Allow the tablet to log in. Either:
   * open the K9s tab once: it shows the **device public key** (generated on the
     tablet on first use), which you add to `~/.ssh/authorized_keys`; or
   * enable **Tailscale SSH** on the node (`tailscale set --ssh`). No keys are
     needed; the tailnet identity is used.
3. In `android/config.toml` (or `~/.config/tui-os/config.toml`):

   ```toml
   olares_host = "olares-one"     # MagicDNS name
   k9s_user = "olares"
   # k9s_host = ""                 # defaults to olares_host
   # k9s_kubeconfig = "/etc/kubernetes/admin.conf"
   # k9s_refresh_rate = 5          # seconds; slow is kind to E-ink
   ```

Behaviour:

* **Host key:** trusted on first use and pinned in `known_hosts` in the app's data
  dir. A changed key is refused with a clear message.
* **Skin:** each session uploads the E-ink skin and an E-ink config
  (`refreshRate`, `noIcons`, `logoless`) to `~/.config/tui-os/k9s` and runs k9s
  with `K9S_CONFIG_DIR` pointing there. Your own `~/.config/k9s` is never touched.
  To use the skin elsewhere: `tui_os/k9s/skins/eink.yaml`.
* **Palette clamp:** whatever k9s (or a plugin) prints is clamped to the six
  tokens and the pairing rules before it's drawn.
* **Keys:** everything goes to k9s (arrows, Esc, Enter, `:` commands, `/` filters,
  digits for namespaces, Ctrl combinations) except the app's F1–F5, Ctrl+B,
  Ctrl+R and Ctrl+Q. k9s's own Ctrl+B page-up is also on PgUp. Ctrl+C quits k9s;
  Enter reconnects.

## Architecture

```
tui_os/
├── host/                APK host (no terminal needed)
│   ├── android_host.py  Native WebView via pyjnius (+ android/src/…/EinkBrowser.java)
│   ├── eink_web.py      CSS/JS injected into every page
│   ├── services.py      HostServices protocol the Textual side talks to
│   ├── bridge.py        Embedded Textual driver + pyte screen + input injection
│   ├── grid.py          147×46 logical grid → device pixels
│   ├── glyphs.py        Pixel-exact box-drawing / block characters
│   ├── keymap.py        Hardware keyboard → Textual keys
│   └── kivy_app.py      Full-screen landscape Kivy app
├── app.py               App shell: layout, Ctrl+B toggle, tab keys, telemetry worker
├── app.tcss             E-ink stylesheet (design tokens at the top)
├── theme.py             "eink-dark" Textual theme: pure #000/#FFF + solid accents
├── config.py            Settings (defaults → TOML → env → CLI)
├── palette.py           The six tokens + clamp for colours we don't control
├── k9s/                 E-ink k9s skin (skins/eink.yaml) and config.yaml
├── services/            Network code with no Textual imports
│   ├── ssh.py           asyncssh PTY session, TOFU host keys, device key, skin upload
│   ├── ollama.py        Async NDJSON streaming client for /api/generate
│   ├── telemetry.py     Prometheus /api/v1/query poller + demo source
│   └── web.py           HTML → Rich Text renderer with numbered links
├── widgets/
│   ├── telemetry_panel.py
│   ├── k9s.py           K9s tab (states, device key, session wiring)
│   ├── term_emulator.py pyte terminal widget: palette clamp, key encoding, resize
│   ├── webview.py       Browser tab for the APK (WebSurface overlay placeholder)
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

### Interface spec (enforced in code and by `tests/test_spec.py`)

| Rule | Implementation |
|------|----------------|
| Six colours only: `#000000 #FFFFFF #00FF00 #0000FF #FFFF00 #FF0000` | Theme with `luminosity_spread=0`. Textual's built-in tints (DataTable header, palette separator) are overridden. A test renders every screen through the APK bridge and checks every cell. |
| Blue is a fill only, with white text. Green fill takes black text. Red fill takes white text. | Tabs, status bars, selections and code fences: white on blue. Title chip: black on green. Critical meters: white on red. |
| JetBrains Mono 14/20 everywhere, no size hierarchy. Italic only for placeholders. | Bold and solid colour blocks carry hierarchy. Font is bundled in `assets/fonts/` (OFL). |
| Cell grid 8.4 × 20 px, 147 × 46 cells | The APK host locks Textual to 147×46 and scales cells to the panel (2.008× = 16.87 × 40.2 px on 2480×1860, `tui_os/host/grid.py`). |
| Meters: exactly 20 cells of `█` and `·`, never `░▒▓` | `telemetry_panel.bar()` |
| Panel titles inside the top border. Focus = heavy green border. | `border-title`. Inputs and the calendar box switch `solid` white to `heavy` green. Mail and Browser use a heavy left gutter that is always present, so focus changes colour and never shifts layout. |
| `LIVE` / `OFFLINE` in the telemetry bottom border | `border-subtitle`, red when offline |
| Active tab = solid blue, no sliding underline | The `Underline` widget is hidden and `Tabs` is 1 row high |
| Static green block cursor | `cursor_blink = False`, green `input--cursor` |
| Status bars white on blue. Key hints: green key, white label. | Mail, Calendar and Browser bars; `Footer` |
| Telemetry rows: label + value, then meter. Ollama node. States ok / warn / offline. | `MetricRow`, `LlmNode`, `TelemetryPanel.state`. The terminal posts `LlmState` messages to the panel. |

E-ink behaviour on top of that:

* **No animation.** `App.animation_level = "none"`, and every scroll call passes
  `animate=False`. Ctrl+B is a single layout change: the workspace takes back the 30
  columns in the same frame.
* **Paging, not smooth scrolling.** Use Space/PgUp/PgDn in the app, or a vertical swipe
  on the tablet.
* **Fewer repaints.** LLM tokens are repainted in batches (350 ms). Telemetry is rounded
  first (whole °C, 0.1 GB, 5 % meter steps) and a row repaints only when its rendered
  text changes. The clock updates once a minute. The host only redraws dirty rows.
* **Crisp edges.** The host draws box-drawing and block characters as pixel-snapped
  rectangles (`tui_os/host/glyphs.py`), so borders join without gaps and never
  anti-alias to grey.

## Development

```bash
pip install -e '.[dev]'
pytest                       # services (httpx MockTransport) + Textual pilot tests
textual run --dev tui_os.app:TuiOS   # live CSS reload with textual-dev
```

## CI/CD and releases

| Workflow | When | What |
|----------|------|------|
| `tests.yml` | every push and PR | pytest on Python 3.11 and 3.14 (3.14 is the CPython the APK ships), `buildozer.spec` dependency resolution, Java helper type-check |
| `apk.yml` | PRs, `main` | Builds the APK and uploads it as a workflow artifact |
| `apk.yml` | **Run workflow** with `release: X.Y.Z`, or a `vX.Y.Z` tag | Runs the tests, builds the APK as version X.Y.Z, then publishes a **GitHub Release** `vX.Y.Z` with the APK and its SHA-256 |

To cut a release, either:

* **GitHub UI:** Actions → *Android APK* → *Run workflow* → `release: 1.1.0`. The
  workflow creates the `v1.1.0` tag on the selected commit; or
* **git:** `git tag v1.1.0 && git push origin v1.1.0`.

Signing: add the repository secrets `ANDROID_KEYSTORE_BASE64`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` and `ANDROID_KEY_PASSWORD` for a
release-signed APK with a stable key. That's needed for in-place updates. Without
them the release ships a debug-signed APK, which installs but may need an
uninstall before the next update.

## Roadmap

* IMAP/SMTP provider for Mail and a CalDAV provider (e.g. Radicale on Olares) for
  Calendar. Both tabs already read from `mock_data`-shaped objects.
* A real PTY shell tab next to the LLM terminal.
* An in-app settings screen for the host, model and k9s target (today: `android/config.toml` before building).
* Keyboard-driven link hints in the WebView (like Vimium) for mouse-free navigation.
