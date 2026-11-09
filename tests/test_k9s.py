"""K9s tab: key encoding, colour clamping, and a real SSH round trip.

The integration tests start an asyncssh *server* on localhost with a fake
``k9s`` that paints a screen in arbitrary colours, records keystrokes and
reports PTY size changes, so the whole path (TOFU host keys, public-key
auth, skin upload, PTY sizing, key passthrough, resize) runs for real.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncssh
import pytest

from tui_os.palette import TOKENS
from tui_os.services.ssh import SshTarget, SshTerminalSession, ensure_client_key, k9s_skin, known_host
from tui_os.widgets.term_emulator import TerminalEmulator, cell_style, encode_key


# ------------------------------------------------------------------ units
@pytest.mark.parametrize(
    ("key", "char", "app", "expected"),
    [
        ("up", None, False, "\x1b[A"),
        ("up", None, True, "\x1bOA"),
        ("left", None, False, "\x1b[D"),
        ("enter", "\r", False, "\r"),
        ("escape", "\x1b", False, "\x1b"),
        ("colon", ":", False, ":"),
        ("slash", "/", False, "/"),
        ("0", "0", False, "0"),
        ("ctrl+c", "\x03", False, "\x03"),
        ("ctrl+d", None, False, "\x04"),
        ("pagedown", None, False, "\x1b[6~"),
        ("shift+tab", None, False, "\x1b[Z"),
        ("ctrl+right", None, False, "\x1b[1;5C"),
        ("alt+x", "x", False, "\x1bx"),
    ],
)
def test_encode_key(key, char, app, expected):
    assert encode_key(key, char, app_cursor=app) == expected


@pytest.mark.parametrize(
    ("fg", "bg", "reverse", "expected"),
    [
        ("default", "default", False, ("#FFFFFF", "#000000")),
        ("5f87af", "default", False, ("#FFFFFF", "#000000")),   # steel blue text → not blue on black
        ("ff8700", "default", False, ("#FFFF00", "#000000")),   # orange → yellow
        ("default", "1c1c1c", False, ("#FFFFFF", "#000000")),   # near-black bg → black
        ("ffffff", "005fd7", False, ("#FFFFFF", "#0000FF")),    # selection → white on blue
        ("ffffff", "00d700", False, ("#000000", "#00FF00")),    # green fill → black text
        ("cyan", "default", True, ("#000000", "#00FF00")),      # reversed accent → black on green
        ("red", "default", False, ("#FF0000", "#000000")),
    ],
)
def test_cell_style_clamps_to_palette_and_pairing(fg, bg, reverse, expected):
    f, b, *_ = cell_style(fg, bg, bold=False, underline=False, reverse=reverse)
    assert (f, b) == expected and f in TOKENS and b in TOKENS


def test_skin_uses_only_palette_tokens():
    import re

    colours = {c.upper() for c in re.findall(r'"(#[0-9a-fA-F]{6})"', k9s_skin())}
    assert colours == set(TOKENS)


# --------------------------------------------------------- fake k9s server
class FakeK9s:
    """Server side: what the 'remote' saw."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.sizes: list[tuple[int, int]] = []
        self.keys: list[str] = []
        self.command: str | None = None
        self.env_ok = False

    async def handle(self, process: asyncssh.SSHServerProcess) -> None:
        cmd = process.command or ""
        if "cat >" in cmd:  # skin/config upload
            name = cmd.rsplit("/", 1)[-1].strip('"')
            self.files[name] = await process.stdin.read()
            process.exit(0)
            return
        self.command = cmd
        self.env_ok = "K9S_CONFIG_DIR" in cmd and "COLORTERM=truecolor" in cmd
        cols, rows, *_ = process.get_terminal_size()
        self.sizes.append((cols, rows))
        self.paint(process, cols, rows)
        while True:
            try:
                data = await process.stdin.read(1024)
            except asyncssh.TerminalSizeChanged as exc:
                self.sizes.append((exc.width, exc.height))
                self.paint(process, exc.width, exc.height)
                continue
            if not data:
                break
            self.keys.append(data)
            if data == "\x03":  # ctrl+c quits, like k9s
                break
            process.stdout.write(f"\x1b[{rows};1H\x1b[38;2;255;135;0mkey {data!r}\x1b[0m")
        process.exit(0)

    @staticmethod
    def paint(process, cols: int, rows: int) -> None:
        # Full redraw in non-palette colours, like a real k9s skin would.
        out = ["\x1b[2J\x1b[H", "\x1b[38;2;95;135;175m Context: olares \x1b[0m"]
        out.append(f"\x1b[2;1H\x1b[48;2;0;95;215m\x1b[38;2;255;255;255m{' pods(all)[3] '.center(cols)}\x1b[0m")
        out.append(f"\x1b[3;1H\x1b[1;38;5;46mNAME{'':20}STATUS\x1b[0m")
        out.append(f"\x1b[{rows};{max(1, cols - 6)}H{cols}x{rows}")
        process.stdout.write("".join(out))


@pytest.fixture
async def ssh_server(tmp_path: Path):
    fake = FakeK9s()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key_path = tmp_path / "client" / "id_ed25519"
    pub = ensure_client_key(client_key_path)
    server = await asyncssh.create_server(
        asyncssh.SSHServer,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=asyncssh.import_authorized_keys(pub + "\n"),
        process_factory=fake.handle,
        line_editor=False,  # raw PTY bytes, as a real sshd gives k9s
    )
    port = server.sockets[0].getsockname()[1]
    target = SshTarget(
        host="127.0.0.1",
        username="olares",
        port=port,
        key_path=client_key_path,
        known_hosts_path=tmp_path / "client" / "known_hosts",
    )
    yield fake, target, host_key
    server.close()
    await server.wait_closed()


