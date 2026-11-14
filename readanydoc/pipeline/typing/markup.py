"""Block type from the block's own text. Every rule reads the text; none changes it."""

from __future__ import annotations

import re
from typing import Optional

from ...model import RegionType

_TAGS = re.compile(r"</?[A-Za-z][^<>]*>")
_HTML_HEADING = re.compile(r"^\s*<h([1-6])\b[^>]*>(.*?)</h\1\s*>\s*$", re.I | re.S)
_BOLD_LINE = re.compile(r"^\s*(?:<p>\s*)?(?:\*\*|__|<strong>|<b>)(.+?)(?:\*\*|__|</strong>|</b>)(?:\s*</p>)?\s*$", re.I)
_BOLD_LEAD = re.compile(r"^\s*(?:\*\*|<strong>|<b>)(.+?)(?:\*\*|</strong>|</b>)\s*(.*)$", re.I)
_NOTE = re.compile(r"^\s*(?:<p>\s*)?([*_]{0,2})\[([^\[\]]{3,600})\]([*_]{0,2})(?:\s*</p>)?\s*$", re.S)
_ITALIC_FIGURE = re.compile(r"^\s*[*_](?:figure|image|photo|logo|illustration|chart|diagram)\s*:\s*[^*\n]{2,400}[*_]\s*$", re.I)
_FIGURE_WORDS = re.compile(
    r"\b(?:logo|seal|emblem|crest|figure|image|photo|photograph|picture|illustration|drawing|chart|graph|diagram|"
    r"map|icon|qr code|barcode|bar code|wordmark|coat of arms|portrait|sketch)\b", re.I)
_SIGNATURE_WORDS = re.compile(r"^\s*(?:signature|signed|handwritten signature|initials)\b", re.I)
_NOT_FIGURE = re.compile(r"^\s*(?:illegible|unclear|unreadable|blank|redacted|sic|handwritten|handwriting|"
                         r"watermark|stamp|note)\b", re.I)
_CHECKBOX = re.compile(r"\[[ xX✓✔]?\]|[☐☑☒□■▢]")
_BLANK = re.compile(r"_{3,}|\.{6,}|<empty\s*/?>")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•▶►■●○◦·]|\(?\d{1,3}[.)])\s+\S")
_HEADING_KEYWORD = re.compile(r"^(?:section|part|schedule|article|chapter|table|appendix|exhibit|§)\s*[\dIVXLC§A-Z]",
                              re.I)
_SENTENCE_END = re.compile(r"[.!?][\"”’)]?$")


