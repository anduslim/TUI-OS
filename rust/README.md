# TUI OS v2 (Rust)

Phase 1 spike of the v2 rearchitecture: the Olares One does the work, the Boox only draws.

```
Boox app (Kotlin shell + Rust core)            Olares One
  keys, taps, swipes  ───────────────▶  tuiosd ── PTY ── tmux / shell / claude
  cell-grid frames    ◀───────────────    alacritty_terminal emulates the PTY,
        WebSocket (postcard), port 7420   sends only the rows that changed
```

| Crate | What it is |
|-------|------------|
| `crates/tuios-proto` | Messages and the cell grid + row diff shared by both sides |
| `crates/tuiosd` | Server: runs one shared session in a PTY, streams frames, encodes keys for the program's terminal modes |
| `crates/tuios-client` | Client core: reconnecting connection, grid model, software renderer (JetBrains Mono, six-colour E-ink palette) |
| `crates/tuios-desktop` | `tuios`, the laptop client (winit + softbuffer) |
| `crates/tuios-android` | JNI core of the Boox app; draws into the SurfaceView's buffer and posts only the dirty rectangle |
| `android/` | Gradle project: `MainActivity` (keys, settings), `TermView` (surface, touch) |

Phase 1 scope: one shared session (default `tmux new -A -s main`), shared-token auth, no tabs or panels yet. The fingerprint pairing, tabs and side panels come in phase 2 and 3 (plan doc in the project).

## Set up the server on the Olares One

```bash
# 1. Binary (from the GitHub Release or the "tuiosd" CI artifact)
sudo install -m 755 tuiosd-x86_64-linux /usr/local/bin/tuiosd

# 2. Config
mkdir -p ~/.config/tuiosd
tuiosd --print-config > ~/.config/tuiosd/config.toml
sed -i "s/change-me-to-a-long-random-string/$(openssl rand -hex 24)/" ~/.config/tuiosd/config.toml
grep token ~/.config/tuiosd/config.toml   # you need this on the Boox

# 3. Service (runs as user olares, restarts on failure and at boot)
sudo cp tuiosd.service /etc/systemd/system/      # rust/deploy/tuiosd.service
sudo systemctl daemon-reload && sudo systemctl enable --now tuiosd
journalctl -u tuiosd -f
```

Then allow **TCP 7420** in Olares Settings > VPN, the same way as port 9090 for the Prometheus proxy, so the Boox can reach `100.64.0.1:7420` over the VPN.

## Install the Boox app

```bash
adb install -r tuios2-<version>-arm64-v8a.apk
# Settings over adb, so you don't type the token:
adb shell am start -n org.olares.tuios2/.MainActivity \
    --es url ws://100.64.0.1:7420 --es token <token>
```

It installs beside v1 (`org.olares.tuios2`), signed with the same release key. Use Fast refresh mode for it in the Boox app settings, as for v1.

| Input | Does |
|-------|------|
| Keyboard cover | Everything goes to the session; Ctrl+B etc. reach tmux. BACK is Escape |
| Ctrl+V, Shift+Insert, long press | Paste the Android clipboard |
| Ctrl+Alt+S | Settings (server, token, font size) |
| Tap | Click (tmux/k9s with mouse mode) |
| Vertical swipe | Scroll back through output (mouse wheel inside tmux) |
| Two-finger tap | Show or hide the on-screen keyboard |

## Laptop client

```bash
cargo run --release -p tuios-desktop -- --url ws://100.64.0.1:7420   # TUIOS_TOKEN=<token> in the env
```

`--eink` previews the Boox palette, `--font-size 20` changes the size. It shares the session with the Boox.

## Develop

```bash
cargo test --workspace      # includes an end-to-end test: real PTY + server + client over a WebSocket
cargo fmt --all && cargo clippy --workspace --all-targets -- -D warnings
```

Android build (CI does this in `.github/workflows/rust.yml`):

```bash
cargo install cargo-ndk
cargo ndk -t arm64-v8a -P 30 -o android/app/src/main/jniLibs build --release -p tuios-android
gradle -p android assembleRelease
```
