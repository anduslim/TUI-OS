"""Shell tab: a plain SSH terminal (login shell, or e.g. tmux) on the node."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from tui_os.config import Settings
from tui_os.services.ssh import SshTerminalSession, remote_command
from tui_os.widgets.term_emulator import TerminalEmulator

from .test_k9s import ssh_server  # noqa: F401 - pytest fixture


def test_shell_target_defaults_to_olares_host_and_login_shell():
    from tui_os.widgets.shell import shell_target

    t = shell_target(Settings(olares_host="olares-one"))
    assert (t.host, t.username, t.mode) == ("olares-one", "olares", "shell")
    assert remote_command(t) is None  # server starts the login shell
    t = shell_target(Settings(shell_host="box", shell_command="tmux new -A -s main"))
    assert t.host == "box" and remote_command(t) == "tmux new -A -s main"


async def _wait(cond, tries=100):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.03)
    return cond()


async def test_shell_session_runs_command_without_k9s_setup(ssh_server):  # noqa: F811
    fake, target, _ = ssh_server
    target = replace(target, mode="shell", command="tmux new -A -s main")
    out: list[str] = []
    session = SshTerminalSession(target, out.append, lambda *_a: None)
    await session.start(90, 25)
    assert await _wait(lambda: fake.sizes)
    assert fake.command == "tmux new -A -s main"
    assert fake.files == {}  # no k9s skin uploaded
    assert fake.sizes == [(90, 25)]
    await session.close()


async def test_shell_session_without_command_requests_a_shell(ssh_server):  # noqa: F811
    fake, target, _ = ssh_server
    session = SshTerminalSession(replace(target, mode="shell", command=""), lambda _d: None,
                                 lambda *_a: None)
    await session.start(80, 24)
    assert await _wait(lambda: fake.sizes)
    assert fake.command == ""  # shell request: no command string
    await session.close()


async def test_shell_tab_connects_at_start_and_passes_tmux_keys(ssh_server, settings):  # noqa: F811
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.shell import ShellPane

    fake, target, _ = ssh_server
    app = TuiOS(replace(settings, start_tab="shell", shell_autoconnect=True),
                telemetry_source=DemoTelemetry(seed=1),
                k9s_session_factory=lambda _t, o, e: SshTerminalSession(
                    replace(target, mode="shell"), o, e))
    async with app.run_test(size=(147, 46)) as pilot:
        pane = app.query_one(ShellPane)
        for _ in range(100):
            await pilot.pause(0.05)
            if pane.state == "connected" and fake.sizes:
                break
        assert pane.state == "connected", pane.error
        assert app.query_one("#workspace").active == "shell"
        term = pane.query_one(TerminalEmulator)
        assert app.focused is term
        assert fake.sizes[0] == (term.size.width, term.size.height)

        await pilot.press("l", "s", "enter", "ctrl+b", "ctrl+r")
        await pilot.pause(0.3)
        # Ctrl+B (tmux prefix) and Ctrl+R (history search) reach the remote.
        assert "".join(fake.keys) == "ls\r\x02\x12"
        assert app.query_one("#telemetry").display  # telemetry did not toggle

        await pilot.press("f2")  # app still owns tab keys
        await pilot.pause()
        assert app.query_one("#workspace").active == "mail"


async def test_late_connect_failure_does_not_pull_focus_back(settings):
    """Regression: a K9s connect that failed after the user had moved to
    another tab focused the K9s idle screen, and TabbedContent jumped back."""
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry

    class SlowFailing:
        def __init__(self, *_a):
            pass

        async def start(self, cols, rows):
            await asyncio.sleep(0.3)
            raise OSError("connection refused")

        def write(self, data): ...
        def resize(self, cols, rows): ...
        async def close(self): ...

    app = TuiOS(replace(settings, k9s_autoconnect=True), telemetry_source=DemoTelemetry(seed=1),
                k9s_session_factory=SlowFailing)
    async with app.run_test(size=(147, 46)) as pilot:
        await pilot.press("f5")
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause(0.6)  # the failure lands while Mail is shown
        assert app.query_one("#workspace").active == "mail"
