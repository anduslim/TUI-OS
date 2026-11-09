from __future__ import annotations

import asyncio
import datetime as dt

import httpx

from tui_os.config import Settings
from tui_os.mock_data import MailMessage
from tui_os.services.ical import ICalCalendar, expand, parse_ics
from tui_os.services.mail import html_to_text, parse_message, quote_reply

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:standup\r
DTSTART:20261102T093000\r
DTEND:20261102T094500\r
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261130T235959Z\r
EXDATE:20261111T093000\r
SUMMARY:Stand-up\\, daily\r
LOCATION:Room 1\r
BEGIN:VALARM\r
ACTION:DISPLAY\r
SUMMARY:should be ignored\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:standup\r
RECURRENCE-ID:20261113T093000\r
DTSTART:20261113T110000\r
DTEND:20261113T111500\r
SUMMARY:Stand-up (moved)\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:trip\r
DTSTART;VALUE=DATE:20261120\r
DTEND;VALUE=DATE:20261123\r
SUMMARY:Trip to\r
  Penang\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:board\r
DTSTART:20261101T100000\r
DURATION:PT1H\r
RRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=3\r
SUMMARY:Board review\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:gone\r
DTSTART:20261110T080000\r
STATUS:CANCELLED\r
SUMMARY:Cancelled thing\r
END:VEVENT\r
END:VCALENDAR\r
"""


def _titles(by_day, day):
    return [a.title for a in by_day.get(day, [])]


def test_ics_parse_and_expand():
    events = parse_ics(ICS)
    assert len(events) == 5
    by_day = expand(events, dt.date(2026, 11, 1), dt.date(2027, 3, 1))
    nov = lambda d: dt.date(2026, 11, d)  # noqa: E731
    assert _titles(by_day, nov(2)) == ["Stand-up, daily"]
    assert by_day[nov(2)][0].location == "Room 1"
    assert by_day[nov(2)][0].start == dt.time(9, 30) and by_day[nov(2)][0].end == dt.time(9, 45)
    assert _titles(by_day, nov(3)) == []  # Tuesday
    assert _titles(by_day, nov(11)) == []  # EXDATE
    assert _titles(by_day, nov(13)) == ["Stand-up (moved)"]  # RECURRENCE-ID override
    assert by_day[nov(13)][0].start == dt.time(11, 0)
    assert "Stand-up, daily" not in _titles(by_day, dt.date(2026, 12, 2))  # UNTIL
    # All-day, DTEND exclusive, folded SUMMARY line.
    for d in (20, 21, 22):
        assert "Trip to Penang" in _titles(by_day, nov(d))
        assert by_day[nov(d)][0].all_day
    assert "Trip to Penang" not in _titles(by_day, nov(23))
    # Last Friday of the month, COUNT=3: Nov 27, Dec 25, Jan 29 then stop.
    board = sorted(d for d, appts in by_day.items() if any(a.title == "Board review" for a in appts))
    assert board == [nov(27), dt.date(2026, 12, 25), dt.date(2027, 1, 29)]
    assert all("Cancelled thing" not in _titles(by_day, d) for d in by_day)


def test_ics_utc_times_become_local():
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20261110T120000Z\n"
           "DTEND:20261110T130000Z\nSUMMARY:Call\nEND:VEVENT\nEND:VCALENDAR\n")
    by_day = expand(parse_ics(ics), dt.date(2026, 11, 1), dt.date(2026, 12, 1))
    local = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc).astimezone()
    assert [a.start for a in by_day[local.date()]] == [local.time().replace(tzinfo=None)]


async def test_ical_calendar_fetch_rewrites_webcal():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=ICS)

    cal = ICalCalendar("webcal://calendar.example/basic.ics", transport=httpx.MockTransport(handler))
    by_day = await cal.fetch(dt.date(2026, 11, 9))
    assert seen == ["https://calendar.example/basic.ics"]
    assert _titles(by_day, dt.date(2026, 11, 2)) == ["Stand-up, daily"]


RAW = b"""From: Mara Chen <mara@example.com>\r
To: me@example.com\r
Subject: =?utf-8?q?Caf=C3=A9_plans?=\r
Date: Mon, 09 Nov 2026 10:00:00 +0000\r
Message-ID: <abc@example.com>\r
MIME-Version: 1.0\r
Content-Type: multipart/alternative; boundary="b"\r
\r
--b\r
Content-Type: text/html; charset=utf-8\r
\r
<p>Hi <b>there</b></p><p>See you &amp; bye</p>\r
--b--\r
"""


def test_parse_message_html_fallback_and_flags():
    msg = parse_message(RAW, uid="42", flags=["\\Flagged"])
    assert msg.sender == "Mara Chen" and msg.sender_address == "mara@example.com"
    assert msg.subject == "Café plans"
    assert msg.uid == "42" and msg.new and msg.flagged and not msg.replied
    assert "Hi there" in msg.body and "See you & bye" in msg.body
    assert msg.message_id == "<abc@example.com>"
    assert "> Hi there" in quote_reply(msg)
    assert html_to_text("<style>x{}</style>a<br>b") == "a\nb"


class FakeMailbox:
    def __init__(self):
        self.calls = []
        now = dt.datetime(2026, 11, 9, 10, 0)
        self.inbox = [
            MailMessage("Mara", "Hello", now, "body one", new=True, uid="7", sender_address="m@x"),
            MailMessage("Jonas", "Lunch?", now - dt.timedelta(hours=1), "body two", uid="6"),
        ]

    def fetch(self, count):
        self.calls.append(("fetch", count))
        return [MailMessage(**m.__dict__) for m in self.inbox]

    def set_flag(self, uid, flag, on):
        self.calls.append(("flag", uid, flag, on))

    def expunge(self, uids):
        self.calls.append(("expunge", uids))

    def send(self, to, subject, body, **headers):
        self.calls.append(("send", to, subject, headers.get("in_reply_to", "")))


async def _until(pilot, cond, tries=40):
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


async def test_mail_pane_live_mailbox(make_app):
    from tui_os.widgets.mail import MailPane

    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        box = FakeMailbox()
        pane = MailPane(Settings(mail_refresh_interval=0, mail_fetch_count=5), mailbox=box, id="live-mail")
        await app.query_one("#mail").mount(pane)
        await pilot.press("f2")
        assert await _until(pilot, lambda: len(pane.messages) == 2)
        assert ("fetch", 5) in box.calls
        # Showing the first (new) message marks it read on the server.
        assert await _until(pilot, lambda: ("flag", "7", "\\Seen", True) in box.calls)
        pane.query_one("#mail-index").focus()
        await pilot.press("f")
        assert await _until(pilot, lambda: ("flag", "7", "\\Flagged", True) in box.calls)
        await pilot.press("d", "dollar_sign")
        assert await _until(pilot, lambda: ("expunge", ["7"]) in box.calls)
        assert [m.subject for m in pane.messages] == ["Lunch?"]
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("ctrl+s")
        assert await _until(pilot, lambda: any(c[0] == "send" for c in box.calls))
        assert ("send", "m@x", "Re: Lunch?", "") not in box.calls  # replied to Jonas
        send = next(c for c in box.calls if c[0] == "send")
        assert send[2] == "Re: Lunch?"


async def test_calendar_pane_live_feed(make_app):
    from tui_os.widgets.calendar import CalendarPane

    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        source = ICalCalendar("https://cal.example/x.ics",
                              transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ICS)))
        pane = CalendarPane(Settings(calendar_refresh_interval=0), source=source, id="live-cal")
        pane.today = pane.selected = dt.date(2026, 11, 2)
        await app.query_one("#calendar").mount(pane)
        await pilot.press("f3")
        assert await _until(pilot, lambda: bool(pane._by_day))
        await asyncio.sleep(0)
        assert "Stand-up, daily" in str(pane.query_one("#cal-appts").render())


async def test_mail_pane_reconfigure_switches_from_demo(make_app, monkeypatch):
    from tui_os.widgets import mail as mail_mod

    box = FakeMailbox()
    monkeypatch.setattr(mail_mod, "mailbox_from_settings", lambda s: box if s and s.mail_configured else None)
    app = make_app()
    async with app.run_test(size=(160, 48)) as pilot:
        pane = app.query_one(mail_mod.MailPane)
        assert not pane.live and pane.messages  # demo mailbox
        pane.reconfigure(Settings(mail_address="a@b", mail_password="p", mail_refresh_interval=0))
        assert await _until(pilot, lambda: [m.subject for m in pane.messages] == ["Hello", "Lunch?"])
        assert pane.live


def test_settings_below_queries_table_are_rescued(tmp_path):
    from tui_os import config

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'olares_host = "100.64.0.1"\n'
        'ical_url = "https://top.example/a.ics"\n'
        "[queries]\n"
        "net_rx = 'sum(rx)'\n"
        'vault_url = "https://vault.example/"\n'
        'vault_email = "a@b.c"\n'
        'ical_url = "https://ignored.example/b.ics"\n'
    )
    s = Settings.load(cfg)
    assert s.vault_url == "https://vault.example/" and s.vault_email == "a@b.c"
    assert s.ical_url == "https://top.example/a.ics"  # top level wins
    assert "vault_url" not in s.queries and s.queries["net_rx"] == "sum(rx)"
    assert len(config.LOAD_WARNINGS) == 1
    assert "vault_url, vault_email, ical_url" in config.LOAD_WARNINGS[0]
    # A clean file leaves no warning behind.
    cfg.write_text('[queries]\nnet_rx = "x"\n')
    Settings.load(cfg)
    assert config.LOAD_WARNINGS == []


async def test_google_embed_link_gets_a_clear_error():
    import pytest

    from tui_os.services.ical import CalendarError

    cal = ICalCalendar("https://calendar.google.com/calendar/embed?src=x%40gmail.com&ctz=Asia/Singapore")
    with pytest.raises(CalendarError, match="Secret address in iCal format"):
        await cal.fetch()
