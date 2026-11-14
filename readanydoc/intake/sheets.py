"""Spreadsheets (XLSX / XLSM via openpyxl) and CSV: one sheet = one page, tables as HTML.

``spreadsheet.clustering``:
- ``fast``: rule-based. The used cells of a sheet are cut into blocks wherever a fully empty row or
  column separates them (recursive XY cut). A block with two or more columns is a Table, otherwise Text.
- ``disabled``: the whole used range is one table.
- ``accurate`` (Reducto's default, model-based): not available offline; runs ``fast`` and says so.
``split_large_tables`` cuts a table every N body rows (default 50) and repeats its header row.
Cells keep their sheet address: ``region.meta["cell_refs"]`` maps "row,col" of the table to "B7",
and every ``TableCell.bbox`` is the cell's place in the sheet grid, normalised to the used range.
Markup for ``include``: ``data-formula="=SUM(A1:A3)"``, ``style="background-color:#RRGGBB"``,
``data-dropdown="a,b,c"`` on the ``<td>`` (our choice; Reducto's exact markup is not documented).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..model import BBox, Page, RegionType, TableCell
from ..pipeline.shaping.options import SpreadsheetOptions
from ..pipeline.shaping.page_range import PageRangeError, resolve_page_range
from .common import EXACT, decode_text, esc, iso, make_region, new_page
from .errors import IntakeError, cell_count_exceeded, damaged


@dataclass
class SCell:
    text: str
    formula: Optional[str] = None
    fill: Optional[str] = None
    dropdown: Optional[str] = None


@dataclass
class Sheet:
    name: str
    number: int  # 1-based position in the workbook
    hidden: bool = False
    cells: dict[tuple[int, int], SCell] = field(default_factory=dict)  # 1-based (row, col)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)  # r0, c0, r1, c1 inclusive
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)


def col_letter(c: int) -> str:
    s = ""
    while c > 0:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return s


def ref(r: int, c: int) -> str:
    return f"{col_letter(c)}{r}"


def format_value(v: Any, number_format: str = "General") -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) \
            else v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (int, float)):
        fmt = number_format or "General"
        if "%" in fmt:
            decimals = len(fmt.split(".")[1].split("%")[0]) if "." in fmt else 0
            return f"{v * 100:.{decimals}f}%"
        if isinstance(v, float):
            if v == int(v) and abs(v) < 1e15:
                return str(int(v))
            return f"{v:.10g}"
        return str(v)
    return str(v).replace("\r\n", "\n").replace("\r", "\n").strip()


# ---------------------------------------------------------------- readers

def read_xlsx(path: Path, so: SpreadsheetOptions) -> tuple[list[Sheet], dict]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(str(path), data_only=False)
    except Exception as e:
        raise damaged("XLSX", e) from e
    has_formula = False
    sheets: list[Sheet] = []
    want_color = "cell_colors" in so.include and "styling" not in so.exclude
    for i, ws in enumerate(wb.worksheets):
        sh = Sheet(name=ws.title, number=i + 1, hidden=ws.sheet_state != "visible")
        sheets.append(sh)
        for r, dim in ws.row_dimensions.items():
            if dim.hidden:
                sh.hidden_rows.add(int(r))
        for dim in ws.column_dimensions.values():
            if dim.hidden and dim.min and dim.max:
                sh.hidden_cols.update(range(int(dim.min), int(dim.max) + 1))
        for rng in ws.merged_cells.ranges:
            sh.merges.append((rng.min_row, rng.min_col, rng.max_row, rng.max_col))
        dropdowns: dict[tuple[int, int], str] = {}
        if "dropdowns" in so.include and ws.data_validations is not None:
            for dv in ws.data_validations.dataValidation:
                if dv.type != "list" or not dv.formula1:
                    continue
                for rng in dv.sqref.ranges:
                    if (rng.max_row - rng.min_row + 1) * (rng.max_col - rng.min_col + 1) > 20000:
                        continue
                    for r in range(rng.min_row, rng.max_row + 1):
                        for c in range(rng.min_col, rng.max_col + 1):
                            dropdowns[(r, c)] = str(dv.formula1).strip('"')
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                key = (cell.row, cell.column)
                if v is None and key not in dropdowns:
                    continue
                sc = SCell(text="")
                if isinstance(v, str) and v.startswith("=") or cell.data_type == "f":
                    sc.formula = str(getattr(v, "text", v))
                    has_formula = True
                else:
                    sc.text = format_value(v, cell.number_format)
                if want_color:
                    try:
                        fill = cell.fill
                        rgb = fill.fgColor.rgb if fill is not None and fill.fill_type == "solid" else None
                        if isinstance(rgb, str) and len(rgb) == 8 and rgb.upper() not in ("00000000",):
                            sc.fill = "#" + rgb[2:].upper()
                    except Exception:
                        pass
                sc.dropdown = dropdowns.get(key)
                sh.cells[key] = sc
    info: dict[str, Any] = {"formula_values": None}
    if has_formula:  # cached results of formulas live in the file; read them in a second pass
        try:
            wv = openpyxl.load_workbook(str(path), data_only=True)
            missing = 0
            for sh, ws in zip(sheets, wv.worksheets):
                for key, sc in sh.cells.items():
                    if sc.formula is None:
                        continue
                    c = ws.cell(row=key[0], column=key[1])
                    if c.value is None:
                        missing += 1
                        sc.text = sc.formula  # never calculated by a spreadsheet app: show the formula
                    else:
                        sc.text = format_value(c.value, c.number_format)
            info["formula_values"] = "cached"
            info["formulas_without_cached_value"] = missing
        except Exception:
            info["formula_values"] = "unavailable"
            for sh in sheets:
                for sc in sh.cells.values():
                    if sc.formula is not None and not sc.text:
                        sc.text = sc.formula
    props = {}
    try:
        p = wb.properties
        props = {"title": p.title, "author": p.creator, "subject": p.subject, "keywords": p.keywords,
                 "last_modified_by": p.lastModifiedBy, "created_at": iso(p.created), "modified_at": iso(p.modified)}
    except Exception:
        pass
    info["properties"] = {k: v for k, v in props.items() if v}
    return sheets, info


def read_csv(path: Path) -> tuple[list[Sheet], dict]:
    text, enc = decode_text(Path(path).read_bytes())
    sample = text[:20000]
    delimiter = "\t" if Path(path).suffix.lower() == ".tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        if sample.count("\t") > sample.count(",") and sample.count("\t") > sample.count(";"):
            delimiter = "\t"
        elif sample.count(";") > sample.count(","):
            delimiter = ";"
    sh = Sheet(name=Path(path).stem, number=1)
    for r, row in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), start=1):
        for c, v in enumerate(row, start=1):
            if v.strip():
                sh.cells[(r, c)] = SCell(text=v.strip())
    return [sh], {"encoding": enc, "delimiter": delimiter, "properties": {}}


# ---------------------------------------------------------------- clustering

Rect = tuple[int, int, int, int]  # r0, c0, r1, c1 inclusive


def _occupied(sh: Sheet, rows: set[int], cols: set[int]) -> set[tuple[int, int]]:
    occ = {k for k in sh.cells if k[0] in rows and k[1] in cols}
    for r0, c0, r1, c1 in sh.merges:
        if (r0, c0) in occ:
            occ.update((r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1) if r in rows and c in cols)
    return occ


def _runs(values: list[int], order: list[int]) -> list[list[int]]:
    """Group ``values`` into runs that are consecutive in ``order`` (visible rows / columns)."""
    pos = {v: i for i, v in enumerate(order)}
    out: list[list[int]] = []
    for v in sorted(values, key=lambda x: pos[x]):
        if out and pos[v] == pos[out[-1][-1]] + 1:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def xy_cut(occ: set[tuple[int, int]], rows: list[int], cols: list[int]) -> list[tuple[list[int], list[int]]]:
    """Recursive cut at empty rows, then empty columns. Returns (rows, cols) of each block, top-left first."""
    if not occ:
        return []
    used_rows = sorted({r for r, _ in occ})
    row_runs = _runs(used_rows, rows)
    if len(row_runs) > 1:
        out = []
        for run in row_runs:
            s = set(run)
            out.extend(xy_cut({k for k in occ if k[0] in s}, run, cols))
        return out
    used_cols = sorted({c for _, c in occ})
    col_runs = _runs(used_cols, cols)
    if len(col_runs) > 1:
        out = []
        for run in col_runs:
            s = set(run)
            out.extend(xy_cut({k for k in occ if k[1] in s}, row_runs[0], run))
        return out
    return [(row_runs[0], col_runs[0])]


# ---------------------------------------------------------------- pages

def _is_header_row(sh: Sheet, row: int, cols: list[int], n_rows: int) -> bool:
    if n_rows < 2:
        return False
    cells = [sh.cells.get((row, c)) for c in cols]
    texts = [c.text for c in cells if c is not None and c.text]
    if len(texts) < max(1, len(cols) // 2):
        return False
    return all(not _numeric(t) for t in texts) and not any(c is not None and c.formula for c in cells)


def _numeric(t: str) -> bool:
    try:
        float(t.replace(",", "").replace("%", "").replace("$", ""))
        return True
    except ValueError:
        return False


def _table_region(sh: Sheet, rows: list[int], cols: list[int], header: Optional[int], page: Page,
                  grid_rows: list[int], grid_cols: list[int], so: SpreadsheetOptions, part: Optional[int] = None):
    """One HTML table for the given sheet rows (``header`` is a sheet row repeated on top, or None)."""
    all_rows = ([header] if header is not None and header not in rows else []) + rows
    rset, cset = set(all_rows), set(cols)
    rpos = {r: i for i, r in enumerate(all_rows)}
    cpos = {c: i for i, c in enumerate(cols)}
    span: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    overlay: dict[tuple[int, int], SCell] = {}
    for r0, c0, r1, c1 in sh.merges:
        rr = [r for r in all_rows if r0 <= r <= r1]
        cc = [c for c in cols if c0 <= c <= c1]
        if not rr or not cc:
            continue
        origin = (rr[0], cc[0])
        if origin != (r0, c0) and (r0, c0) in sh.cells:
            overlay[origin] = sh.cells[(r0, c0)]  # the anchor fell outside this part: repeat its text
        if len(rr) > 1 or len(cc) > 1:
            span[origin] = (len(rr), len(cc))
            covered.update((r, c) for r in rr for c in cc if (r, c) != origin)
    gr = {r: i for i, r in enumerate(grid_rows)}
    gc = {c: i for i, c in enumerate(grid_cols)}
    nr, nc = max(len(grid_rows), 1), max(len(grid_cols), 1)
    out = ["<table>"]
    cells: list[TableCell] = []
    refs: dict[str, str] = {}
    want_formula = "formula" in so.include
    for r in all_rows:
        is_head = header is not None and r == header
        out.append("<tr>")
        for c in cols:
            if (r, c) in covered:
                continue
            sc = overlay.get((r, c)) or sh.cells.get((r, c))
            rs, cs = span.get((r, c), (1, 1))
            tag = "th" if is_head else "td"
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            if sc is not None:
                if want_formula and sc.formula:
                    attrs += f' data-formula="{esc(sc.formula).replace(chr(34), "&quot;")}"'
                if sc.fill:
                    attrs += f' style="background-color:{sc.fill}"'
                if sc.dropdown:
                    attrs += f' data-dropdown="{esc(sc.dropdown).replace(chr(34), "&quot;")}"'
            text = sc.text if sc is not None else ""
            start = sum(len(x) for x in out) + len(f"<{tag}{attrs}>")
            inner = esc(text).replace("\n", "<br>")
            out.append(f"<{tag}{attrs}>{inner}</{tag}>")
            if text or (sc is not None and sc.formula):
                box = BBox(left=gc[c] / nc, top=gr[r] / nr, width=cs / nc, height=rs / nr, page=page.index)
                cells.append(TableCell(row=rpos[r], col=cpos[c], rowspan=rs, colspan=cs, text=text, is_header=is_head,
                                       bbox=box, char_start=start, char_end=start + len(inner),
                                       confidence=EXACT.model_copy()))
                refs[f"{rpos[r]},{cpos[c]}"] = ref(r, c)
        out.append("</tr>")
    out.append("</table>")
    first = header if header is not None and header < rows[0] and part in (None, 0) else rows[0]
    top, left = gr[first], gc[cols[0]]
    box = BBox(left=left / nc, top=top / nr, width=len(cols) / nc, height=(max(gr[r] for r in rows) - top + 1) / nr,
               page=page.index)
    region = make_region(RegionType.TABLE, "".join(out), "spreadsheet", fmt="html", bbox=box,
                         sheet=sh.name, cell_refs=refs, part=part,
                         range=f"{ref(first, cols[0])}:{ref(rows[-1], cols[-1])}")
    region.cells = cells
    return region


def _text_region(sh: Sheet, rows: list[int], cols: list[int], page: Page, grid_rows: list[int],
                 grid_cols: list[int]):
    lines = []
    for r in rows:
        parts = [sh.cells[(r, c)].text for c in cols if (r, c) in sh.cells and sh.cells[(r, c)].text]
        if parts:
            lines.append(" ".join(parts))
    if not lines:
        return None
    gr = {r: i for i, r in enumerate(grid_rows)}
    gc = {c: i for i, c in enumerate(grid_cols)}
    nr, nc = max(len(grid_rows), 1), max(len(grid_cols), 1)
    box = BBox(left=gc[cols[0]] / nc, top=gr[rows[0]] / nr, width=len(cols) / nc, height=len(rows) / nr,
               page=page.index)
    return make_region(RegionType.TEXT, "\n".join(lines), "spreadsheet", fmt="text", bbox=box, sheet=sh.name,
                       range=f"{ref(rows[0], cols[0])}:{ref(rows[-1], cols[-1])}")


def sheet_page(sh: Sheet, index: int, so: SpreadsheetOptions) -> Page:
    rows_all = sorted({r for r, _ in sh.cells} | {r for m in sh.merges for r in range(m[0], m[2] + 1)})
    cols_all = sorted({c for _, c in sh.cells} | {c for m in sh.merges for c in range(m[1], m[3] + 1)})
    if rows_all:
        rows_all = list(range(rows_all[0], rows_all[-1] + 1))
        cols_all = list(range(cols_all[0], cols_all[-1] + 1))
    if "hidden_rows" in so.exclude:
        rows_all = [r for r in rows_all if r not in sh.hidden_rows]
    if "hidden_cols" in so.exclude:
        cols_all = [c for c in cols_all if c not in sh.hidden_cols]
    page = new_page(index, sh.number, width=float(max(len(cols_all), 1) * 80), height=float(max(len(rows_all), 1) * 20),
                    unit="px", sheet_name=sh.name, sheet_hidden=sh.hidden or None)
    if not rows_all or not cols_all:
        return page
    occ = _occupied(sh, set(rows_all), set(cols_all))
    if so.clustering == "disabled":
        used_r = sorted({r for r, _ in occ})
        used_c = sorted({c for _, c in occ})
        blocks = [([r for r in rows_all if used_r[0] <= r <= used_r[-1]],
                   [c for c in cols_all if used_c[0] <= c <= used_c[-1]])] if occ else []
    else:
        blocks = xy_cut(occ, rows_all, cols_all)
    for rows, cols in blocks:
        if len(cols) < 2 and so.clustering != "disabled":
            region = _text_region(sh, rows, cols, page, rows_all, cols_all)
            if region is not None:
                page.regions.append(region)
            continue
        header = rows[0] if _is_header_row(sh, rows[0], cols, len(rows)) else None
        body = rows[1:] if header is not None else rows
        col_parts = [cols]
        if so.split.enabled and so.split.cols and len(cols) > so.split.cols:
            col_parts = [cols[i:i + so.split.cols] for i in range(0, len(cols), so.split.cols)]
        row_parts = [body]
        if so.split.enabled and so.split.rows and len(body) > so.split.rows:
            row_parts = [body[i:i + so.split.rows] for i in range(0, len(body), so.split.rows)]
        many = len(col_parts) * len(row_parts) > 1
        n = 0
        for rp in row_parts:
            for cp in col_parts:
                if not rp and header is None:
                    continue
                page.regions.append(_table_region(sh, rp or [header], cp, header if rp else None, page, rows_all,
                                                  cols_all, so, part=n if many else None))
                n += 1
    return page


def load_sheets(sheets: list[Sheet], so: SpreadsheetOptions, page_range: Any = None,
                titles: bool = False) -> tuple[list[Page], dict]:
    """Select sheets (page_range numbers or names, hidden-sheet exclusion), check the cell limit, build pages.
    ``titles``: start each sheet with a Title block holding the sheet name, as Reducto does for workbooks."""
    try:
        wanted = resolve_page_range(page_range, len(sheets), [s.name for s in sheets])
    except PageRangeError as e:
        raise IntakeError(str(e)) from e
    chosen = [s for s in sheets if s.number in wanted]
    if "hidden_sheets" in so.exclude:
        chosen = [s for s in chosen if not s.hidden]
    count = 0
    for s in chosen:
        rows_skip = s.hidden_rows if "hidden_rows" in so.exclude else set()
        cols_skip = s.hidden_cols if "hidden_cols" in so.exclude else set()
        count += sum(1 for (r, c), cell in s.cells.items()
                     if cell.text and r not in rows_skip and c not in cols_skip)  # Reducto: cells with a value
    if so.max_cell_count is not None and count > int(so.max_cell_count):
        raise cell_count_exceeded(count, int(so.max_cell_count))
    pages = [sheet_page(s, i, so) for i, s in enumerate(chosen)]
    if titles:
        from ..model import BBox, RegionType
        from .common import make_region
        for s, p in zip(chosen, pages):
            p.regions.insert(0, make_region(RegionType.TITLE, f"# {s.name}", "xlsx", level=1, sheet_title=True,
                                            bbox=BBox(left=0.0, top=0.0, width=0.0, height=0.0, page=p.index)))
    for p in pages:
        for k, r in enumerate(p.regions):
            r.order = k
    return pages, {"non_empty_cell_count": count, "sheet_names": [s.name for s in sheets], "total_pages": len(sheets),
                   "clustering": "fast" if so.clustering == "accurate" else so.clustering}
