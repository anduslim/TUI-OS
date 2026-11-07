"""Mock content for the Mail, Calendar and Browser apps.

Kept separate from the widgets so it can be swapped for real providers
(IMAP / CalDAV / ...) behind the same shapes later.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


# ---------------------------------------------------------------------- mail
@dataclass
class MailMessage:
    sender: str
    subject: str
    date: dt.datetime
    body: str
    to: str = "me@olares.local"
    new: bool = False
    flagged: bool = False
    replied: bool = False
    attachment: bool = False

    @property
    def flags(self) -> str:
        return (
            ("N" if self.new else " ")
            + ("!" if self.flagged else " ")
            + ("r" if self.replied else " ")
            + ("+" if self.attachment else " ")
        )


def mailbox(now: dt.datetime | None = None) -> list[MailMessage]:
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)

    def ago(**kw: float) -> dt.datetime:
        return now - dt.timedelta(**kw)

    return [
        MailMessage(
            "Olares Alerts", "[FIRING] GPU temperature above 80C for 5m", ago(minutes=12),
            "Alert: GPUHighTemperature\nSeverity: warning\nInstance: olares-one:9400\n\n"
            "DCGM_FI_DEV_GPU_TEMP = 81\n\nThe GPU has been above the 80C threshold for 5\n"
            "minutes. Check fan curve and the current Ollama workload.\n\n"
            "-- \nAlertmanager @ olares-one", new=True, flagged=True,
        ),
        MailMessage(
            "CI Bot", "build #482 passed: tui-os@main", ago(minutes=47),
            "Pipeline: tui-os / main\nCommit: a1b2c3d  Add telemetry worker\n\n"
            "  lint ........ ok   (12s)\n  test ........ ok   (41s)\n  package ..... ok   (9s)\n\n"
            "Artifacts: tui_os-0.1.0-py3-none-any.whl", new=True,
        ),
        MailMessage(
            "Mara Chen", "Re: model routing for the code assistant", ago(hours=3),
            "> Should we keep the 14B coder model resident?\n\n"
            "Yes - VRAM headroom is fine at ~11 GB. Let's pin it with\n"
            "OLLAMA_KEEP_ALIVE=-1 and lazy-load the larger chat model.\n\n"
            "I'll update the systemd unit tonight.\n\nMara", replied=True,
        ),
        MailMessage(
            "Boox Updates", "Firmware 4.1 release notes", ago(days=1, hours=2),
            "Highlights\n\n* Faster partial refresh in 'Regal' mode\n"
            "* Improved keyboard cover latency\n* Per-app refresh profiles\n\n"
            "Tip: set the terminal app to 'Fast' mode for TUI work.", attachment=True,
        ),
        MailMessage(
            "Jonas Weber", "Pairing session tomorrow?", ago(days=1, hours=7),
            "Hey,\n\nfree for pairing on the Prometheus exporter tomorrow at 10?\n"
            "I'd like to add per-process VRAM metrics.\n\nJ.",
        ),
        MailMessage(
            "GitHub", "[tui-os] Issue #12: Calendar should sync via CalDAV", ago(days=2),
            "Opened by a contributor:\n\nIt would be great if the calendar tab could\n"
            "read from a CalDAV server (Radicale on the Olares box).\n\n"
            "-- \nReply to this email directly or view it on GitHub.",
        ),
        MailMessage(
            "Olares Team", "Weekly digest: new apps in the market", ago(days=4),
            "This week:\n\n  - Open WebUI 0.6\n  - Radicale (CalDAV/CardDAV)\n"
            "  - Gitea 1.22\n\nOpen the Olares Market to install.",
        ),
        MailMessage(
            "Me", "notes: e-ink TUI ideas", ago(days=6), "- page-based scrolling only\n"
            "- no blinking cursor\n- batch token repaints\n- pure colours only\n",
        ),
    ]


# ------------------------------------------------------------------ calendar
@dataclass
class Appointment:
    start: dt.time
    end: dt.time | None
    title: str


def appointments_for(day: dt.date) -> list[Appointment]:
    """Deterministic, weekday-based schedule so any month looks populated."""
    t = dt.time
    weekday = day.weekday()
    items: list[Appointment] = []
    if weekday < 5:
        items.append(Appointment(t(9, 30), t(9, 45), "Stand-up"))
    if weekday == 0:
        items.append(Appointment(t(11, 0), t(12, 0), "Sprint planning"))
    if weekday == 1:
        items.append(Appointment(t(14, 0), t(15, 30), "Deep work: telemetry exporter"))
    if weekday == 2:
        items.append(Appointment(t(10, 0), t(11, 0), "Pairing w/ Jonas"))
        items.append(Appointment(t(16, 0), t(16, 30), "1:1 Mara"))
    if weekday == 3:
        items.append(Appointment(t(13, 0), t(14, 0), "Model eval review"))
    if weekday == 4:
        items.append(Appointment(t(15, 0), t(16, 0), "Demo + retro"))
    if day.day == 1:
        items.append(Appointment(t(8, 0), None, "Rotate API keys"))
    if day.day == 15:
        items.append(Appointment(t(19, 0), t(21, 0), "Olares meetup"))
    if weekday == 5 and day.day % 2:
        items.append(Appointment(t(10, 0), t(12, 0), "Long run"))
    return sorted(items, key=lambda a: a.start)


TODOS: list[tuple[int, str]] = [
    (1, "Ship telemetry sidebar"),
    (1, "Pin coder model in VRAM"),
    (2, "CalDAV provider for calendar tab"),
    (3, "IMAP provider for mail tab"),
    (5, "Write README for Boox setup"),
    (9, "Try 'Regal' refresh mode"),
]


# ------------------------------------------------------------------- browser
ABOUT_PAGES: dict[str, str] = {
    "about:home": """
<title>TUI-OS start page</title>
<h1>TUI-OS :: start</h1>
<p>Text-mode browser. Type a URL or search terms in the location bar,
or enter a link <b>number</b> to follow it.</p>
<h2>Local</h2>
<ul>
 <li><a href="about:help">Keyboard help</a></li>
 <li><a href="about:olares">Olares One services</a></li>
</ul>
<h2>Docs</h2>
<ul>
 <li><a href="https://docs.python.org/3/">Python 3 documentation</a></li>
 <li><a href="https://textual.textualize.io/">Textual documentation</a></li>
 <li><a href="https://github.com/ollama/ollama/blob/main/docs/api.md">Ollama API reference</a></li>
 <li><a href="https://prometheus.io/docs/prometheus/latest/querying/basics/">PromQL basics</a></li>
</ul>
<h2>Search</h2>
<ul><li><a href="https://lite.duckduckgo.com/lite/">DuckDuckGo Lite</a></li></ul>
""",
    "about:help": """
<title>Browser help</title>
<h1>Browser keys</h1>
<pre>
  u / g        focus location bar (URL, search terms, or link number)
  1..99 Enter  in the location bar: follow link [n]
  b            back
  r            reload
  space, PgDn  next page
  PgUp         previous page
  Home / End   top / bottom
</pre>
<p><a href="about:home">Back to start</a></p>
""",
    "about:olares": """
<title>Olares One services</title>
<h1>Olares One</h1>
<p>Services this workspace talks to:</p>
<pre>
  Ollama       :11434   /api/generate (streaming)
  Prometheus   :9090    /api/v1/query
  node-export  :9100    CPU / disk / network
  dcgm-export  :9400    GPU temperature / VRAM
</pre>
<p>Configure the host with <code>OLARES_HOST</code> or
<code>~/.config/tui-os/config.toml</code>.</p>
<p><a href="about:home">Back to start</a></p>
""",
}
