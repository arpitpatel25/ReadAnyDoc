"""Table cells: grid positions and char spans from the block's HTML (or a markdown pipe table)."""

from __future__ import annotations

import re

from ...model import TableCell

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.I | re.S)
_CELL = re.compile(r"<(t[dh])\b([^>]*)>(.*?)</t[dh]\s*>", re.I | re.S)
_SPAN = re.compile(r"(rowspan|colspan)\s*=\s*[\"']?(\d+)", re.I)
_TAG = re.compile(r"<[^>]+>")
_MD_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def parse_table_cells(content: str) -> list[TableCell]:
    """HTML table -> cells with grid positions and the char span of each cell's inner HTML."""
    cells: list[TableCell] = []
    occupied: set[tuple[int, int]] = set()
    for r, row in enumerate(_ROW.finditer(content)):
        c = 0
        for m in _CELL.finditer(row.group(1)):
            while (r, c) in occupied:
                c += 1
            spans = {k.lower(): min(max(int(v), 1), 500) for k, v in _SPAN.findall(m.group(2))}
            rs, cs = spans.get("rowspan", 1), spans.get("colspan", 1)
            for dr in range(rs):
                for dc in range(cs):
                    occupied.add((r + dr, c + dc))
            start = row.start(1) + m.start(3)
            cells.append(TableCell(row=r, col=c, rowspan=rs, colspan=cs, is_header=m.group(1).lower() == "th",
                                   text=re.sub(r"\s+", " ", _TAG.sub(" ", m.group(3))).strip(),
                                   char_start=start, char_end=start + len(m.group(3))))
            c += cs
    return cells


def parse_pipe_table_cells(content: str) -> list[TableCell]:
    """Markdown pipe table -> cells (no spans). Rows are lines that start with ``|``."""
    cells: list[TableCell] = []
    pos = 0
    r = 0
    saw_sep = False
    for line in content.splitlines(keepends=True):
        body = line.rstrip("\n")
        if body.lstrip().startswith("|"):
            if _MD_SEP.match(body):
                saw_sep = True
            else:
                # cell boundaries are unescaped pipes
                cuts = [i for i, ch in enumerate(body) if ch == "|" and (i == 0 or body[i - 1] != "\\")]
                for c, (a, b) in enumerate(zip(cuts, cuts[1:] + ([len(body)] if body.rstrip()[-1:] != "|" else []))):
                    raw = body[a + 1:b]
                    lead = len(raw) - len(raw.lstrip())
                    text = raw.strip()
                    start = pos + a + 1 + lead
                    cells.append(TableCell(row=r, col=c, text=text, is_header=(r == 0 and not saw_sep),
                                           char_start=start, char_end=start + len(text)))
                r += 1
        pos += len(line)
    return cells if saw_sep else []


def table_cells(content: str) -> list[TableCell]:
    low = content.lower()
    if "<tr" in low:
        return parse_table_cells(content)
    if "|" in content and "--" in content:
        return parse_pipe_table_cells(content)
    return []