# ------------------------------------------------------------- integration
async def test_session_round_trip(ssh_server):
    fake, target, host_key = ssh_server
    out: list[str] = []
    exited = asyncio.Event()
    session = SshTerminalSession(target, out.append, lambda status, err: exited.set())

    await session.start(100, 30)
    for _ in range(100):
        if "100x30" in "".join(out):
            break
        await asyncio.sleep(0.02)
    assert fake.sizes == [(100, 30)] and fake.env_ok
    assert fake.files["eink.yaml"] == k9s_skin() and "skin: eink" in fake.files["config.yaml"]
    assert known_host(target)  # TOFU recorded the server key
    assert host_key.export_public_key().decode().split()[1] in target.known_hosts_path.read_text()

    for key in (":", "p", "o", "\r", "\x1b[A", "0"):
        session.write(key)
    session.resize(120, 40)
    await asyncio.sleep(0.3)
    assert "".join(fake.keys).startswith(":po\r\x1b[A0")
    assert fake.sizes[-1] == (120, 40)

    session.write("\x03")
    await asyncio.wait_for(exited.wait(), 5)
    await session.close()


async def test_changed_host_key_is_refused(ssh_server):
    from tui_os.services.ssh import HostKeyChanged

    _fake, target, _key = ssh_server
    target.known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    target.known_hosts_path.write_text(f"{target.host_token} {other}\n")
    session = SshTerminalSession(target, lambda _d: None, lambda *_a: None)
    with pytest.raises(HostKeyChanged):
        await session.start(80, 24)


async def test_k9s_tab_end_to_end(ssh_server, settings):
    """F5 → connect → k9s output clamped to the palette → keys + resize."""
    from dataclasses import replace

    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.k9s import K9sPane

    fake, target, _ = ssh_server

    def factory(_t, on_output, on_exit):
        return SshTerminalSession(target, on_output, on_exit)

    app = TuiOS(replace(settings, k9s_autoconnect=True), telemetry_source=DemoTelemetry(seed=1),
                k9s_session_factory=factory)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f5")
        pane = app.query_one(K9sPane)
        for _ in range(100):
            await pilot.pause(0.05)
            if pane.state == "connected" and fake.sizes:
                break
        assert pane.state == "connected", pane.error
        term = app.query_one(TerminalEmulator)
        assert app.focused is term
        # The PTY is exactly the panel's interior: no clipping.
        assert fake.sizes[0] == (term.size.width, term.size.height) == term.grid_size
        await pilot.pause(0.2)
        assert "pods(all)[3]" in "\n".join(term.text_lines())

        # Every rendered segment obeys the palette.
        for y in range(term.size.height):
            for seg in term.render_line(y):
                if seg.style and seg.text.strip():
                    assert seg.style.color.triplet.hex.upper() in TOKENS
                    assert seg.style.bgcolor.triplet.hex.upper() in TOKENS

        await pilot.press("colon", "p", "o", "enter", "up", "escape", "1")
        await pilot.pause(0.3)
        assert "".join(fake.keys) == ":po\r\x1b[A\x1b1"

        await pilot.press("ctrl+b")  # app keeps Ctrl+B: telemetry hides, k9s widens
        await pilot.pause(0.4)
        assert fake.sizes[-1][0] == fake.sizes[0][0] + 30
        assert "".join(fake.keys) == ":po\r\x1b[A\x1b1"  # Ctrl+B was not sent to k9s


async def test_quit_while_connected_is_clean(ssh_server, settings):
    """Regression: the session's exit callback fired after the pane's children
    were removed during shutdown and raised NoMatches."""
    from dataclasses import replace

    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.k9s import K9sPane

    _fake, target, _ = ssh_server
    app = TuiOS(replace(settings, k9s_autoconnect=True), telemetry_source=DemoTelemetry(seed=1),
                k9s_session_factory=lambda _t, o, e: SshTerminalSession(target, o, e))
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f5")
        for _ in range(100):
            await pilot.pause(0.05)
            if app.query_one(K9sPane).state == "connected":
                break
        assert app.query_one(K9sPane).state == "connected"
    # leaving the context unmounts everything; any exception would surface here


async def test_k9s_tab_degrades_when_ssh_cannot_load(settings, monkeypatch):
    """If the native cryptography module can't load on a device, only the K9s
    tab reports it; the app itself still starts (asyncssh is imported lazily)."""
    import sys

    from tui_os.app import TuiOS
    from tui_os.services import ssh
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.k9s import K9sPane

    def broken():
        raise ImportError("dlopen failed: _rust.abi3.so")

    monkeypatch.setattr(ssh, "_asyncssh", broken)
    monkeypatch.setenv("TUI_OS_DATA", str(Path(__file__).parent / "__no_key_here__"))
    app = TuiOS(settings, telemetry_source=DemoTelemetry(seed=1))
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f5")
        await pilot.pause()
        pane = app.query_one(K9sPane)
        assert pane.state == "error" and "SSH is unavailable" in (pane.error or "")
        await pilot.press("f1")  # rest of the app works
        await pilot.pause()
        assert app.query_one("#workspace").active == "terminal"
