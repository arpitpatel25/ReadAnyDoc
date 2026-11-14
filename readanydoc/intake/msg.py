"""Outlook .msg: headers and body, attachments listed by name (as Reducto does for email).

A .msg file is an OLE compound file whose streams are MAPI properties (``__substg1.0_<tag><type>``).
Read with ``olefile`` (BSD-2, pure Python); nothing else is needed for the fields below.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..model import Page, RegionType
from .common import decode_text, finish_pages, iso, make_region, new_page
from .errors import damaged, unsupported
from .text import html_to_text

_BLANK = re.compile(r"\n[ \t]*\n+")
# MAPI property tags
SUBJECT, BODY, HTML, SENDER_NAME, SENDER_EMAIL, SENDER_SMTP = "0037", "1000", "1013", "0C1A", "0C1F", "5D01"
TO, CC, BCC, HEADERS, ATT_LONG, ATT_SHORT = "0E04", "0E03", "0E02", "007D", "3707", "3704"
CLIENT_SUBMIT_TIME, DELIVERY_TIME = 0x00390040, 0x0E060040


def _text(ole, base: str, tag: str) -> Optional[str]:
    """A string property: unicode stream (type 001F) first, then the 8-bit one (001E)."""
    for kind, enc in (("001F", "utf-16-le"), ("001E", None)):
        name = f"{base}__substg1.0_{tag}{kind}"
        if ole.exists(name):
            raw = ole.openstream(name).read()
            return (raw.decode(enc, "replace") if enc else decode_text(raw)[0]).rstrip("\x00")
    return None


def _binary(ole, base: str, tag: str) -> Optional[bytes]:
    name = f"{base}__substg1.0_{tag}0102"
    return ole.openstream(name).read() if ole.exists(name) else None


def _filetime(ole, wanted: tuple[int, ...]) -> Optional[datetime]:
    """A time property from the fixed-width property stream (16-byte entries after a 32-byte header)."""
    name = "__properties_version1.0"
    if not ole.exists(name):
        return None
    raw = ole.openstream(name).read()
    found: dict[int, int] = {}
    for off in range(32, len(raw) - 15, 16):
        tag = int.from_bytes(raw[off:off + 4], "little")
        if tag in wanted:
            found[tag] = int.from_bytes(raw[off + 8:off + 16], "little")
    for tag in wanted:
        if found.get(tag):
            try:
                return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=found[tag] // 10)
            except OverflowError:
                return None
    return None


def read_msg(path: Path) -> dict[str, Any]:
    """{"subject", "from", "to", "cc", "bcc", "date", "body", "attachments"} of a .msg file."""
    try:
        import olefile
    except ImportError as e:  # pragma: no cover - olefile is a dependency
        raise unsupported("msg", "reading Outlook .msg files needs the 'olefile' package",
                          "Install olefile, or save the message as .eml and try again.") from e
    try:
        ole = olefile.OleFileIO(str(path))
    except Exception as e:
        raise damaged("msg", e) from e
    try:
        body = _text(ole, "", BODY)
        if not (body or "").strip():
            html = _binary(ole, "", HTML)
            markup = decode_text(html)[0] if html else _text(ole, "", HTML)
            body = html_to_text(markup) if markup else ""
        name, addr = _text(ole, "", SENDER_NAME), _text(ole, "", SENDER_SMTP) or _text(ole, "", SENDER_EMAIL)
        sender = name or ""
        if addr and "@" in addr and addr not in sender:
            sender = f"{sender} <{addr}>".strip()
        names = []
        for entry in ole.listdir(streams=False, storages=True):
            if len(entry) == 1 and entry[0].startswith("__attach_version1.0_"):
                base = entry[0] + "/"
                names.append(_text(ole, base, ATT_LONG) or _text(ole, base, ATT_SHORT) or "(unnamed attachment)")
        date = _filetime(ole, (CLIENT_SUBMIT_TIME, DELIVERY_TIME))
        if date is None:
            m = re.search(r"^Date:\s*(.+)$", _text(ole, "", HEADERS) or "", re.M)
            if m:
                from email.utils import parsedate_to_datetime
                try:
                    date = parsedate_to_datetime(m.group(1).strip())
                except Exception:
                    date = None
        clean = lambda s: (s or "").replace("\x00", "").strip().strip(";").strip()  # noqa: E731
        return {"subject": clean(_text(ole, "", SUBJECT)), "from": clean(sender), "to": clean(_text(ole, "", TO)),
                "cc": clean(_text(ole, "", CC)), "bcc": clean(_text(ole, "", BCC)), "date": date,
                "body": (body or "").replace("\r\n", "\n").replace("\r", "\n"), "attachments": names}
    finally:
        ole.close()


def load_msg(path: Path) -> tuple[list[Page], dict, dict]:
    """Same block layout as ``text.load_eml``: title, header lines, body paragraphs, attachment names."""
    m = read_msg(Path(path))
    page = new_page(0, 1)
    if m["subject"]:
        page.regions.append(make_region(RegionType.TITLE, f"# {m['subject']}", "msg", level=1))
    heads = [f"**{label}:** {m[key]}" for label, key in (("From", "from"), ("To", "to"), ("Cc", "cc"), ("Bcc", "bcc"))
             if m[key]]
    if m["date"] is not None:
        heads.append(f"**Date:** {m['date'].strftime('%Y-%m-%d %H:%M:%S%z')}")
    if heads:
        page.regions.append(make_region(RegionType.KEY_VALUE, "\n".join(heads), "msg"))
    for block in _BLANK.split(m["body"]):
        if block.strip():
            page.regions.append(make_region(RegionType.TEXT, block.strip("\n"), "msg", fmt="text"))
    if m["attachments"]:
        page.regions.append(make_region(RegionType.TEXT, "Attachments: " + ", ".join(m["attachments"]), "msg",
                                        attachments=m["attachments"]))
    props = {"title": m["subject"] or None, "author": m["from"] or None, "created_at": iso(m["date"])}
    return finish_pages([page]), props, {"attachments": m["attachments"]}
