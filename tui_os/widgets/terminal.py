"""Terminal tab: a streaming prompt environment for the on-box Ollama server.

Architecture
------------
* :class:`~tui_os.services.ollama.OllamaClient` yields tokens from an async
  HTTP stream (httpx). Nothing here blocks the event loop.
* :meth:`TerminalPane._stream_reply` runs as an exclusive Textual *async
  worker*. It appends every token to the active :class:`ResponseBlock`'s
  buffer immediately.
* A paused/resumed interval timer repaints the block every
  ``stream_flush_interval`` seconds while a reply is streaming. On E-ink this
  turns ~30 partial refreshes per second into ~3 without delaying tokens
  (0 = repaint on every token for LCD screens).
* ``Esc`` cancels the worker; the HTTP stream is closed by the async context
  manager unwinding on ``CancelledError``.
"""

from __future__ import annotations

import re
import time

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Input, Static
from textual.worker import Worker, WorkerState

from ..services.ollama import OllamaClient, OllamaError

STYLE_TEXT = "#FFFFFF"
STYLE_CODE = "#00FF00"
STYLE_FENCE = "bold #FFFFFF on #0000FF"
STYLE_PROMPT = "bold #00FF00"
STYLE_DIM = "#FFFFFF"
STYLE_ERROR = "bold #FFFFFF on #FF0000"

_INLINE_CODE = re.compile(r"`([^`\n]+)`")

HELP_TEXT = """\
Commands
  /help            this help
  /models          list models on the server
  /model NAME      switch model
  /system TEXT     set a system prompt (empty to clear)
  /reset           forget conversation context
  /clear           clear the screen (keeps context)
Keys
  Enter send · Esc cancel stream · PgUp/PgDn page transcript"""


def render_reply(text: str) -> Text:
    """Fence-aware renderer: code in solid green, fences on solid blue."""
    out = Text()
    in_code = False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            label = stripped[3:].strip() or ("code" if in_code else "")
            out.append(f" {label} " if in_code else " end ", STYLE_FENCE)
        elif in_code:
            out.append(line, STYLE_CODE)
        elif stripped.startswith("#"):
            out.append(line, f"bold underline {STYLE_TEXT}")
        else:
            pos = 0
            for match in _INLINE_CODE.finditer(line):
                out.append(line[pos : match.start()], STYLE_TEXT)
                out.append(match.group(1), STYLE_CODE)
                pos = match.end()
            out.append(line[pos:], STYLE_TEXT)
        if i < len(lines) - 1:
            out.append("\n")
    return out


class ChatEntry(Static):
    """A finished line in the transcript (prompt echo, notice, error)."""


class ResponseBlock(Static):
    """Assistant reply that grows as tokens arrive."""

    def __init__(self) -> None:
        super().__init__(classes="response")
        self._parts: list[str] = []
        self.dirty = False
        self.token_count = 0

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def append(self, token: str) -> None:
        if token:
            self._parts.append(token)
            self.token_count += 1
            self.dirty = True

    def repaint(self, cursor: bool = True) -> None:
        body = render_reply(self.text)
        if cursor:
            body.append("▌", STYLE_CODE)  # static block cursor, never blinks
        self.update(body)
        self.dirty = False