def _plain(s: str) -> str:
    s = _TAGS.sub(" ", s)
    s = re.sub(r"\*\*|__|(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _words(s: str) -> list[str]:
    return [w for w in _plain(s).split() if any(c.isalnum() for c in w)]


def note_type(text: str) -> Optional[RegionType]:
    """A block that is only the reader's bracketed note: Figure for a described picture, Signature for a
    described signature. Printed bracketed text ("[In millions of dollars]") is neither."""
    if "<" in text:  # <p align="center"><em>[Seal of ...]</em></p>
        bare = _TAGS.sub("", text)
        if bare != text and bare.strip():
            was_italic = bool(re.search(r"<(?:em|i)\b", text, re.I))
            text = f"*{bare.strip()}*" if was_italic and not bare.strip().startswith(("*", "_")) else bare
    if _ITALIC_FIGURE.match(text):
        return RegionType.FIGURE
    m = _NOTE.match(text)
    if not m:
        return None
    body = m.group(2)
    if _CHECKBOX.fullmatch(text.strip()):
        return None
    if _SIGNATURE_WORDS.match(body):
        return RegionType.SIGNATURE
    if _NOT_FIGURE.match(body):
        return None
    italic = bool(m.group(1)) and bool(m.group(3))
    if _FIGURE_WORDS.search(body) and (italic or len(body.split()) <= 25):
        return RegionType.FIGURE
    return None


def _field_marks(line: str) -> bool:
    return bool(_CHECKBOX.search(line) or _BLANK.search(line))


def _kv_line(line: str) -> bool:
    """``Label: value``, ``Label:`` or a line with a blank to fill in or a checkbox."""
    p = _plain(line)
    if not p:
        return False
    if _field_marks(line):
        return True
    m = re.match(r"^(.{1,70}?):(?:\s+(.*))?$", p)
    if not m:
        return False
    label, value = m.group(1), (m.group(2) or "")
    lw = label.split()
    if not lw or len(lw) > 9 or not any(c.isalpha() for c in label):
        return False
    if re.search(r"https?$|\d$", label) and value[:1].isdigit():  # a time or a ratio, not a label
        return False
    if label.lower() in ("note", "notes", "caution", "warning", "example", "tip", "source", "sources", "re",
                         "important", "attention", "notice"):
        return False
    if len(value.split()) > 8 and re.search(r"[.!?]\s+[A-Z]", value):
        return False  # the "value" is running text
    return len(value.split()) <= 14


def is_key_value(text: str) -> bool:
    """A fill-in field or a run of label/value lines. Prose that merely contains a colon is not."""
    if _NOTE.match(text):  # a printed or editorial bracketed remark, whatever is inside
        return False
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines or len(lines) > 60:
        return False
    hits = [_kv_line(l) for l in lines]
    n = sum(hits)
    if not n:
        return False
    words = len(_words(text))
    if len(lines) == 1:
        marks = _field_marks(lines[0])
        # a long sentence with one blank or box in it is still a field; a long sentence with a colon is prose
        return words <= (60 if marks else 24)
    if hits[0] and n == 1 and not _field_marks(lines[0]):
        # "Provider: NAME" followed by its address lines
        return len(lines) <= 5 and words <= 30
    return n * 2 >= len(lines) and words <= 40 * n


def heading_markup(text: str) -> Optional[tuple[RegionType, int, str]]:
    """Headings written without ``#``: HTML heading tags, a line that is bold from end to end, or a bold
    lead-in with a short remainder ("**Part I** General Information", "**DECLARATION.** (See Instruction E)")."""
    m = _HTML_HEADING.match(text)
    if m and len(_words(m.group(2))) <= 40:
        level = int(m.group(1))
        return (RegionType.TITLE if level == 1 else RegionType.SECTION_HEADER), level, "html_heading"
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines or len(lines) > 4:
        return None
    if _field_marks(text):
        return None
    bold = [_BOLD_LINE.match(l) for l in lines]
    if all(bold):
        inner = " ".join(b.group(1) for b in bold)
        if "**" in inner or "<b>" in inner.lower():  # several bold spans on one line: a row of labels
            return None
        w = _words(inner)
        if 1 <= len(w) <= 16 and not inner.rstrip().endswith((":", ",", ";")) and any(c.isalpha() for c in inner):
            numbered = bool(_HEADING_KEYWORD.match(_plain(inner)))
            if len(w) > 9 and not numbered and (_SENTENCE_END.search(inner.strip()) or re.search(r"[.:;]\s", inner)):
                return None  # a bold sentence
            if len(lines) >= 3 and not _plain(inner).isupper():
                return None  # a bold address or notice, not a heading
            return RegionType.SECTION_HEADER, 2, "bold_line"
        return None
    if len(lines) == 1:
        m = _BOLD_LEAD.match(lines[0])
        if m and m.group(2):
            lead, rest = m.group(1).strip(), m.group(2).strip()
            rw, lw = _words(rest), _words(lead)
            if _HEADING_KEYWORD.match(_plain(lead)) and len(lw) >= 2 and len(lw) + len(rw) <= 60 and "**" not in rest:
                return RegionType.SECTION_HEADER, 2, "bold_lead_numbered"  # "**Part III Information.** Complete ..."
            if lead.endswith((":", ",")) or not any(c.isalpha() for c in lead) or "**" in rest:
                return None
            parenthetical = rest.startswith("(") and rest.endswith(")")
            if (len(lw) >= 1 and len(lw) + len(rw) <= 22 and (parenthetical or (len(rw) <= 6 and not
                    _SENTENCE_END.search(rest)))) and len(lead) >= 4:
                return RegionType.SECTION_HEADER, 2, "bold_lead"
    return None


_LETTERED = re.compile(r"^\s*[\"“'‘]{0,2}(?:\((?:[A-Za-z]{1,2}|\d{1,3}|[ivxlc]{1,5})\)\.?|[A-Za-z][.)])\s+(?![A-Z]\.)\S")


def classify_text(text: str) -> tuple[RegionType, Optional[int], Optional[str]]:
    """(type, heading level, rule) for a block that is not a markdown heading, table or list."""
    nt = note_type(text)
    if nt is not None:
        return nt, None, "reader_note"
    h = heading_markup(text)
    if h is not None:
        return h
    if _LETTERED.match(text) and "\n\n" not in text.strip() and not _field_marks(text) \
            and not text.rstrip().endswith(":"):
        return RegionType.LIST_ITEM, None, "lettered_item"  # "(b) ...", "a. ..."
    if is_key_value(text):
        return RegionType.KEY_VALUE, None, "label_value"
    return RegionType.TEXT, None, None


def split_list_items(text: str) -> list[str]:
    """One string per top-level list item; continuation and nested lines stay with their item.
    The characters of each item are unchanged."""
    lines = text.split("\n")
    first = next((l for l in lines if l.strip()), "")
    indent = len(first) - len(first.lstrip())
    items: list[list[str]] = []
    for l in lines:
        own_indent = len(l) - len(l.lstrip())
        if l.strip() and _LIST_MARK.match(l) and own_indent <= indent or not items:
            items.append([l])
        else:
            items[-1].append(l)
    return ["\n".join(i).strip("\n") for i in items if any(x.strip() for x in i)]
