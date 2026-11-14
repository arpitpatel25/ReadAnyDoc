"""Split: section descriptions in, page lists out (see tasks/__init__.py for the contract).

One model call per window of pages assigns pages to the described sections (and to partitions where
a ``partition_key`` is set). The answer is then cleaned deterministically: unknown names dropped,
page numbers clamped to the document, overlap rules applied. ``deep_split`` adds one verify pass in
which the model sees the proposed split next to the pages and may correct it; every correction and
every page the checks flag is reported with ``conf: "low"``.

Response shapes follow Reducto's real responses (compared 2026-11-12): sections with no pages are
left out, ``section_mapping`` is null, and ``deep_split`` returns ``DeepSplitResult``: every page
carries an ``evidence`` quote (checked against the page text; ``confidence`` is "high" when the quote
is on the page, "low" otherwise) and, with ``auto_partition``, each section is grouped into the
individual documents it contains.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .common import DocView, PageRef, TaskError, render_pages, sum_usage
from .llm import LLM, LLMError, LLMResult, ask_json

DEFAULT_RULES = ("Split the document into the applicable sections. Sections may only overlap at their "
                 "first and last page if at all.")
DEFAULTS: dict[str, Any] = {
    "window_pages": 40,        # pages per model call
    "truncate_chars": 8000,    # per-page text budget when table_cutoff == "truncate" (head and tail are kept)
    "preserve_chars": 20000,   # per-page text budget when table_cutoff == "preserve"
    "max_tokens": 8000,
}

SYSTEM = """You split a document into sections. You are given the section definitions, the splitting rules and the text of the pages.
Decide for every section which pages belong to it. Page numbers are the ones in the "=== Page N ===" markers.
- Use only the section names given. A section that does not occur gets an empty page list.
- A page that fits no section is left out.
- "conf" is "high" when the page text clearly supports the assignment and "low" when you are unsure.
- For a section with a partition key, also group its pages by the value of that key as it appears in the text (for example one partition per account number). A partition name is the key value exactly as written.
Reply with JSON only, in this shape:
{"sections": [{"name": "<section name>", "pages": [1, 2], "conf": "high", "partitions": [{"name": "<key value>", "pages": [1], "conf": "high"}]}]}"""

DEEP_SYSTEM = SYSTEM.split("Reply with JSON only")[0] + """- For every page you assign, give "evidence": a short quote copied exactly from that page (a title, form number or heading, at most 15 words) that shows why the page belongs to the section.
- Unless told otherwise, also group the pages of every section into the individual documents it contains ("partitions"): one partition per separate document, named by what identifies it in the text (form title and number, case number, account, party names).
Reply with JSON only, in this shape:
{"sections": [{"name": "<section name>", "conf": "high", "pages": [{"page_number": 1, "evidence": "<quote from page 1>"}], "partitions": [{"name": "<document or key value>", "pages": [{"page_number": 1, "evidence": "<quote>"}]}]}]}"""


def _clean_pages(pages: Any, valid: set[int]) -> list[int]:
    out = set()
    for p in pages if isinstance(pages, list) else []:
        if isinstance(p, bool):
            continue
        if isinstance(p, int) and p in valid:
            out.add(p)
        elif isinstance(p, str) and p.strip().isdigit() and int(p) in valid:
            out.add(int(p))
        elif isinstance(p, dict) and isinstance(p.get("page_number"), int) and p["page_number"] in valid:
            out.add(p["page_number"])
    return sorted(out)


def _evidence(pages: Any, valid: set[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for p in pages if isinstance(pages, list) else []:
        if isinstance(p, dict) and isinstance(p.get("page_number"), int) and p["page_number"] in valid \
                and isinstance(p.get("evidence"), str) and p["evidence"].strip():
            out[p["page_number"]] = p["evidence"].strip()
    return out


def _conf(v: Any) -> str:
    return "high" if isinstance(v, str) and v.strip().lower() == "high" else "low"


def _parse(data: Any, names: dict[str, str], valid: set[int]) -> dict[str, dict]:
    """Model reply -> {section name: {"pages", "conf", "partitions": {name: {"pages","conf"}}}}."""
    out: dict[str, dict] = {}
    sections = data.get("sections") if isinstance(data, dict) else data if isinstance(data, list) else None
    if isinstance(data, dict) and sections is None and isinstance(data.get("splits"), list):
        sections = data["splits"]
    for s in sections or []:
        if not isinstance(s, dict):
            continue
        name = names.get(str(s.get("name", "")).strip().lower())
        if name is None:
            continue
        entry = out.setdefault(name, {"pages": [], "conf": "high", "partitions": {}})
        pages = _clean_pages(s.get("pages"), valid)
        entry.setdefault("evidence", {}).update(_evidence(s.get("pages"), valid))
        entry["pages"] = sorted(set(entry["pages"]) | set(pages))
        if pages and _conf(s.get("conf")) == "low":
            entry["conf"] = "low"
        for part in s.get("partitions") or []:
            if not isinstance(part, dict) or part.get("name") in (None, ""):
                continue
            pname = str(part["name"]).strip()
            pp = _clean_pages(part.get("pages"), valid)
            if not pp:
                continue
            pe = entry["partitions"].setdefault(pname, {"pages": [], "conf": "high"})
            pe["pages"] = sorted(set(pe["pages"]) | set(pp))
            pe.setdefault("evidence", {}).update(_evidence(part.get("pages"), valid))
            if _conf(part.get("conf")) == "low":
                pe["conf"] = "low"
    return out


def _merge(total: dict[str, dict], part: dict[str, dict]) -> None:
    for name, e in part.items():
        t = total.setdefault(name, {"pages": [], "conf": "high", "partitions": {}})
        t["pages"] = sorted(set(t["pages"]) | set(e["pages"]))
        t.setdefault("evidence", {}).update(e.get("evidence") or {})
        if e["conf"] == "low":
            t["conf"] = "low"
        for pname, pe in e["partitions"].items():
            tp = t["partitions"].setdefault(pname, {"pages": [], "conf": "high"})
            tp["pages"] = sorted(set(tp["pages"]) | set(pe["pages"]))
            tp.setdefault("evidence", {}).update(pe.get("evidence") or {})
            if pe["conf"] == "low":
                tp["conf"] = "low"


def _describe(categories: list[dict], rules: str) -> str:
    lines = ["Sections:"]
    for c in categories:
        line = f"- name: {json.dumps(c['name'])}; description: {c['description']}"
        if c.get("partition_key"):
            line += f"; partition key: {c['partition_key']}"
        lines.append(line)
    lines += ["", "Rules:", rules]
    return "\n".join(lines)


def _enforce(assign: dict[str, dict], categories: list[dict], settings: dict, deep: bool) -> set[str]:
    """Deterministic clean-up. Returns the names of sections whose result was changed by a rule."""
    touched: set[str] = set()
    keyed = {c["name"] for c in categories if c.get("partition_key")}
    auto = bool(settings.get("auto_partition", True)) and deep
    for name, e in assign.items():
        if name not in keyed and not auto:
            e["partitions"] = {}
        for pname, pe in list(e["partitions"].items()):
            extra = set(pe["pages"]) - set(e["pages"])
            if extra:  # a partition page must be a page of its section
                e["pages"] = sorted(set(e["pages"]) | extra)
                e["conf"] = "low"
                touched.add(name)
    if settings.get("allow_page_overlap", True) is False:
        order = [c["name"] for c in categories]
        owner: dict[int, str] = {}
        said = {n: e["conf"] for n, e in assign.items()}  # the model's own confidence, before any rule
        for name in order:
            for p in assign.get(name, {}).get("pages", []):
                cur = owner.get(p)
                if cur is None:
                    owner[p] = name
                else:  # keep the page with the confident section, else with the first listed one
                    keep = cur if not (said[cur] == "low" and said[name] == "high") else name
                    lose = name if keep == cur else cur
                    owner[p] = keep
                    assign[lose]["pages"] = [x for x in assign[lose]["pages"] if x != p]
                    for pe in assign[lose]["partitions"].values():
                        pe["pages"] = [x for x in pe["pages"] if x != p]
                    assign[keep]["conf"] = "low"
                    touched.update((keep, lose))
        for e in assign.values():  # a page may sit in one partition only
            seen: set[int] = set()
            for pe in e["partitions"].values():
                pe["pages"] = [x for x in pe["pages"] if x not in seen]
                seen.update(pe["pages"])
    for e in assign.values():
        e["partitions"] = {k: v for k, v in e["partitions"].items() if v["pages"]}
    return touched


def _result(assign: dict[str, dict], categories: list[dict], deep: bool, settings: dict,
            keep_empty: bool = False) -> dict:
    splits = []
    mapping: dict[str, list[int]] = {}
    auto = bool(settings.get("auto_partition", True)) and deep
    for c in categories:
        e = assign.get(c["name"], {"pages": [], "conf": "low", "partitions": {}})
        partitions: Optional[list] = None
        if c.get("partition_key") or (auto and e["partitions"]):
            partitions = [{"name": k, "pages": v["pages"], "conf": v["conf"]}
                          for k, v in sorted(e["partitions"].items(), key=lambda kv: (kv[1]["pages"][0], kv[0]))]
        mapping[c["name"]] = e["pages"]
        if not e["pages"] and not keep_empty:
            continue  # Reducto leaves out sections that do not occur
        splits.append({"name": c["name"], "pages": e["pages"],
                       "conf": e["conf"] if e["pages"] else "low", "partitions": partitions})
    return {"section_mapping": mapping if keep_empty else None, "splits": splits}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"<[^>]+>", " ", text or "").lower()))


def _deep_result(assign: dict[str, dict], categories: list[dict], settings: dict, page_text: dict[int, str]) -> dict:
    """Reducto's DeepSplitResult: pages are {page_number, evidence, confidence}; no section_mapping."""
    norm = {n: _norm(t) for n, t in page_text.items()}

    def first_line(n: int) -> str:
        for line in (page_text.get(n) or "").splitlines():
            line = re.sub(r"<[^>]+>", " ", line).strip()
            if len(line) > 3:
                return line[:120]
        return ""

    def page_list(pages: list[int], evidence: dict, fallback: dict, conf: str) -> list[dict]:
        out = []
        for n in pages:
            quote = evidence.get(n) or evidence.get(str(n)) or fallback.get(n) or ""
            found = bool(quote) and _norm(quote) != "" and _norm(quote) in norm.get(n, "")
            if not quote:
                quote = first_line(n)  # the model gave no quote: show where the page starts, marked low
            out.append({"page_number": n, "evidence": quote, "confidence": "high" if found and conf == "high" else "low"})
        return out

    splits = []
    auto = bool(settings.get("auto_partition", True))
    for c in categories:
        e = assign.get(c["name"])
        if not e or not e["pages"]:
            continue
        ev = e.get("evidence") or {}
        partitions = None
        if c.get("partition_key") or (auto and e["partitions"]):
            partitions = [{"name": k, "pages": page_list(v["pages"], v.get("evidence") or {}, ev, v["conf"])}
                          for k, v in sorted(e["partitions"].items(), key=lambda kv: (kv[1]["pages"][0], kv[0]))]
        splits.append({"name": c["name"], "pages": page_list(e["pages"], ev, {}, e["conf"]), "partitions": partitions})
    return {"splits": splits}


