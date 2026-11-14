"""Stage 5, Verify + repair.

Two parts.

1. Robustness (used by Read while reading): ``detect_problems`` finds empty output, refusals,
   truncation, repetition loops and dropped text; ``RetryPolicy`` turns problems into actions
   (retry, trim the loop, fall back to the next reader or to the text layer);
   ``read_with_retry`` runs one read under a policy. Current Claude models reject non-default
   sampling, so a "temperature schedule" is a plain re-ask.

2. The Verify stage (after Read): records evidence and confidence per region, then repairs
   near-miss characters by token fusion (``reading/fusion.py``). A token changes only where a
   trusted text layer, or a second read plus the OCR words, agree against the model; the
   original stays in ``Region.candidates`` and every edit is listed in ``Region.meta``.
   Tokens disputed only by a second read plus OCR words are flagged
   (``Region.meta['disputed_tokens']``, confidence low), not replaced, unless ``fusion_second_read``.
   Options: ``fusion`` (off by default so the baseline profiles stay byte-identical to the raw
   model output; on in the ``quality`` and ``cheap`` profiles), ``fusion_numbers`` (default off: digits are never changed),
   ``high_at``, ``agree_at``.

3. Checks (``pipeline/checks/``), run before part 2 and off by default: table grid repair,
   completeness, printed totals, confidence flags. Option ``checks`` (or ``READANYDOC_VERIFY_CHECKS``),
   e.g. ``grid=norm+draft;complete=text+scan;numbers=1;flags=1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..model import Candidate, Confidence, Document, Evidence, Page
from ..readers.base import ImageInput, ReadContext, Reader, ReaderError, ReadResult
from ..textnorm import agreement, containment, norm_tokens, plain_text
from .base import PipelineContext, Stage, register_stage
from .common import search_bbox, words_in
from .reading.evidence import PageEvidence, all_evidence, read_tokens
from .reading.fusion import apply_edits, majority_edits, propose_edits
from .reading.robust import Problem, detect_problems, find_repetition, trim_repetition

__all__ = ["Problem", "detect_problems", "find_repetition", "trim_repetition", "RetryAction",
           "RetryPolicy", "Attempt", "read_with_retry", "Verify"]


@dataclass
class RetryAction:
    kind: str  # "retry" | "trim" | "fallback" | "accept" | "give_up"
    reason: str = ""


@dataclass
class RetryPolicy:
    """What to do with a problematic read. Deterministic; unit-tested with fake readers."""

    max_attempts: int = 3  # reads of the same reader, including the first
    retry_on: tuple[str, ...] = ("empty", "error", "truncated", "repetition", "refusal")
    trim_repetition: bool = True  # after the last attempt keep the text before the loop
    use_fallback: bool = True

    def next_action(self, attempt: int, problems: list[Problem], same_as_last: bool = False) -> RetryAction:
        fatal = [p for p in problems if p.fatal]
        if not fatal:
            return RetryAction("accept")
        kinds = {p.kind for p in fatal}
        if "refusal" in kinds and attempt >= 2:
            return RetryAction("fallback" if self.use_fallback else "give_up", "refused twice")
        if attempt < self.max_attempts and not same_as_last and kinds & set(self.retry_on):
            return RetryAction("retry", ", ".join(sorted(kinds)))
        if kinds == {"repetition"} and self.trim_repetition:
            return RetryAction("trim", "repetition loop cut after its first copy")
        if kinds <= {"repetition", "truncated"} and self.trim_repetition:
            return RetryAction("trim", "truncated output kept (loop cut)")
        return RetryAction("fallback" if self.use_fallback else "give_up", ", ".join(sorted(kinds)))


@dataclass
class Attempt:
    reader: str
    problems: list[Problem] = field(default_factory=list)
    action: str = "accept"
    reason: str = ""


def read_with_retry(reader: Reader, image: ImageInput, hint=None, context: Optional[ReadContext] = None,
                    policy: Optional[RetryPolicy] = None,
                    fallback: Optional[Callable[[], Optional[ReadResult]]] = None,
                    check: Optional[Callable[[ReadResult], list[Problem]]] = None,
                    ) -> tuple[Optional[ReadResult], list[Attempt]]:
    """Read once, look for problems, follow the policy. Returns (result or None, attempt log).
    ``fallback`` supplies another read (next tier, text layer). ``check`` overrides detection."""
    policy = policy or RetryPolicy()
    check = check or (lambda r: detect_problems(r.text, r.stop_reason, r.refused, r.truncated))
    log: list[Attempt] = []
    last_text: Optional[str] = None
    result: Optional[ReadResult] = None
    attempt = 0
    while True:
        attempt += 1
        ctx = context.model_copy(update={"extra": {**context.extra, "attempt": attempt}}) if context else None
        try:
            result = reader.read(image, hint, ctx)
            problems = check(result)
        except ReaderError as e:
            result, problems = None, [Problem("error", str(e)[:200])]
        same = result is not None and result.text == last_text
        action = policy.next_action(attempt, problems, same_as_last=same)
        log.append(Attempt(reader.name, problems, action.kind, action.reason))
        if action.kind == "accept":
            return result, log
        if action.kind == "retry":
            last_text = result.text if result is not None else None
            continue
        if action.kind == "trim" and result is not None:
            trimmed = result.model_copy(update={"text": trim_repetition(result.text), "truncated": True})
            return trimmed, log
        if action.kind == "fallback" and fallback is not None:
            fb = fallback()
            if fb is not None and fb.text.strip():
                log.append(Attempt("fallback", [], "accept", "fallback read used"))
                return fb, log
        return None, log


def other_reads(page: Page, strong_only: bool = False) -> list[str]:
    """Full-page texts of the reads that were not selected. Unless ``strong_only``, the fast-tier
    reads and the layout pipeline's own text are included too."""
    alts = [r["content"] for r in page.properties.get("reads", [])
            if not r.get("selected") and r.get("content") and (not strong_only or r.get("tier") == "strong")]
    if not strong_only:
        layout_text = "\n\n".join(c.content for lr in page.layout_regions for c in lr.candidates[:1])
        if layout_text.strip():
            alts.append(layout_text)
    return alts