class TerminalPane(Vertical):
    BINDINGS = [
        Binding("escape", "cancel_stream", "Cancel", show=True),
        Binding("pageup", "page_up", "PgUp", show=False),
        Binding("pagedown", "page_down", "PgDn", show=False),
    ]

    def __init__(
        self,
        client: OllamaClient,
        *,
        flush_interval: float,
        system_prompt: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.client = client
        self.flush_interval = max(0.0, flush_interval)
        self.system_prompt = system_prompt
        self.context: list[int] | None = None
        self._active: ResponseBlock | None = None
        self._flush_timer: Timer | None = None
        self._follow = True

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static("", id="term-header")
        yield VerticalScroll(id="transcript")
        prompt = Input(placeholder="ask the local model…  (/help)", id="prompt")
        prompt.cursor_blink = False  # blinking forces a refresh every 0.5s on E-ink
        yield prompt

    def on_mount(self) -> None:
        self._set_header("connecting…", ok=None)
        if self.flush_interval > 0:
            self._flush_timer = self.set_interval(self.flush_interval, self._flush, pause=True)
        self._log(Text("TUI-OS terminal · local LLM via Ollama · /help for commands", STYLE_DIM))
        self.probe_server()

    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    @property
    def is_streaming(self) -> bool:
        return self._active is not None

    # ----------------------------------------------------------------- helpers
    def _set_header(self, status: str, *, ok: bool | None) -> None:
        mark, style = {True: ("●", STYLE_PROMPT), False: ("✗", "bold #FF0000"), None: ("○", STYLE_DIM)}[ok]
        header = Text()
        header.append(f" {mark} ", style)
        header.append(f"{self.client.model}", "bold #FFFFFF")
        header.append(f"  @ {self.client.base_url}  ", STYLE_DIM)
        header.append(status, style)
        self.query_one("#term-header", Static).update(header)

    def _log(self, renderable: Text, classes: str = "") -> ChatEntry:
        entry = ChatEntry(renderable, classes=classes)
        self.transcript.mount(entry)
        self.transcript.scroll_end(animate=False)
        return entry

    def _flush(self) -> None:
        block = self._active
        if block is None or not block.dirty:
            return
        scroller = self.transcript
        follow = scroller.scroll_y >= scroller.max_scroll_y - 1
        block.repaint()
        if follow:
            scroller.scroll_end(animate=False)

    # ----------------------------------------------------------------- workers
    @work(exclusive=True, group="ollama-probe", exit_on_error=False)
    async def probe_server(self) -> None:
        try:
            models = await self.client.list_models()
        except httpx.HTTPError as exc:
            self._set_header(f"unreachable ({type(exc).__name__})", ok=False)
            return
        if models and self.client.model not in models:
            self._set_header(f"model not pulled · {len(models)} available (/models)", ok=False)
        else:
            self._set_header(f"ready · {len(models)} models", ok=True)

    @work(exclusive=True, group="ollama", exit_on_error=False)
    async def _stream_reply(self, prompt: str) -> None:
        block = ResponseBlock()
        await self.transcript.mount(block)
        self._active = block
        block.repaint()
        self.transcript.scroll_end(animate=False)
        if self._flush_timer:
            self._flush_timer.resume()
        self._set_header("generating…", ok=True)
        started = time.monotonic()
        final_stats = None
        try:
            async for chunk in self.client.generate(
                prompt, context=self.context, system=self.system_prompt or None
            ):
                block.append(chunk.text)
                if self._flush_timer is None:
                    self._flush()  # per-token repaint mode
                if chunk.done:
                    self.context = chunk.context or self.context
                    final_stats = chunk
        finally:
            if self._flush_timer:
                self._flush_timer.pause()
            block.repaint(cursor=False)
            self._active = None

        elapsed = time.monotonic() - started
        tps = final_stats.tokens_per_second if final_stats else None
        summary = f"{block.token_count} chunks · {elapsed:.1f}s"
        if tps:
            summary += f" · {tps:.1f} tok/s"
        self._log(Text(f"  ── {summary}", STYLE_DIM), classes="meta")
        self._set_header("ready", ok=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "ollama":
            return
        if event.state == WorkerState.CANCELLED:
            self._log(Text(" cancelled ", STYLE_ERROR), classes="meta")
            self._set_header("cancelled", ok=None)
        elif event.state == WorkerState.ERROR:
            err = event.worker.error
            if isinstance(err, OllamaError):
                msg = str(err)
            elif isinstance(err, httpx.ConnectError):
                msg = f"cannot reach {self.client.base_url}"
            else:
                msg = f"{type(err).__name__}: {err}"
            self._log(Text(f" error ", STYLE_ERROR) + Text(f" {msg}", "bold #FF0000"), classes="meta")
            self._set_header("error", ok=False)

    # ----------------------------------------------------------------- input
    @on(Input.Submitted, "#prompt")
    def _submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/"):
            event.input.clear()
            self._command(text)
            return
        if self.is_streaming:
            self.app.notify("Still streaming — Esc to cancel.", severity="warning")
            return
        event.input.clear()
        self._log(Text("❯ ", STYLE_PROMPT) + Text(text, "bold #FFFFFF"), classes="user")
        self._stream_reply(text)

    def _command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        if cmd == "/help":
            self._log(Text(HELP_TEXT, STYLE_DIM), classes="meta")
        elif cmd == "/clear":
            self.transcript.remove_children()
        elif cmd == "/reset":
            self.context = None
            self._log(Text("context cleared", STYLE_DIM), classes="meta")
        elif cmd == "/model":
            if not arg:
                self._log(Text(f"current model: {self.client.model}", STYLE_DIM), classes="meta")
                return
            self.client.model = arg
            self.context = None
            self._log(Text(f"model → {arg} (context reset)", STYLE_PROMPT), classes="meta")
            self.probe_server()
        elif cmd == "/models":
            self._list_models()
        elif cmd == "/system":
            self.system_prompt = arg
            self._log(Text(f"system prompt {'set' if arg else 'cleared'}", STYLE_DIM), classes="meta")
        else:
            self._log(Text(f"unknown command {cmd} — /help", "bold #FF0000"), classes="meta")

    @work(group="ollama-probe", exit_on_error=False)
    async def _list_models(self) -> None:
        try:
            models = await self.client.list_models()
        except httpx.HTTPError as exc:
            self._log(Text(f"cannot list models: {type(exc).__name__}", "bold #FF0000"), classes="meta")
            return
        body = Text("models on server:\n", STYLE_DIM)
        for name in models or ["(none pulled)"]:
            marker = "● " if name == self.client.model else "  "
            body.append(f"  {marker}{name}\n", STYLE_CODE if marker.strip() else STYLE_TEXT)
        body.rstrip()
        self._log(body, classes="meta")

    # ----------------------------------------------------------------- actions
    def action_cancel_stream(self) -> None:
        if self.is_streaming:
            self.workers.cancel_group(self, "ollama")

    def action_page_up(self) -> None:
        self.transcript.scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.transcript.scroll_page_down(animate=False)
