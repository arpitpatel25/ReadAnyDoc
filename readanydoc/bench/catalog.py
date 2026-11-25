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


def _eb_schema(folder: str) -> dict:
    tree = fetch.github_tree(EB_REPO, EB_REV)
    schema_path = next(p for p in tree if p.startswith(f"dataset/{folder}/") and p.endswith("-schema.json"))
    schema = json.loads(fetch.github_file(EB_REPO, EB_REV, schema_path).read_text())
    if "schema_definition" in schema and "type" not in schema:
        schema = schema["schema_definition"]
    return schema


def _extractbench(folder: str) -> Callable[[Optional[int]], list[dict]]:
    def load(limit: Optional[int]) -> list[dict]:
        tree = fetch.github_tree(EB_REPO, EB_REV)
        base = f"dataset/{folder}/"
        schema = _eb_schema(folder)
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


# ---------------------------------------------------------------- commercial contracts (CUAD, The Atticus Project, CC-BY-4.0)

CUAD_REPO = "theatticusproject/cuad"
_CUAD_CLAUSES = {  # schema field: (master_clauses.csv column, what the clause is, from CUAD's own category definitions)
    "non_compete": ("Non-Compete-Answer", "restricts a party from competing with the other party or operating in a "
                    "certain geography, business or technology"),
    "exclusivity": ("Exclusivity-Answer", "an exclusive dealing commitment: a party must deal only with the other "
                    "party, or a party is the exclusive provider in a territory or for a product"),
    "termination_for_convenience": ("Termination For Convenience-Answer", "a party can terminate without cause, "
                                    "solely by giving notice"),
    "change_of_control": ("Change Of Control-Answer", "a party's rights change (termination, consent, notice) when "
                          "the other party has a change of control, merger or sale of assets"),
    "anti_assignment": ("Anti-Assignment-Answer", "assigning the contract requires consent or notice of the other "
                        "party"),
    "audit_rights": ("Audit Rights-Answer", "a party may audit the books, records or premises of the other party"),
    "cap_on_liability": ("Cap On Liability-Answer", "a cap on liability, or a time limit on bringing claims"),
    "uncapped_liability": ("Uncapped Liability-Answer", "a party's liability is uncapped for some breach or claim"),
    "insurance": ("Insurance-Answer", "a party must maintain insurance"),
    "license_grant": ("License Grant-Answer", "a license is granted by one party to the other"),
    "ip_ownership_assignment": ("Ip Ownership Assignment-Answer", "intellectual property created by one party "
                                "becomes the property of the other party"),
    "most_favored_nation": ("Most Favored Nation-Answer", "if a third party gets better terms, the other party is "
                            "entitled to those better terms"),
}
CUAD_SCHEMA = {
    "type": "object",
    "properties": {
        "document_name": {"type": ["string", "null"], "description": "the contract's title as printed, e.g. "
                          "Distributor Agreement"},
        "parties": {"type": "array", "items": {"type": "string"},
                    "description": "the legal name of each party to the contract, without its role"},
        "agreement_date": {"type": ["string", "null"], "format": "date",
                           "description": "the date the contract is dated or signed"},
        "effective_date": {"type": ["string", "null"], "format": "date",
                           "description": "the date the contract takes effect"},
        "expiration_date": {"type": ["string", "null"], "format": "date",
                            "description": "the date the initial term ends, when the contract gives one or it "
                                         "follows from the effective date and the term"},
        "governing_law": {"type": ["string", "null"], "description": "the state or country whose law governs, the "
                          "name only, e.g. Nevada, New York, England"},
        "clauses": {"type": "object", "description": "whether the contract has each kind of clause",
                    "properties": {k: {"type": "boolean", "description": f"the contract has a clause: {d}"}
                                   for k, (_, d) in _CUAD_CLAUSES.items()},
                    "required": list(_CUAD_CLAUSES)},
    },
    "required": ["document_name", "parties", "agreement_date", "effective_date", "expiration_date", "governing_law",
                 "clauses"],
}


def _cuad_date(text: str) -> Optional[str]:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*", text or "")
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + (2000 if y < 50 else 1900) if y < 100 else y
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None


def _cuad_gold(row: dict) -> dict:
    """The lawyers' answers (master_clauses.csv) in the schema's shape; a field whose answer is not a plain value
    (several dates, "Perpetual", a sentence) is left out of the key and not scored."""
    g: dict = {}
    name = (row.get("Document Name-Answer") or "").strip()
    if name and len(name) <= 120:
        g["document_name"] = name
    parties = []
    for part in (row.get("Parties-Answer") or "").split(";"):
        part = re.sub(r"\(\s*[\"“][^)]*\)", "", part)  # the role in brackets: ("Licensor")
        part = part.strip().strip('"“”').strip()
        if len(part) > 3 and not re.fullmatch(r"(the )?(company|licensor|licensee|party|customer|supplier)s?", part, re.I):
            parties.append(part)
    if parties:
        g["parties"] = parties
    for key, col in (("agreement_date", "Agreement Date-Answer"), ("effective_date", "Effective Date-Answer"),
                     ("expiration_date", "Expiration Date-Answer")):
        d = _cuad_date(row.get(col) or "")
        if d:
            g[key] = d
    law = (row.get("Governing Law-Answer") or "").strip()
    if law and len(law) <= 40:
        g["governing_law"] = law
    cl = {k: (row.get(col) or "").strip().lower() == "yes" for k, (col, _) in _CUAD_CLAUSES.items()
          if (row.get(col) or "").strip().lower() in ("yes", "no")}
    if cl:
        g["clauses"] = cl
    return g