@register_stage("verify")
class Verify(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        high_at = float(self.opt("high_at", 0.85))
        agree_at = float(self.opt("agree_at", 0.9))
        from . import checks as _checks
        spec = _checks.spec_from(self.opt("checks"))  # off unless switched on
        if spec:
            ground_options = dict(ctx.config.stage("ground").options)
            workers = int(self.opt("check_workers", 4))
            if workers > 1 and len(doc.pages) > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=workers) as ex:  # pages are independent
                    list(ex.map(lambda p: _checks.run_page(doc, p, spec, ctx.config.reader, ground_options), doc.pages))
            else:
                for page in doc.pages:
                    _checks.run_page(doc, page, spec, ctx.config.reader, ground_options)
        for page in doc.pages:
            if self.opt("fusion", False):
                self._fuse(doc, page)
            if self.opt("majority", False):
                self._majority(doc, page)
            page_tokens = {src: norm_tokens(" ".join(w.text for w in ws)) for src, ws in page.words.items()}
            for region in page.regions:
                tokens = norm_tokens(region.content)
                if not tokens:
                    continue
                scores = []
                box = search_bbox(region)
                for src, words in page.words.items():
                    if src == "pdf" and not page.text_layer_usable:
                        continue  # a text layer that failed triage is not evidence
                    if box is not None:
                        local = norm_tokens(" ".join(words[i].text for i in words_in(words, box)))
                        score, note = containment(tokens, local), "words inside the region box"
                    else:
                        score, note = containment(tokens, page_tokens[src]), "page-level bag (no box yet)"
                    region.evidence.append(Evidence(
                        source=src, verdict="agree" if score >= agree_at else "disagree",
                        score=round(score, 4), note=note))
                    scores.append(score)
                for cand in region.candidates:
                    if cand.source == region.source or not cand.source.startswith("layout:"):
                        continue
                    score = agreement(tokens, norm_tokens(cand.content))
                    region.evidence.append(Evidence(
                        source=cand.source, verdict="agree" if score >= agree_at else "disagree",
                        score=round(score, 4), note="second model read"))
                    scores.append(score)
                if scores:
                    region.confidence = Confidence.of(max(scores), high_at)
                if (region.meta.get("disputed_tokens") or region.meta.get("table_disputed")
                        or region.meta.get("flagged")) and region.confidence.label == "high":
                    region.confidence = Confidence(score=region.confidence.score, label="low")
        return doc

    def _majority(self, doc: Document, page: Page) -> None:
        """Expert arbiter on pages that have an expert read (handwriting): a token changes only
        when the other page read AND the expert read agree against it. Crop-read tables are left out."""
        reads = page.properties.get("reads", [])
        expert = [r["content"] for r in reads if r.get("tier") == "expert" and r.get("content")]
        peers = [r["content"] for r in reads if not r.get("selected") and r.get("tier") != "expert" and r.get("content")]
        if not expert or not peers:
            return
        regions = [r for r in page.regions if r.content.strip() and not r.meta.get("from_layout_only")
                   and not r.meta.get("table_selected") and not r.meta.get("cell_edits")]
        if not regions:
            return
        sep = "\n\n"
        offsets, pos = [], 0
        for r in regions:
            offsets.append(pos)
            pos += len(r.content) + len(sep)
        edits = majority_edits(sep.join(r.content for r in regions), [peers[0], expert[0]], min_agree=2)
        n = 0
        for k, r in enumerate(regions):
            lo, hi = offsets[k], offsets[k] + len(r.content)
            mine = [e for e in edits if lo <= e.start and e.end <= hi]
            if not mine:
                continue
            for e in mine:
                e.start -= lo
                e.end -= lo
            r.candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                          meta={"before_majority": True}))
            r.content = apply_edits(r.content, mine)
            r.text = plain_text(r.content)
            r.meta.setdefault("majority_edits", []).extend({"old": e.old, "new": e.new} for e in mine)
            n += len(mine)
        if n:
            page.properties["majority_edits"] = n
            doc.note(self.name, f"page {page.index}: {n} token(s) changed by 2-of-3 majority with the expert read")

    def _fuse(self, doc: Document, page: Page) -> None:
        """Token fusion over the page: regions are joined so the context of a token crosses block
        borders exactly as in the reads it is compared with; edits are mapped back per region."""
        evidences = all_evidence(page)
        regions = [r for r in page.regions if r.content.strip() and not r.meta.get("from_layout_only")]
        if not evidences or not regions:
            return
        sep = "\n\n"
        offsets, pos = [], 0
        for r in regions:
            offsets.append(pos)
            pos += len(r.content) + len(sep)
        text = sep.join(r.content for r in regions)
        kw = dict(numbers=bool(self.opt("fusion_numbers", False)), max_dist=int(self.opt("fusion_max_dist", 2)))
        # A strong-tier region is corrected only by the text layer, or by another strong read that
        # both the OCR words and the layout model's own read confirm (three reads against one):
        # a fast model and the OCR words share failure modes and must not outvote the stronger model.
        strong = lambda r: r.meta.get("tier") in ("strong", "expert")
        layout_text = "\n\n".join(c.content for lr in page.layout_regions for c in lr.candidates[:1])
        confirm = [PageEvidence.from_tokens("layout", read_tokens(layout_text))] if layout_text.strip() else []
        by_tier = {True: propose_edits(text, evidences, other_reads(page, strong_only=True), confirm=confirm, **kw)
                   if any(strong(r) for r in regions) else [],
                   False: propose_edits(text, evidences, other_reads(page), **kw)
                   if not all(strong(r) for r in regions) else []}
        if not by_tier[True] and not by_tier[False]:
            return
        n = 0
        for k, r in enumerate(regions):
            lo, hi = offsets[k], offsets[k] + len(r.content)
            mine = [e for e in by_tier[strong(r)] if lo <= e.start and e.end <= hi]
            if not mine:
                continue
            for e in mine:
                e.start -= lo
                e.end -= lo
            # Weak evidence (a second read plus OCR words) only flags the token; it substitutes
            # only with ``fusion_second_read``. An exact text layer substitutes: it is what is printed.
            weak = [e for e in mine if not e.exact]
            if weak and not self.opt("fusion_second_read", False):
                r.meta.setdefault("disputed_tokens", []).extend(
                    {"model": e.old, "other": e.new, "reason": e.reason} for e in weak)
                for e in weak:
                    r.evidence.append(Evidence(source=e.reason, verdict="disagree", text=e.new,
                                               note=f"token {e.old!r} disputed (other reads: {e.new!r}); kept"))
                mine = [e for e in mine if e.exact]
                if not mine:
                    continue
            if not any(c.meta.get("before_fusion") for c in r.candidates):
                r.candidates.append(Candidate(source=r.source or "reader", content=r.content,
                                              meta={"before_fusion": True}))
            r.content = apply_edits(r.content, mine)
            r.text = plain_text(r.content)
            r.meta.setdefault("fusion_edits", []).extend(
                {"old": e.old, "new": e.new, "reason": e.reason} for e in mine)
            for e in mine:
                r.evidence.append(Evidence(source=e.reason, verdict="disagree", text=e.new,
                                           note=f"token {e.old!r} replaced by {e.new!r}"))
            n += len(mine)
        page.properties["fusion_edits"] = n
        doc.note(self.name, f"page {page.index}: {n} token(s) repaired by evidence")
