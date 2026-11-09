"""Gmail (or any IMAP/SMTP server) for the Mail tab.

Uses only the standard library (imaplib, smtplib, email), so nothing new has
to be built for the APK. Both libraries block, so callers run these methods
in a worker thread. Every call opens its own connection: the tab refreshes a
few times an hour, and a fresh login is simpler than keeping an idle IMAP
session alive across Wi-Fi sleep on the tablet.
"""

from __future__ import annotations

import datetime as dt
import email
import email.policy
import email.utils
import html
import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage, Message

from ..mock_data import MailMessage

_FETCH_ITEMS = "(UID FLAGS BODY.PEEK[])"
_MAX_BODY_CHARS = 20_000


class MailError(Exception):
    """A failure worth showing to the user as-is (login refused, no network)."""


@dataclass(frozen=True)
class MailAccount:
    address: str
    password: str
    name: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    timeout: float = 30.0

    @property
    def from_header(self) -> str:
        return email.utils.formataddr((self.name, self.address)) if self.name else self.address


class ImapMailbox:
    """INBOX over IMAP; sending over SMTP."""

    def __init__(self, account: MailAccount, *, mailbox: str = "INBOX") -> None:
        self.account = account
        self.mailbox = mailbox

    # ---------------------------------------------------------------- reading
    def fetch(self, count: int = 40) -> list[MailMessage]:
        """Newest ``count`` messages, newest first. Does not mark them read."""
        with self._imap() as imap:
            self._select(imap, readonly=True)
            typ, data = imap.uid("SEARCH", None, "ALL")
            _check(typ, data, "search")
            uids = data[0].split()[-max(1, count):] if data and data[0] else []
            if not uids:
                return []
            typ, data = imap.uid("FETCH", b",".join(uids).decode(), _FETCH_ITEMS)
            _check(typ, data, "fetch")
        messages = [m for m in (_parse_fetch_part(part) for part in data) if m is not None]
        messages.sort(key=lambda m: m.date, reverse=True)
        return messages

    # ---------------------------------------------------------------- writing
    def set_flag(self, uid: str, flag: str, on: bool) -> None:
        """Add or remove one IMAP flag (``\\Seen``, ``\\Flagged``, ...)."""
        with self._imap() as imap:
            self._select(imap, readonly=False)
            typ, data = imap.uid("STORE", uid, "+FLAGS" if on else "-FLAGS", f"({flag})")
            _check(typ, data, "store")

    def expunge(self, uids: list[str]) -> None:
        """Remove messages from the INBOX.

        On Gmail this archives them (the default "auto-expunge" setting
        moves an expunged INBOX message to All Mail), which is the safe,
        undoable choice for a tablet keyboard.
        """
        if not uids:
            return
        with self._imap() as imap:
            self._select(imap, readonly=False)
            typ, data = imap.uid("STORE", ",".join(uids), "+FLAGS", r"(\Deleted)")
            _check(typ, data, "store")
            imap.expunge()

    def send(self, to: str, subject: str, body: str, *, in_reply_to: str = "",
             references: str = "") -> None:
        msg = EmailMessage()
        msg["From"] = self.account.from_header
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=self.account.address.rpartition("@")[2] or None)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = f"{references} {in_reply_to}".strip()
        msg.set_content(body)
        acct = self.account
        try:
            context = _ssl_context()
            if acct.smtp_port == 465:
                smtp = smtplib.SMTP_SSL(acct.smtp_host, acct.smtp_port, timeout=acct.timeout, context=context)
            else:
                smtp = smtplib.SMTP(acct.smtp_host, acct.smtp_port, timeout=acct.timeout)
                smtp.starttls(context=context)
            with smtp:
                smtp.login(acct.address, acct.password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailError(_auth_hint("SMTP")) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP {acct.smtp_host}: {exc}") from exc

    # --------------------------------------------------------------- plumbing
    def _imap(self) -> imaplib.IMAP4_SSL:
        acct = self.account
        try:
            imap = imaplib.IMAP4_SSL(acct.imap_host, acct.imap_port,
                                     ssl_context=_ssl_context(), timeout=acct.timeout)
        except OSError as exc:
            raise MailError(f"IMAP {acct.imap_host}: {exc}") from exc
        try:
            imap.login(acct.address, acct.password)
        except imaplib.IMAP4.error as exc:
            imap.shutdown()
            raise MailError(_auth_hint("IMAP")) from exc
        return imap

    def _select(self, imap: imaplib.IMAP4, *, readonly: bool) -> None:
        typ, data = imap.select(self.mailbox, readonly=readonly)
        _check(typ, data, f"select {self.mailbox}")


def _ssl_context() -> ssl.SSLContext:
    # Android has no OpenSSL CA directory, so use certifi's bundle (already
    # shipped for httpx) when it is there.
    try:
        import certifi
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _auth_hint(proto: str) -> str:
    return (f"{proto} login refused. For Gmail, use an app password "
            "(myaccount.google.com/apppasswords, needs 2-Step Verification), "
            "not your Google password.")


def _check(typ: str, data: object, what: str) -> None:
    if typ != "OK":
        raise MailError(f"IMAP {what} failed: {data!r}")


# ------------------------------------------------------------------ parsing
_UID_RE = re.compile(rb"UID (\d+)")
_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")


def _parse_fetch_part(part: object) -> MailMessage | None:
    # imaplib returns (b'1 (UID 42 FLAGS (\\Seen) BODY[] {1234}', b'<raw>')
    # tuples, separated by b')' strings we skip.
    if not isinstance(part, tuple) or len(part) < 2:
        return None
    meta, raw = part[0], part[1]
    uid = _UID_RE.search(meta)
    flags_m = _FLAGS_RE.search(meta)
    flags = flags_m.group(1).decode(errors="replace").split() if flags_m else []
    return parse_message(raw, uid=uid.group(1).decode() if uid else "", flags=flags)


def parse_message(raw: bytes, *, uid: str = "", flags: list[str] | None = None) -> MailMessage:
    flags = flags or []
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    try:
        date = email.utils.parsedate_to_datetime(str(msg["Date"]))
        if date.tzinfo is not None:
            date = date.astimezone().replace(tzinfo=None)
    except (TypeError, ValueError, IndexError):
        date = dt.datetime.now().replace(second=0, microsecond=0)
    return MailMessage(
        sender=name or addr or "(unknown)",
        subject=str(msg.get("Subject", "")) or "(no subject)",
        date=date,
        body=_body_text(msg),
        to=str(msg.get("To", "")),
        new="\\Seen" not in flags,
        flagged="\\Flagged" in flags,
        replied="\\Answered" in flags,
        attachment=any(True for _ in msg.iter_attachments()) if msg.is_multipart() else False,
        uid=uid,
        sender_address=addr,
        message_id=str(msg.get("Message-ID", "")),
        references=str(msg.get("References", "")),
    )


def _body_text(msg: Message) -> str:
    part = msg.get_body(preferencelist=("plain", "html")) if isinstance(msg, EmailMessage) else msg
    if part is None:
        return "(no text body)"
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return "(no text body)"
    if part.get_content_subtype() == "html":
        text = html_to_text(text)
    text = text.replace("\r\n", "\n").strip()
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS] + "\n\n[... truncated ...]"
    return text


_BLOCK_TAGS = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/h\d|/li)\b[^>]*>")
_DROP = re.compile(r"(?is)<(script|style|head)\b.*?</\1\s*>")
_TAGS = re.compile(r"(?s)<[^>]+>")


def html_to_text(markup: str) -> str:
    """Crude HTML -> text for mail that has no text/plain part."""
    text = _DROP.sub("", markup)
    text = _BLOCK_TAGS.sub("\n", text)
    text = html.unescape(_TAGS.sub("", text))
    lines = [ln.strip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def quote_reply(msg: MailMessage) -> str:
    when = msg.date.strftime("%a, %d %b %Y at %H:%M")
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in msg.body.splitlines())
    return f"\n\nOn {when}, {msg.sender} wrote:\n{quoted}\n"
