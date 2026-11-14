"""File type from content first, extension second."""

from __future__ import annotations

import zipfile
from pathlib import Path

IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"II*\x00": "tiff",
               b"MM\x00*": "tiff", b"BM": "bmp"}
IMAGE_KINDS = ("png", "jpg", "gif", "tiff", "bmp", "webp")
NATIVE_KINDS = ("docx", "xlsx", "csv", "txt", "pptx", "eml", "msg")
# formats that need LibreOffice to convert, and what they convert to
LEGACY_KINDS = {"doc": "docx", "dot": "docx", "rtf": "docx", "odt": "docx", "wpd": "docx",
                "xls": "xlsx", "xlt": "xlsx", "ods": "xlsx", "qpw": "xlsx",
                "ppt": "pptx", "pps": "pptx", "odp": "pptx"}
_EXT = {"jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "xlsm": "xlsx", "xltx": "xlsx", "xltm": "xlsx",
        "dotx": "docx", "docm": "docx", "dotm": "docx", "pptm": "pptx", "ppsx": "pptx", "potx": "pptx",
        "tsv": "csv", "text": "txt", "md": "txt", "log": "txt", "heif": "heic"}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except Exception:
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(path) as z:
                mt = z.read("mimetype").decode("ascii", "ignore")
        except Exception:
            mt = ""
        for key, kind in (("opendocument.text", "odt"), ("opendocument.spreadsheet", "ods"),
                          ("opendocument.presentation", "odp")):
            if key in mt:
                return kind
    return "zip"


def sniff(path: Path) -> str:
    """Canonical kind: pdf, png, jpg, gif, tiff, bmp, webp, heic, docx, xlsx, pptx, csv, txt, eml,
    a legacy kind (doc, xls, ppt, rtf, ...), msg, or the bare extension / "unknown"."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        head = b""
    ext = path.suffix.lower().lstrip(".")
    ext = _EXT.get(ext, ext)
    if head.startswith(b"%PDF") or b"%PDF" in head[:1024]:
        return "pdf"
    for magic, kind in IMAGE_MAGIC.items():
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis"):
        return "heic"
    if head.startswith(b"PK"):
        kind = _zip_kind(path)
        return kind if kind != "zip" else (ext if ext in ("docx", "xlsx", "pptx") else "zip")
    if head.startswith(_OLE):
        return ext if ext in LEGACY_KINDS or ext == "msg" else "ole"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if ext in ("csv", "txt", "eml", "heic", "msg") or ext in LEGACY_KINDS:
        return ext
    if ext in ("docx", "xlsx", "pptx", "pdf") or ext in IMAGE_KINDS:
        return ext  # extension says so but the content does not: let the reader report the damage
    if not ext and head and _looks_like_text(head):
        return "eml" if _looks_like_email(head) else "txt"
    return ext or "unknown"


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return sum(b < 9 or 13 < b < 32 for b in head) < len(head) * 0.02


def _looks_like_email(head: bytes) -> bool:
    text = head.decode("latin-1").lower()
    return sum(h in text for h in ("\nfrom:", "\nto:", "\nsubject:", "\ndate:", "mime-version:", "received:")) >= 3 \
        or text.startswith(("from:", "received:", "return-path:", "mime-version:"))
