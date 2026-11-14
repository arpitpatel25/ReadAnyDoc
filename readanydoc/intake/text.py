"""TXT and EML: plain text straight into blocks."""

from __future__ import annotations

import email
import email.policy
import re
from html.parser import HTMLParser
from pathlib import Path

from ..model import Page, RegionType
from .common import decode_text, finish_pages, iso, make_region, new_page

_BLANK = re.compile(r"\n[ \t]*\n+")


def load_txt(path: Path) -> tuple[list[Page], dict, dict]:
    """Pages split on form feeds; blocks split on blank lines. Returns (pages, properties, meta)."""
    text, enc = decode_text(Path(path).read_bytes())
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = []
    for i, chunk in enumerate(text.split("\f")):
        page = new_page(i, i + 1)
        for block in _BLANK.split(chunk):
            if block.strip():
                page.regions.append(make_region(RegionType.TEXT, block.strip("\n"), "txt", fmt="text"))
        pages.append(page)
    if len(pages) > 1 and not pages[-1].regions:
        pages.pop()
    return finish_pages(pages), {}, {"encoding": enc}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(self.skip - 1, 0)
        elif tag in ("p", "div", "tr", "table", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    p = _Text()
    p.feed(markup)
    text = "".join(p.out).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_eml(path: Path) -> tuple[list[Page], dict, dict]:
    """Headers + body; attachments are listed by name, not parsed (as Reducto does)."""
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=email.policy.default)
    page = new_page(0, 1)
    subject = str(msg.get("Subject") or "").strip()
    if subject:
        page.regions.append(make_region(RegionType.TITLE, f"# {subject}", "eml", level=1))
    heads = [f"**{h}:** {str(msg.get(h)).strip()}" for h in ("From", "To", "Cc", "Bcc", "Reply-To", "Date")
             if msg.get(h)]
    if heads:
        page.regions.append(make_region(RegionType.KEY_VALUE, "\n".join(heads), "eml"))
    body = None
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is not None:
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = decode_text(payload)[0]
        if part.get_content_type() == "text/html":
            body = html_to_text(body)
    for block in _BLANK.split((body or "").replace("\r\n", "\n")):
        if block.strip():
            page.regions.append(make_region(RegionType.TEXT, block.strip("\n"), "eml", fmt="text"))
    names = []
    for att in msg.iter_attachments():
        names.append(att.get_filename() or f"(unnamed {att.get_content_type()})")
    if names:
        page.regions.append(make_region(RegionType.TEXT, "Attachments: " + ", ".join(names), "eml",
                                        attachments=names))
    date = None
    try:
        date = msg["Date"].datetime if msg.get("Date") else None
    except Exception:
        date = None
    props = {"title": subject or None, "author": str(msg.get("From") or "") or None, "created_at": iso(date)}
    return finish_pages([page]), props, {"attachments": names}
