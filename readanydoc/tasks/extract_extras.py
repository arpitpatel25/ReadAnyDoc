"""Optional extract features (all off unless the request asks; none is used by Parse).

``settings.relevance_filter``  pick the pages relevant to the schema with one cheap text-only call, extract
                               from those; required fields still missing are looked for in the other pages.
``instructions.examples``      example outputs shown next to the schema (format only; a value copied from an
                               example that is not in the document is removed).
``warnings``                   one line per required field that was not found (always in the response).
``settings.request_pages``     the model starts from the relevant pages and may ask for more
                               ("page N" / "search X") before it answers.

These fields are not in Reducto's API: a client that does not send them gets exactly the old behaviour, plus
the ``warnings`` list.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import schema as S
from .common import PageRef, TaskError, render_pages
from .llm import LLM, LLMError, LLMResult, ask_json

RELEVANCE_SYSTEM = (
    "You plan a data extraction. You get the fields that must be extracted and a short digest of every page of a "
    "document (headings, the first lines, the header and first row of each table). Pick the pages whose digest "
    "shows that they hold a value for at least one field: the statement, table, section or form that the field "
    "names. Leave out pages that are about other things; fields that stay empty are looked for again in the "
    "pages you leave out, so a lean choice is safe. Two rules override that: if a field is a list of rows and "
    "its table or list continues over several pages, pick every page of it; and if you cannot tell from the "
    "digests where a field is, pick every page that could plausibly hold it. Reply with one JSON object and "
    'nothing else: {"pages": [<page numbers>], "why": "<one short sentence>"}.')
DIGEST_CHARS = 500
_ROW = re.compile(r"<tr\b.*?</tr\s*>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------- (a) relevance
def schema_fields(schema: Any, path: str = "", out: Optional[list[str]] = None, depth: int = 0) -> list[str]:
    """Flat list of 'path (type): description' lines for the prompt."""
    out = [] if out is None else out
    if not isinstance(schema, dict) or depth > 6:
        return out
    t = schema.get("type")
    t = next((x for x in t if x != "null"), None) if isinstance(t, list) else t
    desc = " ".join(str(schema.get("description") or "").split())[:200]
    if t == "object" or "properties" in schema:
        if path and desc:
            out.append(f"{path} (object): {desc}")
        for k, v in (schema.get("properties") or {}).items():
            schema_fields(v, f"{path}.{k}" if path else k, out, depth + 1)
    elif t == "array":
        out.append(f"{path}[] (list)" + (f": {desc}" if desc else ""))
        schema_fields(schema.get("items") or {}, f"{path}[]", out, depth + 1)
    else:
        out.append(f"{path} ({t or 'value'})" + (f": {desc}" if desc else ""))
    return out


def page_digest(page: PageRef, max_chars: int = DIGEST_CHARS) -> str:
    """What a page is about, in a few hundred characters: headings, first lines, table headers + first row."""
    heads, body, tables = [], [], []
    for ref in page.regions:
        r = ref.region
        kind = r.type.value
        c = r.content.strip()
        if not c:
            continue
        if kind in ("Title", "Section Header"):
            heads.append(" ".join(c.lstrip("# ").split())[:120])
        elif kind == "Table":
            rows = _ROW.findall(c)[:2]
            txt = " / ".join(" | ".join(x for x in (" ".join(_TAG.sub(" ", cell).split())
                                                    for cell in re.split(r"</t[dh]\s*>", row)) if x) for row in rows)
            n = len(_ROW.findall(c))
            tables.append(f"[table, {n} rows] {txt[:260]}")
        elif kind not in ("Header", "Footer", "Page Number"):
            body.append(" ".join(c.split()))
    parts = []
    if heads:
        parts.append("headings: " + "; ".join(heads[:6]))
    parts += tables[:3]
    used = sum(len(p) for p in parts)
    text = " ".join(body)
    if text and used < max_chars:
        parts.append("text: " + text[:max(max_chars - used, 120)])
    return "\n".join(parts) or "(no text)"


def select_pages(pages: list[PageRef], schema: dict, system_prompt: str, llm: LLM, results: list[LLMResult],
                 opts: dict) -> tuple[list[PageRef], dict[str, Any]]:
    """The pages to extract from. Falls back to all pages whenever the answer cannot be trusted."""
    info: dict[str, Any] = {"pages_total": len(pages), "pages_used": [p.number for p in pages], "filtered": False}
    if len(pages) < int(opts.get("relevance_min_pages", 4)):
        info["reason"] = "document is short: every page is used"
        return pages, info
    fields = schema_fields(schema)
    user = ("Fields to extract:\n" + "\n".join(f"- {f}" for f in fields[:200])
            + (f"\n\nInstructions from the user: {system_prompt.strip()[:1500]}" if system_prompt.strip() else "")
            + "\n\nPages:\n" + "\n\n".join(f"=== Page {p.number} ===\n{page_digest(p)}" for p in pages))
    try:
        data, res = ask_json(llm, RELEVANCE_SYSTEM, user, max_tokens=int(opts.get("relevance_max_tokens", 2000)),
                             retries=0)
    except Exception as e:  # the step is an optimisation: any failure means "use every page"
        info["reason"] = f"relevance call failed ({type(e).__name__}); every page is used"
        return pages, info
    results.extend(res)
    numbers = {p.number for p in pages}
    picked = sorted({int(n) for n in (data or {}).get("pages") or [] if isinstance(n, (int, float)) and int(n) in numbers}) \
        if isinstance(data, dict) else []
    if not picked:
        info["reason"] = "no usable answer from the relevance step; every page is used"
        return pages, info
    margin = int(opts.get("relevance_margin", 0))
    keep = set(picked)
    for n in picked:
        keep |= {m for m in range(n - margin, n + margin + 1) if m in numbers}
    chosen = [p for p in pages if p.number in keep]
    info.update(pages_used=[p.number for p in chosen], filtered=len(chosen) < len(pages),
                reason=str((data or {}).get("why") or "")[:300])
    return chosen, info


def missing_fields(value: Any, schema: Any) -> list[str]:
    """Top-level fields that are still empty after extracting from the chosen pages: required or not, they
    get one more look in the pages that were left out (so the filter cannot lose a value that is there)."""
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return []
    return [k for k in (schema.get("properties") or {}) if _empty(value.get(k))]


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {} or (isinstance(v, dict) and all(_empty(x) for x in v.values()))


# ---------------------------------------------------------------- (b) examples
def check_examples(examples: Any, schema: dict) -> tuple[list[Any], list[str]]:
    """Validated example outputs and warnings about the ones that do not fit the schema."""
    if examples is None:
        return [], []
    if not isinstance(examples, list):
        raise TaskError(400, "instructions.examples must be a list of example outputs")
    if len(examples) > 5:
        raise TaskError(400, "instructions.examples: at most 5 examples")
    out, warnings = [], []
    for i, ex in enumerate(examples):
        if isinstance(ex, dict) and set(ex) <= {"output", "input", "note"} and "output" in ex:
            ex = ex["output"]
        if isinstance(ex, str):
            try:
                ex = json.loads(ex)
            except json.JSONDecodeError as e:
                raise TaskError(400, f"instructions.examples[{i}] is not valid JSON ({e})") from e
        errs = S.find_errors(ex, schema)
        if errs:
            p, msg = errs[0]
            warnings.append(f"instructions.examples[{i}] does not follow the schema ({S.path_str(p)}: {msg}); "
                            "it was shown to the model as given")
        out.append(ex)
    return out, warnings


def examples_prompt(examples: list[Any]) -> str:
    if not examples:
        return ""
    body = "\n".join(f"Example {i + 1}: {json.dumps(ex, ensure_ascii=False)[:6000]}" for i, ex in enumerate(examples))
    return ("Example outputs from OTHER documents follow. They show the expected format, wording and level of "
            "detail only. Their values are not in this document: never copy a value from an example.\n" + body)


def example_leaves(examples: list[Any]) -> set[str]:
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and len(v.strip()) >= 4:
            out.add(v.strip())
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 1000:
            out.add(repr(v))
    for ex in examples:
        walk(ex)
    return out


def drop_copied_examples(value: Any, schema: Any, examples: list[Any], found_in_document) -> tuple[Any, list[str]]:
    """Null every leaf that equals an example's value and cannot be found in the document."""
    leaves = example_leaves(examples)
    if not leaves:
        return value, []
    warnings: list[str] = []

    def fix(path: tuple, v: Any, s: Any) -> Any:
        key = v.strip() if isinstance(v, str) else repr(v)
        if v is None or isinstance(v, bool) or key not in leaves:
            return v
        if isinstance(s, dict) and s.get("enum"):
            return v  # a choice from a fixed list is not a copied value
        if found_in_document(v):
            return v
        warnings.append(f"{S.path_str(path)}: the value {v!r} equals an example value and is not in the document; "
                        "it was removed")
        return None
    return S.map_leaves(value, schema, fix), warnings


