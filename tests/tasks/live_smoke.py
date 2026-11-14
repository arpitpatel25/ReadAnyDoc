"""Live smoke test of the four tasks through `claude -p` (Haiku). NOT collected by pytest.

Run:  uv run python -m tests.tasks.live_smoke [extract split classify edit]
Hard cap: MAX_CALLS model calls per run. Prompts and results are appended to tests/tasks/live_notes.md.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from readanydoc.tasks import classify, edit, extract, split
from readanydoc.tasks.llm import LLM, ClaudeCLILLM, LLMError

MAX_CALLS = 8
NOTES = Path(__file__).with_name("live_notes.md")


class Capped(LLM):
    """Counts calls, refuses past the cap, keeps prompts and replies for the notes."""

    supports_images = True

    def __init__(self, inner: LLM, cap: int):
        self.inner, self.cap, self.log = inner, cap, []
        self.name = inner.name

    def complete(self, system, user, *, images=None, json_schema=None, max_tokens=16000):
        if len(self.log) >= self.cap:
            raise LLMError(f"live call cap of {self.cap} reached")
        t0 = time.time()
        res = self.inner.complete(system, user, images=images, json_schema=json_schema, max_tokens=max_tokens)
        self.log.append({"system": system, "user": user, "reply": res.text, "usage": res.usage,
                         "seconds": round(time.time() - t0, 1)})
        return res


def note(title: str, llm: Capped, start: int, result) -> None:
    lines = [f"\n## {title}\n"]
    for i, c in enumerate(llm.log[start:], 1):
        lines += [f"### call {i} ({c['seconds']} s, usage {json.dumps(c['usage'])})\n",
                  "System prompt:\n", "```", c["system"], "```\n", "User prompt:\n", "```", c["user"], "```\n",
                  "Reply:\n", "```", c["reply"], "```\n"]
    lines += ["### result\n", "```json", json.dumps(result, indent=1, ensure_ascii=False)[:6000], "```\n"]
    with NOTES.open("a") as f:
        f.write("\n".join(lines))


def main(which: list[str]) -> None:
    from tests.tasks.conftest import TABLE_STEM, build_form_pdf
    from tests.tasks.test_split_classify import DESC, packet
    from readanydoc.pipeline import Pipeline
    from readanydoc.profiles import get_profile

    llm = Capped(ClaudeCLILLM("haiku"), MAX_CALLS)
    table_doc = Pipeline(get_profile("cached-haiku")).run(TABLE_STEM)
    if not NOTES.exists():
        NOTES.write_text("# Live smoke notes (Worker E)\n\nModel: claude-haiku-5-5 through `claude -p`. "
                         "Written by tests/tasks/live_smoke.py.\n")
    with NOTES.open("a") as f:
        f.write(f"\n---\n\nRun at {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(which)}\n")

    if "extract" in which:
        schema = {"type": "object", "properties": {
            "title": {"type": "string"},
            "atrium_exhibitors": {"type": "array", "description": "Exhibitors listed under the Atrium heading only",
                                  "items": {"type": "object", "properties": {
                                      "booth": {"type": "string", "pattern": "^\\d+$"},
                                      "company": {"type": "string"},
                                      "website": {"type": "string"}}}},
            "event_date": {"type": "string", "description": "Date of the event, if printed on the page"}}}
        start = len(llm.log)
        out = extract.run(table_doc, {"instructions": {"schema": schema},
                                      "settings": {"citations": {"enabled": True, "parent_block": "bbox_only"}}}, llm=llm)
        note(f"extract with citations on cached table page {TABLE_STEM}", llm, start, out)
        rows = out["result"]["atrium_exhibitors"]
        cited = sum(1 for r in rows for v in r.values() if v["citations"])
        high = sum(1 for r in rows for v in r.values() if v["citations"] and v["citations"][0]["confidence"] == "high")
        leaves = sum(1 for r in rows for v in r.values() if v["value"] is not None)
        print(f"extract: {len(rows)} rows, {leaves} non-null leaves, {cited} cited, {high} high; "
              f"title={out['result']['title']['value']!r}; event_date={out['result']['event_date']}")

    if "split" in which:
        start = len(llm.log)
        out = split.run(packet(), {"split_description": DESC}, llm=llm)
        note("split of a 5-page synthetic statement packet", llm, start, out)
        print("split:", json.dumps(out["result"]["splits"]))

    if "classify" in which:
        cats = [{"category": "exhibitor directory", "criteria": ["lists companies or organisations", "has booth numbers", "has contact names or websites"]},
                {"category": "invoice", "criteria": ["has an invoice number", "lists amounts due"]},
                {"category": "research paper", "criteria": ["has an abstract", "has citations or references"]}]
        start = len(llm.log)
        out = classify.run(table_doc, {"classification_schema": cats,
                                       "category_groups": {"events": ["exhibitor directory"], "finance": ["invoice"]}}, llm=llm)
        note("classify the cached table page", llm, start, out)
        print("classify:", out["result"], out.get("extra_metadata"),
              [(c["category"], c["confidence"]) for c in out["response_confidence"]["categories"]])

    if "edit" in which:
        d = Path(tempfile.mkdtemp(prefix="readanydoc_live_"))
        build_form_pdf(d / "form.pdf")
        start = len(llm.log)
        out = edit.run(None, {"edit_instructions": "The applicant is Priya Sharma, born 12 April 1990. She agrees to the "
                                                   "terms, wants the premium plan and lives in Germany. Leave comments empty.",
                              "edit_options": {"flatten": False}}, llm=llm, source_path=d / "form.pdf",
                       output_path=d / "filled.pdf")
        from tests.tasks.test_edit import field_values
        got = field_values(out["document_url"])
        note("edit of the generated fillable PDF", llm, start, {"filled_fields": got, "response": out})
        print("edit:", got)

    print(f"model calls used: {len(llm.log)} (cap {MAX_CALLS}); "
          f"cost reported: ${sum((c['usage'].get('cost_usd') or 0) for c in llm.log):.4f}")
    with NOTES.open("a") as f:
        f.write(f"\nCalls in this run: {len(llm.log)}; cost reported by the CLI: "
                f"${sum((c['usage'].get('cost_usd') or 0) for c in llm.log):.4f}\n")


if __name__ == "__main__":
    main(sys.argv[1:] or ["extract", "split", "classify", "edit"])
