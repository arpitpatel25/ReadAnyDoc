"""Classify: categories with criteria in, best category out (see tasks/__init__.py for the contract).

One model call returns yes/no for every criterion of every category. A category's confidence is the
fraction of its criteria that matched, exactly as Reducto documents it; the best category wins
(ties: the model's own pick, then schema order). ``model: "accurate"`` additionally asks for a short
verbatim quote per matched criterion and checks deterministically that the quote is in the pages; a
"yes" whose quote cannot be found is reported as low.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..textnorm import norm_tokens
from .common import DocView, TaskError, page_images, render_pages, select_pages, sum_usage
from .llm import LLM, LLMError, ask_json

DEFAULT_PAGES = 5
MAX_PAGES = 10
DEFAULTS: dict[str, Any] = {"max_chars_per_page": 20000, "max_tokens": 4000}  # whole pages: criteria often hinge on a table mid-page

SYSTEM = """You classify a document. You are given categories, each with numbered criteria, and the text of the document's first pages.
For every criterion of every category decide whether the document meets it, judging only from the text given.
Reply with JSON only, in this shape (one entry per category, one boolean per criterion, in the given order):
{"categories": [{"category": "<name>", "criteria": [true, false]}], "best": "<name of the category that fits best>"}"""

SYSTEM_ACCURATE = """You classify a document. You are given categories, each with numbered criteria, and the text of the document's first pages.
For every criterion of every category decide whether the document meets it, judging only from the text given. For each criterion that is met, quote a short passage (3 to 12 words, copied exactly from the pages) that shows it.
Reply with JSON only, in this shape (one entry per category, one entry per criterion, in the given order):
{"categories": [{"category": "<name>", "criteria": [{"met": true, "evidence": "<exact quote>"}, {"met": false, "evidence": ""}]}], "best": "<name of the category that fits best>"}"""


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "high", "met")
    if isinstance(v, dict):
        return _truthy(v.get("met", v.get("match", v.get("value", False))))
    return False


def _contains(haystack: list[str], quote: str) -> bool:
    want = norm_tokens(quote)
    if len(want) < 2:
        return False
    n = len(want)
    return any(haystack[i:i + n] == want for i in range(len(haystack) - n + 1))


def run(document: Any, config: dict, *, llm: LLM, options: Optional[dict] = None) -> dict:
    opts = {**DEFAULTS, **(options or {})}
    config = config or {}
    schema = config.get("classification_schema")
    if not isinstance(schema, list) or not schema:
        raise TaskError(400, "classification_schema must be a non-empty list of {category, criteria}")
    categories: list[tuple[str, list[str]]] = []
    for c in schema:
        if not isinstance(c, dict) or not str(c.get("category", "")).strip() or not isinstance(c.get("criteria"), list):
            raise TaskError(400, "each classification_schema entry needs a category and a criteria list")
        categories.append((str(c["category"]), [str(x) for x in c["criteria"]]))
    if len({n for n, _ in categories}) != len(categories):
        raise TaskError(400, "classification_schema categories must be unique")
    mode = config.get("model") or "default"
    if mode not in ("default", "accurate"):
        raise TaskError(400, "model must be 'default' or 'accurate'")
    groups = config.get("category_groups")
    if groups is not None and not (isinstance(groups, dict) and all(isinstance(v, list) for v in groups.values())):
        raise TaskError(400, "category_groups must map a group name to a list of categories")

    view = DocView(document)
    pages = select_pages(view, config.get("page_range"))
    if pages is None:
        pages = view.pages[:DEFAULT_PAGES]
    if len(pages) > MAX_PAGES:
        raise TaskError(400, f"page_range selects {len(pages)} pages; classify accepts at most {MAX_PAGES}")

    lines = ["Categories:"]
    for name, criteria in categories:
        lines.append(f"- category: {json.dumps(name)}")
        for i, crit in enumerate(criteria, 1):
            lines.append(f"    {i}. {crit}")
    if config.get("document_metadata"):
        lines += ["", "Document metadata (supplied by the caller):", str(config["document_metadata"])]
    page_text = render_pages(pages, labels=False, max_chars_per_page=opts["max_chars_per_page"])
    lines += ["", "Pages:", page_text, "", "Return the JSON now."]
    has_text = any(ref.region.content.strip() for p in pages for ref in p.regions)
    images = page_images(pages) if (not has_text and llm.supports_images) else None
    accurate = mode == "accurate"
    try:
        data, results = ask_json(llm, SYSTEM_ACCURATE if accurate else SYSTEM, "\n".join(lines),
                                 images=images or None, max_tokens=opts["max_tokens"])
    except LLMError as e:
        raise TaskError(502, f"model call failed: {e}") from e
    if not isinstance(data, dict):
        raise TaskError(502, "the model did not return a usable classification")

    by_name: dict[str, Any] = {}
    for entry in data.get("categories") or []:
        if isinstance(entry, dict) and isinstance(entry.get("category"), str):
            by_name[entry["category"].strip().lower()] = entry.get("criteria")
    haystack = norm_tokens(page_text + " " + str(config.get("document_metadata") or "")) if accurate else []

    confidences = []
    for name, criteria in categories:
        answers = by_name.get(name.strip().lower())
        answers = answers if isinstance(answers, list) else []
        crit_conf = []
        for i, crit in enumerate(criteria):
            a = answers[i] if i < len(answers) else False
            met = _truthy(a)
            if met and accurate:
                quote = a.get("evidence") if isinstance(a, dict) else None
                met = isinstance(quote, str) and _contains(haystack, quote)
            crit_conf.append({"criterion": crit, "confidence": "high" if met else "low"})
        matched = sum(1 for c in crit_conf if c["confidence"] == "high")
        confidences.append({"category": name, "confidence": round(matched / len(criteria), 4) if criteria else 0.0,
                            "criteria_confidence": crit_conf})

    top = max(c["confidence"] for c in confidences)
    tied = [c["category"] for c in confidences if c["confidence"] == top]
    model_best = data.get("best") if isinstance(data.get("best"), str) else None
    lookup = {n.strip().lower(): n for n, _ in categories}
    pick = lookup.get(model_best.strip().lower()) if model_best else None
    best = pick if pick in tied else tied[0]

    out: dict[str, Any] = {
        "response_type": "classify",
        "result": {"category": best},
        "response_confidence": {"categories": confidences},
        "usage": {"num_pages": len(pages), "num_categories": len(categories), "credits": None},
    }
    if groups:
        out["extra_metadata"] = {"grouping": next((g for g, members in groups.items() if best in members), "ungrouped")}
    view.docs[0].usage.append({"task": "classify", **sum_usage(results)})
    return out
