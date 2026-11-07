"""Minimal text-mode web renderer in the spirit of w3m.

Converts HTML to Rich ``Text`` with numbered link references (``[3]``) so a
page can be navigated from the keyboard. Uses only the stdlib HTML parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin

import httpx
from rich.text import Text

from .. import mock_data

USER_AGENT = "TUI-OS/0.1 (text-mode; like w3m)"
MAX_BYTES = 2 * 1024 * 1024

# Styles are pure colours only, so they survive the E-ink colour filter.
STYLE_LINK = "bold #00FF00"
STYLE_HEADING = "bold #FFFFFF on #0000FF"
STYLE_PRE = "#00FF00"
STYLE_EMPH = "bold"


@dataclass
class Page:
    url: str
    title: str
    body: Text
    links: list[str] = field(default_factory=list)


_BLOCK = {
    "p", "div", "section", "article", "header", "footer", "main", "nav", "aside",
    "ul", "ol", "table", "tr", "form", "blockquote", "dl", "dt", "dd", "figure",
}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}


class _TextRenderer(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.out = Text()
        self.links: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._pre = 0
        self._styles: list[str] = []
        self._href_stack: list[str | None] = []
        self._list_stack: list[list[int]] = []  # [counter] or [-1] for <ul>

    # ----------------------------------------------------------------- helpers
    def _newline(self, count: int = 1) -> None:
        plain = self.out.plain
        existing = len(plain) - len(plain.rstrip("\n"))
        if not plain:
            return
        if existing < count:
            self.out.append("\n" * (count - existing))

    def _style(self) -> str:
        return " ".join(self._styles)

    # ---------------------------------------------------------------- handlers
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP and tag != "head":
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if self._skip_depth:
            return
        attr = dict(attrs)
        if tag in _HEADINGS:
            self._newline(2)
            self._styles.append(STYLE_HEADING)
            self.out.append(" ", self._style())
        elif tag in _BLOCK:
            self._newline(2 if tag in {"p", "blockquote", "table"} else 1)
            if tag == "ul":
                self._list_stack.append([-1])
            elif tag == "ol":
                self._list_stack.append([0])
        elif tag == "li":
            self._newline()
            depth = max(0, len(self._list_stack) - 1)
            marker = "*"
            if self._list_stack and self._list_stack[-1][0] >= 0:
                self._list_stack[-1][0] += 1
                marker = f"{self._list_stack[-1][0]}."
            self.out.append("  " * depth + f" {marker} ")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self._newline()
            self.out.append("─" * 60 + "\n")
        elif tag == "pre":
            self._newline(2)
            self._pre += 1
            self._styles.append(STYLE_PRE)
        elif tag in {"strong", "b", "em", "i", "code"}:
            self._styles.append(STYLE_PRE if tag == "code" else STYLE_EMPH)
        elif tag in {"td", "th"}:
            self.out.append("  ")
        elif tag == "img":
            alt = (attr.get("alt") or "").strip()
            if alt:
                self.out.append(f"[{alt}]", "italic")
        elif tag == "a":
            href = attr.get("href")
            if href and not href.startswith(("javascript:", "#")):
                self._href_stack.append(urljoin(self.base_url, href))
                self._styles.append("underline")
            else:
                self._href_stack.append(None)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and tag != "head":
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if self._skip_depth:
            return
        if tag in _HEADINGS:
            self.out.append(" ", self._style())
            self._pop(STYLE_HEADING)
            self._newline(2)
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._newline()
        elif tag in _BLOCK:
            self._newline(2 if tag in {"p", "blockquote", "table"} else 1)
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
            self._pop(STYLE_PRE)
            self._newline(2)
        elif tag in {"strong", "b", "em", "i"}:
            self._pop(STYLE_EMPH)
        elif tag == "code":
            self._pop(STYLE_PRE)
        elif tag == "a" and self._href_stack:
            href = self._href_stack.pop()
            if href:
                self._pop("underline")
                self.links.append(href)
                self.out.append(f"[{len(self.links)}]", STYLE_LINK)

    def _pop(self, style: str) -> None:
        for i in range(len(self._styles) - 1, -1, -1):
            if self._styles[i] == style:
                del self._styles[i]
                return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if not self._pre:
            data = re.sub(r"\s+", " ", data)
            if self.out.plain.endswith(("\n", " ")) or not self.out.plain:
                data = data.lstrip()
            if not data:
                return
        self.out.append(data, self._style() or None)


def render_html(html: str, base_url: str) -> Page:
    parser = _TextRenderer(base_url)
    parser.feed(html)
    parser.close()
    body = parser.out
    body.rstrip()
    title = re.sub(r"\s+", " ", parser.title).strip() or base_url
    return Page(url=base_url, title=title, body=body, links=parser.links)


def normalise_url(raw: str) -> str:
    """Turn what the user typed into a URL: about: page, URL or search."""
    raw = raw.strip()
    if not raw:
        return "about:home"
    if raw.startswith(("about:", "http://", "https://")):
        return raw
    if " " not in raw and ("." in raw or raw.startswith("localhost")):
        return f"https://{raw}"
    return f"https://lite.duckduckgo.com/lite/?q={quote_plus(raw)}"


async def fetch_page(url: str, client: httpx.AsyncClient) -> Page:
    if url.startswith("about:"):
        html = mock_data.ABOUT_PAGES.get(url)
        if html is None:
            html = f"<title>Not found</title><h1>{url}</h1><p>No such page.</p>"
        return render_html(html, url)

    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        raw = b"".join(chunks)
        final_url = str(resp.url)
        encoding = resp.encoding or "utf-8"

    text = raw.decode(encoding, errors="replace")
    if "html" not in ctype and not text.lstrip().startswith("<"):
        return Page(url=final_url, title=final_url, body=Text(text, style=STYLE_PRE))
    return render_html(text, final_url)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(15.0),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
    )