def _cuad(limit: Optional[int]) -> list[dict]:
    import csv
    import hashlib
    tree = fetch.hf_tree(CUAD_REPO)
    pdfs = {Path(p).name.lower(): (p, size) for p, size in tree.items()
            if p.startswith("CUAD_v1/full_contract_pdf/") and p.lower().endswith(".pdf")}
    rows = list(csv.DictReader(open(fetch.hf_file(CUAD_REPO, "CUAD_v1/master_clauses.csv"), encoding="utf-8")))
    # a fixed spread over contract types, each under ~600 KB (most such contracts are 5-30 pages): one contract per
    # type in turn, in an order fixed by a hash of the file name
    by_type: dict[str, list] = {}
    for r in rows:
        hit = pdfs.get((r.get("Filename") or "").lower())
        if not hit or hit[1] > 600_000:
            continue
        kind = hit[0].split("/")[-2]
        by_type.setdefault(kind, []).append((hashlib.sha1(r["Filename"].encode()).hexdigest(), hit[0], r))
    queues = [sorted(v) for _, v in sorted(by_type.items())]
    picked = []
    while any(queues) and (not limit or len(picked) < limit):
        for q in queues:
            if q and (not limit or len(picked) < limit):
                picked.append(q.pop(0))
    out = []
    for _, path, r in picked:
        out.append({"id": re.sub(r"[^\w.-]+", "_", Path(path).stem)[:80], "file": str(fetch.hf_file(CUAD_REPO, path)),
                    "schema": CUAD_SCHEMA, "gold": _cuad_gold(r)})
    return out


# ---------------------------------------------------------------- 10-Q filings vs SEC XBRL (FinanceBench PDFs)

FB_REPO, FB_REV = "patronus-ai/financebench", "cc39aeb4afdf33909ee1412188bf89035950c2eb"
# FinanceBench 10-Q PDF -> (SEC CIK, accession number of that filing). One per company, banks left out; Lockheed
# Martin and PG&E left out because no 10-Q accession matches their cover dates.
SEC_FILINGS = {
    "3M_2023Q2_10Q": (66740, "0000066740-23-000058"), "ACTIVSIONBLIZZARD_2023Q2_10Q": (718877, "0001628280-23-026269"),
    "ADOBE_2022Q2_10Q": (796343, "0000796343-23-000153"), "AMCOR_2023Q2_10Q": (1748790, "0001748790-23-000007"),
    "AMERICANWATERWORKS_2023Q2_10Q": (1410636, "0001410636-23-000116"), "APPLE_2023Q3_10Q": (320193, "0000320193-23-000077"),
    "BESTBUY_2024Q2_10Q": (764478, "0000764478-23-000041"), "CORNING_2023Q2_10Q": (24741, "0001437749-23-020854"),
    "EBAY_2023Q2_10Q": (1065088, "0001065088-23-000026"), "FOOTLOCKER_2023Q3_10Q": (850209, "0000850209-22-000025"),
    "MGMRESORTS_2023Q2_10Q": (789570, "0000789570-23-000019"), "NETFLIX_2023Q2_10Q": (1065280, "0001065280-23-000198"),
    "PAYPAL_2023Q2_10Q": (1633917, "0001633917-23-000117"), "Pfizer_2023Q2_10Q": (78003, "0000078003-23-000088"),
    "SALESFORCE_2023Q3_10Q": (1108524, "0001108524-22-000060"), "WALMART_2024Q1_10Q": (104169, "0000104169-23-000052"),
}


def _sec_filings(limit: Optional[int]) -> list[dict]:
    from . import sec
    schema = _eb_schema("finance/10kq")
    out = []
    for name, (cik, accn) in list(SEC_FILINGS.items())[:limit] if limit else SEC_FILINGS.items():
        out.append({"id": name, "file": str(fetch.github_file(FB_REPO, FB_REV, f"pdfs/{name}.pdf")), "schema": schema,
                    "gold": {"accession": accn, "facts": sec.facts_of(fetch.sec_companyfacts(cik), accn)},
                    "scorer": "sec"})
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
    TestSet("contracts", "Commercial contracts (CUAD)", "commercial contracts",
            "title, parties, agreement, effective and expiry dates, governing law, and whether 12 kinds of clause are "
            "present (non-compete, exclusivity, termination for convenience, change of control, caps on "
            "liability...), against lawyers' answers", 510, "CC-BY-4.0 (The Atticus Project)",
            f"https://huggingface.co/datasets/{CUAD_REPO}", 10, _cuad,
            note="Answer key: CUAD's master_clauses.csv. Fields whose answer is not a plain value are not scored."),
    TestSet("sec-filings", "10-Q filings checked against SEC data", "financial filings",
            "income statement, balance sheet and cash-flow values of 16 companies' 10-Qs (40-100 pages), against the "
            "numbers each company tagged itself in its SEC filing (XBRL): no hand-made answer key",
            16, "PDFs: FinanceBench (CC-BY-NC-4.0, research use); answer key: SEC data, public domain",
            f"https://github.com/{FB_REPO}", 3, _sec_filings,
            note="Recall = tagged values found; precision = extracted company values the company also tagged (a "
                 "lower bound: untagged sub-lines count as wrong). Scorer: readanydoc/bench/sec.py."),
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
