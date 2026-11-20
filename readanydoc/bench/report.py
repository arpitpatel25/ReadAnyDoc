"""An HTML report of a run: the summary per system, every document, and for each document a page viewer with every
system's citation boxes (click a box to see its field, click a field to see its box) next to the answer key."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Optional

from . import grade
from .runner import load

E = html.escape
_CSS = """body{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#222}table{border-collapse:collapse;
font-size:13px}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f3f3f3}
.ok{color:#080}.bad{color:#c00}.muted{color:#888}h1{font-size:22px}a{color:#0060c0}"""


def _pct(x: Optional[float]) -> str:
    return "" if x is None else f"{100 * x:.1f}%"


def _usd(x: Optional[float]) -> str:
    return "" if x is None else f"${x:.4f}"


def write(out: str, cases: list[dict], systems: list[str], title: str, summary: dict, info: Optional[dict] = None) -> str:
    d = Path(out)
    rows = []
    for s in systems:
        r = summary["systems"].get(s, {})
        rows.append(f"<tr><td><b>{E(s)}</b></td><td>{r.get('ran', 0)}</td><td>{r.get('failed', 0)}</td>"
                    f"<td>{_pct(r.get('recall'))}</td><td>{_pct(r.get('precision'))}</td>"
                    f"<td>{_pct(r.get('exact_documents'))}</td><td>{_pct(r.get('schema_valid'))}"
                    f"{' <span class=muted>(' + _pct(r.get('schema_valid_strict')) + ' strict)</span>' if r.get('schema_valid_strict') is not None and r.get('schema_valid_strict') != r.get('schema_valid') else ''}</td>"
                    f"<td>{_pct(r.get('values_with_citation'))}</td>"
                    f"<td>{_pct(r.get('citations_showing_value'))}</td><td>{_usd(r.get('cost_per_document_usd'))}"
                    f"{' <span class=muted>(' + E(r['cost_note']) + ')</span>' if r.get('cost_note') else ''}</td>"
                    f"<td>{r.get('median_seconds') or ''}</td></tr>")
    docs = []
    for c in cases:
        cells = []
        for s in systems:
            rec = load(out, s, c["id"])
            if rec is None:
                cells.append("<td class=muted>not run</td>")
                continue
            sc = rec.get("score")
            txt = (f"{_pct(sc['recall'])} ({sc['right']}/{sc['expected']})" if sc else "no answer key")
            err = f"<br><span class=bad>{E(rec['error'][:120])}</span>" if rec.get("error") else ""
            cells.append(f"<td>{txt}<br><span class=muted>{_usd(rec.get('cost_usd'))} · {rec.get('seconds')} s"
                         f"</span>{err}</td>")
        meta = " ".join(f"{k}: {v}" for k, v in (c.get("meta") or {}).items() if v)
        docs.append(f"<tr><td><a href='docs/{E(c['id'])}.html'>{E(c['id'])}</a><br><span class=muted>{E(meta)}</span>"
                    f"</td>{''.join(cells)}</tr>")
        _doc_page(d, c, systems)
    about = ""
    if info:
        about = (f"<p>{E(info.get('what_it_tests', ''))}<br><span class=muted>Source: <a href='{E(info['source'])}'>"
                 f"{E(info['source'])}</a> · Licence: {E(info['licence'])}</span></p>")
    page = f"""<!doctype html><html><head><meta charset=utf-8><title>{E(title)}</title><style>{_CSS}</style></head><body>
<h1>{E(title)}</h1>{about}
<table><tr><th>System</th><th>Documents</th><th>Failed</th><th>Answer-key values found</th><th>Output values right</th>
<th>Documents fully right</th><th>Pass the JSON Schema</th><th>Values with a citation</th><th>Citations showing the value</th><th>Cost per document</th>
<th>Median seconds</th></tr>{''.join(rows)}</table>
<p class=muted>Scored by code against the answer key (ReadAnyDoc's grader: numbers, dates and text compared after
normalising their printed form; list items paired before comparing). No model judges the output. A failed document
counts as an empty answer. Citations are checked only on PDFs, by reading the words inside each box from the PDF.</p>
<h2>Documents</h2><table><tr><th>Document</th>{''.join(f'<th>{E(s)}</th>' for s in systems)}</tr>{''.join(docs)}</table>
</body></html>"""
    p = d / "report.html"
    p.write_text(page)
    return str(p)


def _pages(case: dict, folder: Path) -> list[str]:
    f = case["file"]
    folder.mkdir(parents=True, exist_ok=True)
    if f.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        out = []
        pdf = pdfium.PdfDocument(f)
        for i in range(min(len(pdf), 60)):  # the first 60 pages are drawn; boxes beyond are listed only
            dest = folder / f"{case['id']}_p{i + 1}.png"
            if not dest.exists():
                pdf[i].render(scale=1.5).to_pil().convert("RGB").save(dest)
            out.append(dest.name)
        return out
    if Path(f).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        dest = folder / (case["id"] + Path(f).suffix.lower())
        if not dest.exists():
            shutil.copy(f, dest)
        return [dest.name]
    return []


def _doc_page(d: Path, case: dict, systems: list[str]) -> None:
    pages = _pages(case, d / "docs" / "pages")
    recs = {s: load(str(d), s, case["id"]) for s in systems}
    colors = ["#0a0", "#06f", "#e80", "#c0c", "#c00"]
    boxes, table = [], []
    n = 0
    gold = dict(grade.leaves(case["gold"])) if case.get("gold") is not None else {}
    paths: list[str] = list(gold)
    for s in systems:
        for p, _ in grade.leaves((recs[s] or {}).get("data") or {}):
            if p not in paths:
                paths.append(p)
    for si, s in enumerate(systems):
        rec = recs[s] or {}
        for path, cs in (rec.get("citations") or {}).items():
            for c in cs:
                b = c.get("bbox") or {}
                if not b.get("page") or int(b["page"]) > len(pages):
                    continue
                n += 1
                boxes.append((n, si, s, path, b))
    got = {s: dict(grade.leaves((recs[s] or {}).get("data") or {})) for s in systems}
    for path in paths:
        cells = []
        for si, s in enumerate(systems):
            v = got[s].get(path)
            ok = (grade.same(gold[path], v) if path in gold and not grade._blank(gold[path]) else None)
            cls = "" if ok is None else ("ok" if ok else "bad")
            ids = [str(i) for i, ssi, _, p, _ in boxes if ssi == si and p == path]
            cells.append(f"<td class='{cls}' data-boxes='{','.join(ids)}' onclick='pick(this)'>"
                         f"{E('' if v is None else str(v))[:200]}</td>")
        table.append(f"<tr><td>{E(path)}</td><td>{E(str(gold.get(path, '')))[:200]}</td>{''.join(cells)}</tr>")
    page_html = []
    for pi, img in enumerate(pages):
        bx = "".join(
            f"<div class=box id=b{i} title='{E(s)}: {E(path)}' onclick='pickbox({i})' style='border-color:{colors[si % 5]};"
            f"left:{b['left'] * 100}%;top:{b['top'] * 100}%;width:{b['width'] * 100}%;height:{b['height'] * 100}%'></div>"
            for i, si, s, path, b in boxes if int(b["page"]) == pi + 1)
        page_html.append(f"<h3>Page {pi + 1}</h3><div class=pg><img src='pages/{E(img)}'>{bx}</div>")
    legend = " ".join(f"<span style='color:{colors[i % 5]}'>■ {E(s)}</span>" for i, s in enumerate(systems))
    (d / "docs" / f"{case['id']}.html").write_text(f"""<!doctype html><html><head><meta charset=utf-8>
<title>{E(case['id'])}</title><style>{_CSS}
#wrap{{display:flex;gap:16px}}#pages{{flex:1.2;max-height:92vh;overflow:auto}}#side{{flex:1;max-height:92vh;overflow:auto}}
.pg{{position:relative;max-width:900px;border:1px solid #bbb}}.pg img{{width:100%;display:block}}
.box{{position:absolute;border:2px solid;box-sizing:border-box;cursor:pointer}}.sel{{background:rgba(0,120,255,.3);
border-width:4px}}td.hl{{background:#cfe2ff}}</style></head><body><a href='../report.html'>← report</a>
<h1>{E(case['id'])}</h1><p>{legend} · click a value to see its box, click a box to see its value</p>
<div id=wrap><div id=pages>{''.join(page_html) or '<p class=muted>no page images for this file type</p>'}</div>
<div id=side><table><tr><th>Field</th><th>Answer key</th>{''.join(f'<th>{E(s)}</th>' for s in systems)}</tr>
{''.join(table)}</table></div></div><script>
function clear(){{document.querySelectorAll('.sel').forEach(e=>e.classList.remove('sel'));
document.querySelectorAll('td.hl').forEach(e=>e.classList.remove('hl'))}}
function pick(td){{clear();td.classList.add('hl');let ids=td.dataset.boxes?td.dataset.boxes.split(','):[];
ids.forEach(i=>{{let b=document.getElementById('b'+i);b.classList.add('sel')}});
if(ids.length)document.getElementById('b'+ids[0]).scrollIntoView({{block:'center',behavior:'smooth'}})}}
function pickbox(i){{clear();document.getElementById('b'+i).classList.add('sel');
let td=[...document.querySelectorAll('td[data-boxes]')].find(t=>t.dataset.boxes.split(',').includes(String(i)));
if(td){{td.classList.add('hl');td.scrollIntoView({{block:'center',behavior:'smooth'}})}}}}
</script></body></html>""")


def write_json(out: str, summary: dict) -> None:
    (Path(out) / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