# ---------------------------------------------------------------- (c) warnings
def required_warnings(value: Any, schema: Any, skip: Any = ()) -> list[str]:
    """One line per required field that came back empty; rows of a list are counted together.

    ``skip``: top-level fields whose reply could not be read; they get their own warning, not "not found".
    """
    counts: dict[str, list[int]] = {}

    def walk(v: Any, s: Any, path: str) -> None:
        if not isinstance(s, dict):
            return
        props = s.get("properties")
        if isinstance(props, dict) and isinstance(v, dict):
            for k in s.get("required") or []:
                if k not in props:
                    continue
                p = f"{path}.{k}" if path else k
                c = counts.setdefault(p, [0, 0])
                c[1] += 1
                if _empty(v.get(k)):
                    c[0] += 1
            for k, sub in props.items():
                if not _empty(v.get(k)):
                    walk(v.get(k), sub, f"{path}.{k}" if path else k)
        elif isinstance(v, list) and isinstance(s.get("items"), dict):
            for item in v:
                walk(item, s["items"], path + "[]")
    walk(value, schema, "")
    out = []
    for p, (missing, total) in counts.items():
        if not missing or re.split(r"[.\[]", p, maxsplit=1)[0] in skip:
            continue
        if "[]" in p:
            out.append(f"required field '{p}' was not found in {missing} of {total} items")
        else:
            out.append(f"required field '{p}' was not found in the document")
    return out


