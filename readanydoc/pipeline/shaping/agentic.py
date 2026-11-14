"""Custom prompts for tables and text: ``enhance.agentic[{scope: "table" | "text", prompt}]``.

Only runs when the request carries such a prompt. One text-only model call per table (scope ``table``) and one
per page for its text and form blocks (scope ``text``; Reducto applies it to form regions). The model does not
re-read the page: it gets the block text the reader produced and the user's instruction, and answers with the
cells / blocks it changes. Guards keep it an edit, not a rewrite:

- table cells are changed in place in the block's HTML, so the grid (rows, columns, spans) cannot change; a
  full replacement table is accepted only when the instruction needs a different structure and the answer
  parses as a table;
- a text block is changed only if the new text stays close to the old (the instruction reformats, it does not
  rewrite); anything else is dropped and noted.

Without a live model (cached profiles) nothing is called and nothing changes.
"""

from __future__ import annotations

import difflib
import html
import re
from typing import Any, Optional

from ...model import Document, Region, RegionType
from . import tables as tables_mod

TABLE_SYSTEM = (
    "You edit one table from a parsed document according to the user's instruction. You get the cells as "
    "numbered lines 'c<N> | r<row> c<col> | <text>'. Change only what the instruction asks for and copy "
    "everything else exactly; never invent values. Reply with one JSON object and nothing else: "
    '{"changes": [{"cell": N, "text": "<new cell text>"}]} listing only the cells whose text changes '
    '(an empty list when nothing needs to change). Only if the instruction cannot be carried out without '
    'changing the rows or columns themselves, reply {"table": "<table>...</table>"} with the complete new '
    "HTML table instead.")
TEXT_SYSTEM = (
    "You edit blocks of text from one page of a parsed document according to the user's instruction. You get "
    "the blocks as '[b<N>] <type>' followed by their text. Change only what the instruction asks for and copy "
    "everything else exactly, including Markdown marks and line breaks; never invent or drop content. Reply "
    'with one JSON object and nothing else: {"changes": [{"block": N, "text": "<complete new text of the '
    'block>"}]} listing only the blocks whose text changes (an empty list when nothing needs to change).')
# Reducto documents the text prompt for form regions; its real responses also changed a footer line, so every
# block that is plain text is offered (tables have their own scope; figures, signatures and page numbers are not text)
TEXT_TYPES = (RegionType.KEY_VALUE, RegionType.TEXT, RegionType.LIST_ITEM, RegionType.HEADER, RegionType.FOOTER,
              RegionType.TITLE, RegionType.SECTION_HEADER, RegionType.COMMENT)
