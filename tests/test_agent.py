"""Coding agents (Claude Code, opencode) in the SSH terminal and the Agent tab."""

from __future__ import annotations

from dataclasses import replace

from textual import events

from tui_os.services.ssh import SshTerminalSession, remote_command
from tui_os.widgets.term_emulator import TerminalEmulator

from .test_k9s import ssh_server  # noqa: F401 - pytest fixture


def _emulator() -> tuple[TerminalEmulator, list[str]]:
    sent: list[str] = []
    return TerminalEmulator(on_input=sent.append), sent


def test_prefixed_csi_is_not_read_as_colours():
    """opencode sends CSI > 4;1 m (modifyOtherKeys); pyte read it as bold+underline."""
    term, _ = _emulator()
    term.feed("\x1b[>4;1m\x1b[>0q\x1b[<u\x1b[=1uok")
    cell = term._screen.buffer[0][0]
    assert term.text_lines()[0].startswith("ok")
    assert not cell.bold and not cell.underscore


def test_sequence_split_across_chunks():
    term, _ = _emulator()
    term.feed("a\x1b[>4")
    term.feed(";1mb\x1b[1")
    term.feed("mc")
    line = term._screen.buffer[0]
    assert term.text_lines()[0].startswith("abc")
    assert not line[1].bold and line[2].bold


def test_terminal_queries_are_answered():
    term, sent = _emulator()
    term.feed("\x1b[c")  # primary device attributes (Claude Code asks)
    term.feed("hi\x1b[6n")  # cursor position (opencode asks)
    assert sent[0].startswith("\x1b[?") and sent[0].endswith("c")
    assert sent[1] == "\x1b[1;3R"


def test_paste_is_bracketed_only_when_asked():
    term, sent = _emulator()
    term.on_paste(events.Paste("a\nb"))
    term.feed("\x1b[?2004h")
    term.on_paste(events.Paste("c\nd\x1b[201~"))
    assert sent == ["a\nb", "\x1b[200~c\nd[201~\x1b[201~"]


def test_agent_command_runs_in_a_login_shell():
    """Over SSH the command gets a bare shell (no .bashrc): kubectl/tmux exit 127."""
    import shlex

    from tui_os.widgets.agent import login_shell

    cmd = "tmux new -A -s agent kubectl exec -it -n ns deploy/x -- bash -ilc 'claude --x'"
    wrapped = login_shell(cmd)
    assert wrapped.startswith('exec "${SHELL:-/bin/bash}" -ilc ')
    assert shlex.split(wrapped)[-1] == cmd
    assert login_shell("  ") == ""


async def test_agent_tab_replaces_llm_terminal(ssh_server, settings):  # noqa: F811
    from tui_os.app import TuiOS
    from tui_os.services.telemetry import DemoTelemetry
    from tui_os.widgets.agent import AgentPane, login_shell

    fake, target, _ = ssh_server
    s = replace(settings, agent_command="tmux new -A -s agent claude", start_tab="shell",
                shell_autoconnect=False)
    app = TuiOS(s, telemetry_source=DemoTelemetry(seed=1),
                k9s_session_factory=lambda t, o, e: SshTerminalSession(
                    replace(target, mode=t.mode, command=t.command), o, e))
    async with app.run_test(size=(147, 46)) as pilot:
        pane = app.query_one(AgentPane)
        wrapped = login_shell("tmux new -A -s agent claude")
        assert remote_command(pane.target) == wrapped
        await pilot.press("ctrl+6")
        term = pane.query_one(TerminalEmulator)
        for _ in range(100):
            await pilot.pause(0.05)
            if pane.state == "connected" and app.focused is term:
                break
        assert pane.state == "connected", pane.error
        assert fake.command == wrapped
        await pilot.press("escape", "shift+tab", "ctrl+c")
        await pilot.pause(0.3)
        assert "".join(fake.keys).endswith("\x1b\x1b[Z\x03")


async def test_without_agent_command_tab_6_is_the_llm_terminal(make_app):
    from tui_os.widgets.agent import AgentPane
    from tui_os.widgets.terminal import TerminalPane

    app = make_app()
    async with app.run_test(size=(147, 46)):
        assert app.query_one("#terminal-pane").__class__ is TerminalPane
        assert not app.query(AgentPane)


def test_dec_line_drawing_renders_box_characters():
    """tmux draws pane borders with the DEC special graphics charset."""
    term, _ = _emulator()
    term.feed("\x1b(0lqqk\x1b(B é \x1b)0\x0emqj\x0f ok")
    assert term.text_lines()[0].startswith("┌──┐ é └─┘ ok")
