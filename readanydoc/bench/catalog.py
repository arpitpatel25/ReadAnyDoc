"""Public test sets, by document type. Each is downloaded on first use into the ReadAnyDoc cache (nothing is
redistributed with ReadAnyDoc) and pinned to a fixed revision so that a score can be reproduced.

Every set has an answer key written by people or generated with the documents, and is scored by code
(``grade.py``), never by a model. Check a set's licence before you use it: some allow research use only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import fetch

EB_REPO, EB_REV = "ContextualAI/extract-bench", "ca817638852a23bb17ca9aaeb82ccdd29e9af9ca"
INV_REPO = "drew-ipp/invoice-extraction-benchmark"
LX_REPO = "micro1-inc/longextract-bench-50"
CORD_REPO = "naver-clova-ix/cord-v2"


@dataclass
class TestSet:
    id: str
    title: str
    doc_type: str
    what: str
    documents: int
    licence: str
    source: str
    default_limit: int
    load: Callable[[Optional[int]], list[dict]]
    note: str = ""
    tags: list[str] = field(default_factory=list)

    def info(self) -> dict:
        return {"id": self.id, "title": self.title, "document_type": self.doc_type, "what_it_tests": self.what,
                "documents": self.documents, "licence": self.licence, "source": self.source,
                "default_sample": self.default_limit, "note": self.note}


# ---------------------------------------------------------------- ExtractBench (Contextual AI, MIT)

def _eb_strip(n: Any) -> Any:
    if isinstance(n, dict):
        return {k: _eb_strip(v) for k, v in n.items() if k != "evaluation_config"}
    if isinstance(n, list):
        return [_eb_strip(v) for v in n]
    return n


def _extractbench(folder: str) -> Callable[[Optional[int]], list[dict]]:
    def load(limit: Optional[int]) -> list[dict]:
        tree = fetch.github_tree(EB_REPO, EB_REV)
        base = f"dataset/{folder}/"
        schema_path = next(p for p in tree if p.startswith(base) and p.endswith("-schema.json"))
        schema = json.loads(fetch.github_file(EB_REPO, EB_REV, schema_path).read_text())
        if "schema_definition" in schema and "type" not in schema:
            schema = schema["schema_definition"]
        pdfs = sorted(p for p in tree if p.startswith(base + "pdf+gold/") and p.endswith(".pdf")
                      and p[:-4] + ".gold.json" in tree)
        out = []
        for p in pdfs[:limit] if limit else pdfs:
            stem = Path(p).stem
            out.append({"id": re.sub(r"[^\w.-]+", "_", stem), "file": str(fetch.github_file(EB_REPO, EB_REV, p)),
                        "schema": _eb_strip(schema),
                        "gold": json.loads(fetch.github_file(EB_REPO, EB_REV, p[:-4] + ".gold.json").read_text())})
        return out
    return load


# ---------------------------------------------------------------- invoices (InvoiceParser Pro, CC-BY-4.0)

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice", "credit_note", "other"],
                          "description": "other for a supplier statement"},
        "vendor_name": {"type": "string", "description": "the company that issued the document"},
        "invoice_number": {"type": "string"},
        "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "currency": {"type": "string", "description": "ISO 4217 code, e.g. USD"},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": "number", "description": "the amount payable"},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "description": {"type": "string"}, "amount": {"type": "number", "description": "the line total"}}}},
    },
}


# Hard input types first, so that even a small sample has photos, scans and handwriting, not only clean PDFs.
_INVOICE_ORDER = ["photo", "scan", "handwritten", "multipage", "hard", "freight", "credit", "tax", "currency", "clean"]
_DIFFICULTY = ["hard", "medium", "easy"]


def _spread(meta: list[dict], limit: int) -> list[dict]:
    """One document of every category before a second of any, each category's documents mixed across difficulties
    (the starting difficulty rotates between categories), so a sample of any size covers the whole set."""
    by: dict[str, list] = {}
    for m in meta:
        by.setdefault(m["category"], []).append(m)
    cats = [c for c in _INVOICE_ORDER if c in by] + sorted(c for c in by if c not in _INVOICE_ORDER)
    for ci, c in enumerate(cats):
        levels = _DIFFICULTY[ci % 3:] + _DIFFICULTY[:ci % 3]
        groups = [[m for m in by[c] if m.get("difficulty") == d] for d in levels]
        groups.append([m for m in by[c] if m.get("difficulty") not in _DIFFICULTY])
        mixed = []
        while any(groups):
            for g in groups:
                if g:
                    mixed.append(g.pop(0))
        by[c] = mixed
    out, i = [], 0
    while len(out) < min(limit, len(meta)):
        for c in cats:
            if i < len(by[c]) and len(out) < limit:
                out.append(by[c][i])
        i += 1
    return out


def _invoices(limit: Optional[int]) -> list[dict]:
    meta = [json.loads(l) for l in fetch.hf_file(INV_REPO, "corpus/v1/metadata.jsonl").read_text().splitlines()
            if l.strip()]
    if limit:
        meta = _spread(meta, limit)
    out = []
    for m in meta:
        exp = m["expected"]
        gold = {k: exp.get(k) for k in INVOICE_SCHEMA["properties"] if k != "lines"}
        gold["lines"] = [{"description": l.get("description"), "amount": l.get("amount")} for l in exp.get("lines") or []]
        alts = {f"lines[{i}].amount": list(l.get("alt_amounts") or []) for i, l in enumerate(exp.get("lines") or [])}
        if exp.get("document_type") == "credit_note":  # the set's rule: credit notes compare magnitudes
            for k in ("subtotal", "tax", "total"):
                if isinstance(gold.get(k), (int, float)):
                    alts.setdefault(k, []).append(-gold[k])
            for i, l in enumerate(gold["lines"]):
                if isinstance(l.get("amount"), (int, float)):
                    alts[f"lines[{i}].amount"].append(-l["amount"])
        scored = [s for s in m.get("scored") or [] if s in INVOICE_SCHEMA["properties"]] + \
            (["lines"] if "line_items" in (m.get("scored") or []) else [])
        out.append({"id": m["doc_id"], "file": str(fetch.hf_file(INV_REPO, "corpus/v1/" + m["file_name"])),
                    "schema": INVOICE_SCHEMA, "gold": gold, "alternatives": alts, "only": scored or None,
                    "meta": {"category": m["category"], "difficulty": m.get("difficulty")}})
    return out


# ---------------------------------------------------------------- receipts (CORD v2, CC-BY-4.0)

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": ["string", "null"]},
            "price": {"type": ["string", "null"], "description": "the line price as printed"}}}},
        "subtotal": {"type": ["string", "null"], "description": "as printed"},
        "tax": {"type": ["string", "null"], "description": "as printed"},
        "total": {"type": ["string", "null"], "description": "the total paid, as printed"},
    },
}


def _cord(limit: Optional[int]) -> list[dict]:
    rows = fetch.hf_rows(CORD_REPO, "test", limit or 100)
    out = []
    for i, r in rows:
        gt = json.loads(r["ground_truth"]).get("gt_parse", {})
        menu = gt.get("menu") or []
        menu = [menu] if isinstance(menu, dict) else menu
        sub, tot = gt.get("sub_total") or {}, gt.get("total") or {}
        gold = {"items": [{"name": m.get("nm"), "quantity": m.get("cnt"), "price": m.get("price")} for m in menu
                          if isinstance(m, dict)],
                "subtotal": sub.get("subtotal_price") if isinstance(sub, dict) else None,
                "tax": sub.get("tax_price") if isinstance(sub, dict) else None,
                "total": tot.get("total_price") if isinstance(tot, dict) else None}
        out.append({"id": f"cord_test_{i:03d}", "file": str(fetch.hf_row_image(CORD_REPO, "test", i, r["image"])),
                    "schema": RECEIPT_SCHEMA, "gold": gold})
    return out


# ---------------------------------------------------------------- long documents (LongExtract, CC-BY-4.0)

def _longextract(limit: Optional[int]) -> list[dict]:
    tree = fetch.hf_tree(LX_REPO)
    docs = sorted((size, path.rsplit("/", 1)[0]) for path, size in tree.items() if path.endswith("/document.pdf"))
    out = []
    for _, d in docs[:limit] if limit else docs:  # shortest first: the longest has 11,622 pages
        out.append({"id": re.sub(r"[^\w.-]+", "_", d)[:80], "file": str(fetch.hf_file(LX_REPO, d + "/document.pdf")),
                    "schema": json.loads(fetch.hf_file(LX_REPO, d + "/schema.json").read_text()),
                    "gold": json.loads(fetch.hf_file(LX_REPO, d + "/ground_truth.json").read_text())})
    return out


SETS: dict[str, TestSet] = {s.id: s for s in [
    TestSet("invoices", "Invoices", "invoices and credit notes",
            "digital PDFs in US/UK/EU/ZA formats, 6 currencies, VAT, credit notes, multi-page, freight, scans, phone "
            "photos, handwritten invoice books, several invoices in one file",
            181, "CC-BY-4.0 (made by InvoiceParser Pro, a vendor; synthetic documents)",
            f"https://huggingface.co/datasets/{INV_REPO}", 20, _invoices,
            note="Our field-by-field grader with the set's rules (credit notes compare magnitudes), not its own scorer."),
    TestSet("receipts", "Restaurant and shop receipts (CORD)", "receipts", "photographed Indonesian receipts: items, "
            "quantities, prices, subtotal, tax, total", 100, "CC-BY-4.0", f"https://huggingface.co/datasets/{CORD_REPO}",
            20, _cord),
    TestSet("financial-reports", "Quarterly and annual reports (10-Q, 10-K)", "financial filings",
            "income statement, balance sheet and cash-flow values with periods, units and scales, by segment",
            7, "MIT (ExtractBench)", f"https://github.com/{EB_REPO}", 2, _extractbench("finance/10kq"),
            note="Long documents (40-100+ pages); a schema the API cannot enforce in one call."),
    TestSet("credit-agreements", "Credit agreements", "legal contracts", "parties, facilities, amounts, rates and key "
            "terms from corporate credit agreements", 10, "MIT (ExtractBench)", f"https://github.com/{EB_REPO}", 2,
            _extractbench("finance/credit_agreement"), note="Very long documents (often 100+ pages)."),
    TestSet("research-papers", "Research papers", "academic papers", "title, authors, affiliations, abstract, "
            "identifiers and references", 6, "MIT (ExtractBench)", f"https://github.com/{EB_REPO}", 3,
            _extractbench("academic/research")),
    TestSet("resumes", "Resumes", "resumes and CVs", "experience, education, skills and certifications", 7,
            "MIT (ExtractBench)", f"https://github.com/{EB_REPO}", 3, _extractbench("hiring/resume")),
    TestSet("sports-results", "Championship results", "result tables", "rankings, times, records and athlete details "
            "from long result tables", 5, "MIT (ExtractBench)", f"https://github.com/{EB_REPO}", 2,
            _extractbench("sport/swimming")),
    TestSet("long-documents", "Long government and business reports (LongExtract)", "long reports",
            "every row of long repeated tables across many pages (statistics, budgets, utilisation reports)", 50,
            "CC-BY-4.0", f"https://huggingface.co/datasets/{LX_REPO}", 2, _longextract,
            note="Some documents run to thousands of pages; samples take the shortest first."),
]}


def get(set_id: str) -> TestSet:
    if set_id not in SETS:
        raise KeyError(f"unknown test set {set_id!r}; known: {', '.join(SETS)}")
    return SETS[set_id]


def own_documents(folder: str, schema_path: str, answers: Optional[str] = None,
                  instructions: Optional[str] = None) -> list[dict]:
    """Your own documents: every file in ``folder`` against one schema; an answer key per document is optional
    (``<answers>/<file stem>.json``). Without answer keys, systems are compared on cost, time and agreement."""
    schema = json.loads(Path(schema_path).read_text())
    out = []
    for f in sorted(Path(folder).iterdir()):
        if f.is_file() and not f.name.startswith("."):
            gold = None
            if answers and (Path(answers) / (f.stem + ".json")).exists():
                gold = json.loads((Path(answers) / (f.stem + ".json")).read_text())
            out.append({"id": re.sub(r"[^\w.-]+", "_", f.stem), "file": str(f), "schema": schema, "gold": gold,
                        **({"instructions": instructions} if instructions else {})})
    return out