def run(document: Any, config: dict, *, llm: LLM, options: Optional[dict] = None) -> dict:
    opts = {**DEFAULTS, **(options or {})}
    config = config or {}
    raw = config.get("split_description")
    if not isinstance(raw, list) or not raw:
        raise TaskError(400, "split_description must be a non-empty list of {name, description}")
    categories: list[dict] = []
    for c in raw:
        if not isinstance(c, dict) or not str(c.get("name", "")).strip() or "description" not in c:
            raise TaskError(400, "each split_description entry needs a name and a description")
        categories.append({"name": str(c["name"]), "description": str(c["description"]),
                           "partition_key": c.get("partition_key") or None})
    if len({c["name"].strip().lower() for c in categories}) != len(categories):
        raise TaskError(400, "split_description names must be unique")
    rules = config.get("split_rules") or DEFAULT_RULES
    settings = config.get("settings") or {}
    cutoff = settings.get("table_cutoff", "truncate")
    if cutoff not in ("truncate", "preserve"):
        raise TaskError(400, "settings.table_cutoff must be 'truncate' or 'preserve'")
    deep = bool(settings.get("deep_split"))

    view = DocView(document)
    pages = view.pages
    valid = {p.number for p in pages}
    names = {c["name"].strip().lower(): c["name"] for c in categories}
    truncate = cutoff == "truncate"
    budget = opts["truncate_chars"] if truncate else opts["preserve_chars"]
    header = _describe(categories, rules)
    results: list[LLMResult] = []

    def text(window: list[PageRef]) -> str:
        return render_pages(window, labels=False, truncate_tables=truncate, max_chars_per_page=budget)

    def ask(prompt: str) -> Any:
        try:
            data, res = ask_json(llm, DEEP_SYSTEM if deep else SYSTEM, prompt, max_tokens=opts["max_tokens"])
        except LLMError as e:
            raise TaskError(502, f"model call failed: {e}") from e
        results.extend(res)
        return data

    assign: dict[str, dict] = {}
    size = max(int(opts["window_pages"]), 1)
    windows = [pages[i:i + size] for i in range(0, len(pages), size)]
    failed_windows = 0
    for wi, window in enumerate(windows):
        prompt = [header, ""]
        if len(windows) > 1:
            prompt.append(f"The document has {len(pages)} pages; this is part {wi + 1} of {len(windows)} "
                          f"(pages {window[0].number}-{window[-1].number}). Assign only these pages.")
            if assign:
                prev = {n: {"last_pages": e["pages"][-2:], "partitions": list(e["partitions"])[-3:]}
                        for n, e in assign.items() if e["pages"]}
                prompt.append("Assignments so far (a section may continue from the previous part): "
                              + json.dumps(prev, ensure_ascii=False))
            prompt.append("")
        prompt += ["Pages:", text(window), "", "Return the JSON now."]
        data = ask("\n".join(prompt))
        if data is None:
            failed_windows += 1
            continue
        _merge(assign, _parse(data, names, {p.number for p in window}))

    touched = _enforce(assign, categories, settings, deep)

    if deep and pages:
        # deterministic flags the verifier is asked to look at
        flags = []
        assigned = {p for e in assign.values() for p in e["pages"]}
        missing = sorted(valid - assigned)
        if missing:
            flags.append(f"pages assigned to no section: {missing}")
        for n, e in assign.items():
            if e["pages"] and e["pages"] != list(range(e["pages"][0], e["pages"][-1] + 1)):
                flags.append(f"section {n!r} has gaps: {e['pages']}")
        proposed = _result(assign, categories, deep, settings)["splits"]
        for sp in proposed:  # show the first pass with its evidence so the verifier returns the same shape
            ev = assign[sp["name"]].get("evidence") or {}
            sp["pages"] = [{"page_number": n, "evidence": ev.get(n, "")} for n in sp["pages"]]
        verify = [header, "", "A first pass proposed this split:", json.dumps(proposed, ensure_ascii=False), ""]
        if flags:
            verify += ["Automatic checks flagged:"] + [f"- {f}" for f in flags] + [""]
        verify += ["Check every page against the section definitions and the rules. Return the corrected, "
                   "complete split in the same JSON shape. Keep what is right; change only what the page text "
                   "contradicts. Leaving a page unassigned or a section with gaps is fine when the text supports it.",
                   "", "Pages:", text(pages if len(pages) <= size else pages[:size]), "", "Return the JSON now."]
        data = ask("\n".join(verify))
        if data is not None:
            checked = _parse(data, names, valid)
            scope = valid if len(pages) <= size else {p.number for p in pages[:size]}
            for c in categories:
                n = c["name"]
                old = assign.get(n, {"pages": [], "conf": "low", "partitions": {}})
                new = checked.get(n, {"pages": [], "conf": "low", "partitions": {}})
                # outside the verified window keep the first pass
                new["pages"] = sorted(set(new["pages"]) | {p for p in old["pages"] if p not in scope})
                new["evidence"] = {**(old.get("evidence") or {}), **(new.get("evidence") or {})}
                for pn, pe in old["partitions"].items():
                    keep = [p for p in pe["pages"] if p not in scope]
                    if keep:
                        npe = new["partitions"].setdefault(pn, {"pages": [], "conf": pe["conf"],
                                                                "evidence": dict(pe.get("evidence") or {})})
                        npe["pages"] = sorted(set(npe["pages"]) | set(keep))
                changed = new["pages"] != old["pages"] or {k: v["pages"] for k, v in new["partitions"].items()} != {
                    k: v["pages"] for k, v in old["partitions"].items()}
                if changed or old["conf"] == "low":
                    new["conf"] = "low"  # the two passes disagree: report it, do not pick silently
                assign[n] = new
            touched |= _enforce(assign, categories, settings, deep)
        else:
            for e in assign.values():
                e["conf"] = "low"

    if failed_windows:
        for e in assign.values():
            e["conf"] = "low"
    if deep:
        result = _deep_result(assign, categories, settings,
                              {p.number: "\n".join(r.region.content for r in p.regions) for p in pages})
    else:
        result = _result(assign, categories, deep, settings, keep_empty=bool(opts.get("keep_empty_sections")))
    view.docs[0].usage.append({"task": "split", **sum_usage(results)})
    return {"response_type": "split", "result": result, "usage": {"num_pages": len(pages), "credits": None}}