MAX_TABLE_CHARS = 60_000
MAX_PAGE_CHARS = 40_000
MIN_SIMILARITY = 0.5
_CELL = re.compile(r"(<t[dh]\b[^>]*>)(.*?)(</t[dh]\s*>)", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def wants(request: Optional[dict[str, Any]]) -> dict[str, Optional[str]]:
    """The table and text prompts of a parse request (None when not asked)."""
    out: dict[str, Optional[str]] = {"table": None, "text": None}
    for entry in ((request or {}).get("enhance") or {}).get("agentic") or []:
        if isinstance(entry, dict) and entry.get("scope") in out and str(entry.get("prompt") or "").strip():
            out[entry["scope"]] = str(entry["prompt"]).strip()
    return out


def _cell_text(inner: str) -> str:
    return html.unescape(_TAGS.sub(" ", inner.replace("<br>", "\n").replace("<br/>", "\n"))).strip()


def _usage(res, task: str) -> dict[str, Any]:
    return {"model": res.model, "task": task,
            **{k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cost_usd")}}


def _ask(llm, system: str, user: str, sink: list, task: str, max_tokens: int) -> Any:
    from ...tasks.llm import parse_json
    res = llm.complete(system, user, max_tokens=max_tokens)
    sink.append(_usage(res, task))
    try:
        return parse_json(res.text)
    except Exception:
        return None


def edit_table(region: Region, prompt: str, llm, sink: list) -> tuple[int, Optional[str]]:
    """Apply ``prompt`` to one table. Returns (cells changed, note)."""
    content = region.content
    if not tables_mod.is_html_table(content) or len(content) > MAX_TABLE_CHARS:
        return 0, "table skipped (not an HTML table, or too large)"
    cells = list(_CELL.finditer(content))
    if not cells:
        return 0, None
    grid = tables_mod.parse_html_table(content)
    where = [(c.row, c.col) for c in grid.cells] if grid is not None and len(grid.cells) == len(cells) else None
    lines = [f"c{i} | " + (f"r{where[i][0] + 1} c{where[i][1] + 1}" if where else "-") + " | "
             + _cell_text(m.group(2)).replace("\n", " / ") for i, m in enumerate(cells)]
    data = _ask(llm, TABLE_SYSTEM, f"Instruction: {prompt}\n\nTable cells:\n" + "\n".join(lines), sink,
                "agentic_table", 16000)
    if not isinstance(data, dict):
        return 0, "table prompt: the model's answer was not usable; table left as read"
    if isinstance(data.get("table"), str) and data["table"].strip():
        new = data["table"].strip()
        g = tables_mod.parse_html_table(new) if tables_mod.is_html_table(new) else None
        if g is None or not g.cells:
            return 0, "table prompt: the replacement table did not parse; table left as read"
        region.content = new
        region.cells = []  # cell boxes belonged to the old grid
        region.meta["agentic_prompt"] = "table"
        return len(g.cells), None
    changes: dict[int, str] = {}
    for ch in data.get("changes") or []:
        if isinstance(ch, dict) and isinstance(ch.get("cell"), int) and 0 <= ch["cell"] < len(cells) \
                and isinstance(ch.get("text"), str):
            changes[ch["cell"]] = ch["text"]
    changes = {i: t for i, t in changes.items() if t.strip() != _cell_text(cells[i].group(2))}
    if not changes:
        return 0, None
    out, pos = [], 0
    for i, m in enumerate(cells):
        out.append(content[pos:m.start()])
        inner = html.escape(changes[i], quote=False).replace("\n", "<br>") if i in changes else m.group(2)
        out.append(m.group(1) + inner + m.group(3))
        pos = m.end()
    out.append(content[pos:])
    region.content = "".join(out)
    if len(region.cells) == len(cells):  # keep the cell list (boxes, citations) in step with the content
        spans = list(_CELL.finditer(region.content))
        for i, cell in enumerate(region.cells):
            if i in changes:
                cell.text = changes[i]
            if cell.char_start is not None:
                cell.char_start, cell.char_end = spans[i].start(2), spans[i].end(2)
    region.meta["agentic_prompt"] = "table"
    return len(changes), None


def _still_the_same_block(old: str, new: str) -> bool:
    """A reformatted block, not a rewritten one. Short blocks (a line of dates) may change in most characters,
    so they are held to length only; longer ones must keep at least half of their text."""
    if not (0.4 <= len(new) / max(len(old), 1) <= 2.5):
        return False
    need = MIN_SIMILARITY if len(old) > 200 else 0.25
    return difflib.SequenceMatcher(None, old, new, autojunk=False).ratio() >= need


def edit_page_text(regions: list[Region], prompt: str, llm, sink: list) -> tuple[int, list[str]]:
    """Apply ``prompt`` to the text and form blocks of one page. Returns (blocks changed, notes)."""
    blocks = [r for r in regions if r.type in TEXT_TYPES and r.content.strip() and not r.meta.get("merged_into")
              and not tables_mod.is_html_table(r.content)]
    if not blocks:
        return 0, []
    parts, size = [], 0
    for i, r in enumerate(blocks):
        size += len(r.content)
        if size > MAX_PAGE_CHARS:
            blocks = blocks[:i]
            break
        parts.append(f"[b{i}] {r.type.value}\n{r.content}")
    data = _ask(llm, TEXT_SYSTEM, f"Instruction: {prompt}\n\nBlocks:\n\n" + "\n\n".join(parts), sink,
                "agentic_text", 16000)
    if not isinstance(data, dict):
        return 0, ["text prompt: the model's answer was not usable; blocks left as read"]
    n, notes = 0, []
    for ch in data.get("changes") or []:
        if not (isinstance(ch, dict) and isinstance(ch.get("block"), int) and 0 <= ch["block"] < len(blocks)
                and isinstance(ch.get("text"), str) and ch["text"].strip()):
            continue
        r = blocks[ch["block"]]
        new = ch["text"].strip()
        if new == r.content.strip():
            continue
        if not _still_the_same_block(r.content, new):
            notes.append(f"text prompt: a change that rewrote most of a block was dropped ({r.content[:40]!r})")
            continue
        r.meta["agentic_original"] = r.content
        r.meta["agentic_prompt"] = "text"
        r.content = new
        n += 1
    return n, notes


def process(doc: Document, request: Optional[dict[str, Any]], reader_spec: Optional[str] = None,
            llm=None) -> dict[str, Any]:
    """Run the table and text prompts of ``request`` over ``doc``. Returns counts for the stage note."""
    want = wants(request)
    stats = {"tables": 0, "cells_changed": 0, "text_blocks_changed": 0, "calls": 0, "notes": []}
    if not (want["table"] or want["text"]):
        return stats
    if llm is None:
        from .summarize import default_spec
        spec = default_spec(reader_spec)
        if not spec:
            stats["notes"].append("enhance.agentic table / text prompts need a live model; none is configured")
            return stats
        from ...tasks.llm import get_llm
        llm = get_llm(spec)
    sink: list[dict[str, Any]] = []
    for page in doc.pages:
        if want["table"]:
            for r in page.regions:
                if r.type == RegionType.TABLE and not r.meta.get("merged_into"):
                    try:
                        n, note = edit_table(r, want["table"], llm, sink)
                    except Exception as e:  # an extra: never fail the parse for it
                        n, note = 0, f"table prompt failed ({type(e).__name__}: {e})"
                    stats["tables"] += 1
                    stats["cells_changed"] += n
                    if note:
                        stats["notes"].append(note)
        if want["text"]:
            try:
                n, notes = edit_page_text(page.regions, want["text"], llm, sink)
            except Exception as e:
                n, notes = 0, [f"text prompt failed ({type(e).__name__}: {e})"]
            stats["text_blocks_changed"] += n
            stats["notes"] += notes
    stats["calls"] = len(sink)
    doc.usage.extend(sink)
    doc.meta["prompted_blocks"] = stats["text_blocks_changed"]
    return stats