# ---------------------------------------------------------------- (d) the model asks for pages
LOOP_RULES = (
    "You do not have the whole document yet. Below are a digest of every page and the full text of some pages. "
    "If the full text you have is enough, answer with the final JSON described above. If you need more "
    "(a definition, a schedule, an exhibit or a section that the text refers to, or a page whose digest suggests "
    "it holds a value), ask for it instead by replying with one JSON object of this exact form and nothing else: "
    '{"need": [{"page": <page number>}, {"search": "<exact words to look for>"}]} (at most 6 requests). '
    "Ask only for what the fields need; do not ask for pages you already have.")


def search_pages(pages: list[PageRef], query: str, limit: int = 3) -> list[tuple[PageRef, int]]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    if not words:
        return []
    phrase = " ".join(words)
    scored = []
    for p in pages:
        text = " ".join(re.findall(r"\w+", " ".join(ref.region.content for ref in p.regions).lower()))
        n = text.count(phrase) * 10 + sum(text.count(w) for w in words if len(words) > 1 and len(w) > 3)
        if text.count(phrase) or (len(words) > 1 and all(w in text for w in words)):
            scored.append((p, n))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def loop_extract(ex, pages: list[PageRef], start: list[PageRef], opts: dict) -> tuple[Any, dict, dict[str, Any]]:
    """Extraction where the model may ask for more pages. Returns (value, sources, info)."""
    rounds = int(opts.get("max_page_requests", 3))
    budget = int(opts.get("page_request_chars", 160_000))
    have = {p.number for p in start}
    by_number = {p.number: p for p in pages}
    info: dict[str, Any] = {"start_pages": sorted(have), "requests": [], "rounds": 0}
    digest = "\n\n".join(f"=== Page {p.number} (digest) ===\n{page_digest(p, 300)}" for p in pages)
    answer = None
    for step in range(rounds + 1):
        shown = [by_number[n] for n in sorted(have)]
        last = step == rounds or sum(len(ref.region.content) for p in shown for ref in p.regions) > budget \
            or len(have) == len(pages)
        prompt = "\n".join([
            "Page digests of the whole document:", digest, "",
            "Full text of pages " + ", ".join(str(n) for n in sorted(have)) + ":",
            render_pages(shown, labels=ex.track), "",
            "JSON Schema:", json.dumps(ex.prompt_schema, ensure_ascii=False), ""]
            + ([ex.track_text, ""] if ex.track else [])
            + ([ex.extra_text, ""] if ex.extra_text else [])
            + (["You now have everything that can be given. Return the final JSON now."] if last
               else [LOOP_RULES, "", "Return the final JSON, or your requests, now."]))
        try:  # no output schema is enforced here: the reply may be a request for pages
            data, res = ask_json(ex.llm, ex.system, prompt, max_tokens=ex.opts["max_tokens"], retries=1)
        except LLMError as e:
            raise TaskError(502, f"model call failed: {e}") from e
        ex.results.extend(res)
        info["rounds"] = step + 1
        need = data.get("need") if isinstance(data, dict) and set(data) == {"need"} else None
        if need is None or last:
            answer = None if need is not None else data
            break
        added = set()
        for req in need[:6] if isinstance(need, list) else []:
            if not isinstance(req, dict):
                continue
            if isinstance(req.get("page"), (int, float)) and int(req["page"]) in by_number:
                added.add(int(req["page"]))
                info["requests"].append({"page": int(req["page"])})
            elif isinstance(req.get("search"), str) and req["search"].strip():
                hits = [p.number for p, _ in search_pages(pages, req["search"])]
                info["requests"].append({"search": req["search"][:80], "pages": hits})
                added |= set(hits)
        new = added - have
        if not new:  # nothing more to give: the next turn must answer
            rounds = step + 1
        have |= new
    info["pages_used"] = sorted(have)
    from .extract import root_ok
    if answer is not None and not root_ok(answer, ex.strict):
        answer = None  # a fragment or another shape is not an answer
    if answer is None:
        ex.notes.append("page-request loop ended without an answer")
        return S.empty_instance(ex.strict), {}, info
    value, sources, _errors = ex._digest(answer, ex.strict)
    return value, sources, info
