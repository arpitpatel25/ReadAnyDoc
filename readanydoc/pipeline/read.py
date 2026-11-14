"""Stage 4, Read: get text for every region.

Flow per page
1. Read the page with the configured reader and with every extra reader in ``candidates``
   (page-level reads; ``verify.read_with_retry`` handles empty output, loops and truncation).
2. Cheap profile (``escalate`` option): if the fast read is broken or two fast reads dispute too
   many words, read the page with the strong tier as well.
3. Choose ONE read by evidence (``reading/select.py``): the first read is the default and is
   replaced only when another agrees clearly better with the PDF/OCR words. Reads are never merged.
4. Split the chosen markdown into blocks (never edited) and match each block to a layout region,
   which gives it a type and a box. The model's reading order wins.
5. Furniture by consensus (``furniture_consensus``): a block the layout detector labels
   Header/Footer/Page Number is typed so only when another read left it out as well.
6. Tables (``table_select`` / ``escalate``): each table region is verified against the words in
   its box and against the other reads; a better-supported candidate replaces it, or, in the cheap
   profile, the strong tier re-reads the crop.
Every read is kept in ``Page.properties["reads"]`` and every replaced block in
``Region.candidates``; ``Document.usage`` carries one entry per call with an estimated cost.
"""

from __future__ import annotations

import threading

from typing import Optional

from .. import paths
from ..model import Candidate, Document, Page, Region, RegionType
from ..readers import make_reader
from ..readers.base import ReadContext, Reader, ReadResult
from ..textnorm import containment, norm_tokens, plain_text
from .base import PipelineContext, Stage, register_stage
from .common import pick_word_source, words_in
from .mdblocks import MdBlock, split_markdown
from .reading.escalate import CostModel, EscalationPolicy, cost_summary
from .reading.evidence import page_evidence, read_tokens
from .reading.robust import detect_problems
from .reading.select import select_page
from .reading.tables import (cell_agreement, check_table, choose_table, choose_table_numeric, find_tables,
                             has_table, number_tokens, parse_grid, vote_cells_majority, vote_number_cells)
from .verify import RetryPolicy, read_with_retry

FURNITURE = {RegionType.HEADER, RegionType.FOOTER, RegionType.PAGE_NUMBER}
Match = tuple[Optional[int], float, float]  # (layout index, block-in-layout, layout-in-block)


class PageRead:
    def __init__(self, source: str, result: ReadResult, tier: str = "reader"):
        self.source = source
        self.result = result
        self.text = result.text
        self.tier = tier
        self.problems: list = []
        self.blocks: Optional[list[MdBlock]] = None
        self.matches: list[Match] = []


@register_stage("read")
class Read(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        reader = ctx.reader
        self.ctx_reader_spec = ctx.config.reader
        extra = [self._reader(spec) for spec in self.opt("candidates", []) or []]
        esc_opt = self.opt("escalate") or None
        self._cost = CostModel(**(esc_opt or {}).get("cost", {})) if esc_opt else CostModel()
        policy = EscalationPolicy(**{k: v for k, v in (esc_opt or {}).items()
                                     if k in EscalationPolicy.__dataclass_fields__}) if esc_opt else None
        from . import tables as _tables2  # table reads started with the page read (off by default)
        _tspec = _tables2.spec_from(ctx.config.stage("ground").options.get("tables"))
        from .reading import budget as _budget  # per-page spending cap (off unless a cap is set)
        capped = bool(_budget.configure(doc, self.opt("page_cap_usd"), self.opt("page_cap_tokens")))
        self._ground_opts = dict(ctx.config.stage("ground").options)

        def work(page: Page) -> None:
            ev = page_evidence(page)
            if _tspec.get("prefetch") and not capped:  # with a spending cap nothing is paid before the page read
                from .tables import prefetch as _table_prefetch
                try:
                    _table_prefetch.start(doc, page, _tspec, ctx.config.reader)
                except Exception as e:  # a prefetch must never cost the page
                    doc.note(self.name, f"page {page.index}: table prefetch not started ({str(e)[:120]})", "warning")
            pre = {}  # zoomed strips started together with the page read (off by default)
            pre_ok = True
            if self.opt("zoom_pre_gate") and self.opt("zoom_vote", False):  # gate before the read (off by default)
                from .reading import zoom_gate
                pre_ok, page.properties["zoom_pre_gate"] = zoom_gate.pre_decide(page, int(self.opt("zoom_pre_gate")))
            if self.opt("form_prefetch", False) and not capped:  # form region reads start now (off by default)
                from .reading import form_prefetch as _form_prefetch
                try:
                    _form_prefetch.start(doc, page, self._ground_opts, ctx.config.reader,
                                         float(self.opt("form_prefetch_min_area", _form_prefetch.MIN_AREA)))
                except Exception as e:  # a prefetch must never cost the page
                    doc.note(self.name, f"page {page.index}: form prefetch not started ({str(e)[:120]})", "warning")
            if pre_ok and self.opt("zoom_prefetch", 0) and self.opt("zoom_vote", False) and not capped:
                from .reading import strips as strip_reads
                pre = strip_reads.start(self, doc, page)
                self._prefetched[(id(doc), page.index)] = pre
            first = reader
            route = self.opt("effort_route")  # easy pages at a cheaper effort (off by default)
            if route:
                from .reading import effort_route
                easy, page.properties["effort_route"] = effort_route.decide(
                    page, int(self.opt("effort_route_numbers", 40)), float(self.opt("effort_route_share", 0.12)))
                if easy:
                    spec = str(route) if ":" in str(route) else effort_route.spec_with_effort(self.ctx_reader_spec, str(route))
                    first = self._reader(spec)
                    page.properties["effort_route"]["reader"] = spec
            reads = [r for r in (self._read_page(doc, page, rd, ev, ctx.config.tier) for rd in [first, *extra])
                     if r is not None]
            if policy is not None:
                strong = self._escalate_page(doc, page, reads, policy, esc_opt, ev)
                if strong is not None:
                    reads.insert(0, strong)
            usable = [r for r in reads if not any(p.fatal for p in r.problems)] or \
                     [r for r in reads if r.text.strip()]
            if not usable:
                doc.note(self.name, f"page {page.index}: empty read", "warning")
                page.regions = []
                return
            chosen, scores, why = 0, [], "single read"
            if len(usable) > 1 and self.opt("select", True):
                chosen, scores, why = select_page(
                    [(r.source, r.text) for r in usable], ev, margin=float(self.opt("select_margin", 0.02)),
                    precision_weight=float(self.opt("select_precision_weight", 0.5)))
                if chosen != 0:
                    doc.note(self.name, f"page {page.index}: {why}")
            best = usable[chosen]
            others = [r for r in usable if r is not best]
            if self.opt("form_reread", False):
                best = self._form_reread(doc, page, best, ev, ctx.config.tier) or best
                others = [r for r in usable if r is not best]
            routed = None
            if self.opt("handwriting_route", False):
                routed = self._handwriting_route(doc, page, best, ev, ctx.config.tier)
                if routed is not None:
                    best.tier = "superseded"  # kept as a candidate; Verify must not use it to edit the new read
                    reads.append(routed)
                    best = routed
            gate = self.opt("zoom_gate")  # strips only where they pay (off by default)
            if gate and self.opt("zoom_vote", False) and routed is None:
                from .reading import zoom_gate
                gate_run, page.properties["zoom_gate"] = zoom_gate.decide(
                    str(gate), page, best.text, int(self.opt("zoom_gate_table_numbers", 4)),
                    int(self.opt("zoom_gate_unsupported", 1)))
            else:
                gate_run = True
            if self.opt("zoom_vote", False) and routed is None and gate_run and pre_ok:
                self._zoom_vote(doc, page, best)
            if pre:
                from .reading import strips as strip_reads
                strip_reads.settle(self, doc, page, pre)  # started but not used: still billed
                self._prefetched.pop((id(doc), page.index), None)
            expert_spec = self.opt("expert_page_reader")
            if expert_spec and page.properties.get("handwriting_likely"):
                # arbiter only: never selected, never used alone (Verify takes a 2-of-3 token majority)
                xr = self._read_page(doc, page, self._reader(expert_spec), ev, "expert", escalation="handwriting")
                if xr is not None and not any(p.fatal for p in xr.problems):
                    xr.tier = "expert"
                    reads.append(xr)
            page.properties["reads"] = [
                {"source": r.source, "tier": r.tier, "selected": r is best, "content": r.text,
                 "problems": [f"{p.kind}: {p.detail}" for p in r.problems]} for r in reads]
            page.properties["read_selection"] = {
                "chosen": best.source, "reason": why,
                "scores": [{"source": s.source, "recall": s.recall, "precision": s.precision} for s in scores]}
            others = [r for r in others if r is not best and r.tier != "superseded"]
            layout = list(page.regions)
            if best.result.blocks:
                page.regions = self._from_blocks(best.result, best.source)
            else:
                page.regions = self._map_page(doc, page, layout, best, others)
                if policy is not None and policy.tables and best.tier != "strong":
                    self._escalate_tables(doc, page, layout, best, policy, esc_opt)
                elif self.opt("table_mode") == "crop_numeric":
                    self._crop_tables(doc, page, layout, others)
                elif others and self.opt("table_select", True):
                    if any(lr.type == RegionType.TABLE for lr in layout):
                        self._select_tables(doc, page, layout, best, others)
                    elif self.opt("table_select_no_layout", False):  # off: lost 19 pages, won 4 when judged
                        self._select_tables_no_layout(doc, page, best, others, ev)
            from . import forms  # every forms switch is off by default
            if forms.switches(self):
                forms.apply_page(doc, page, self)
            for i, r in enumerate(page.regions):
                r.order = i
                if not r.meta.get("from_layout_only"):
                    r.meta.setdefault("tier", best.tier)  # Verify only lets same-or-higher tiers correct it
        workers = int(self.opt("page_workers", 1))
        import os
        if os.environ.get("READANYDOC_PAGE_WORKERS"):  # deployment limit on pages read at once
            workers = max(int(os.environ["READANYDOC_PAGE_WORKERS"]), 1)
        if workers > 1 and len(doc.pages) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:  # pages are independent: read them together
                list(ex.map(work, doc.pages))
        else:
            for page in doc.pages:
                work(page)
        if policy is not None or extra:
            doc.meta["cost"] = cost_summary(doc)
        return doc

    # ---- engine v2: zoomed strip reads vote on the characters of a scanned page ----
    def _page_prompt(self) -> Optional[str]:
        name = getattr(self._local, "prompt", None) or self.opt("page_prompt")
        if not name:
            return None
        from ..readers import prompts
        return getattr(prompts, name)

    def _form_reread(self, doc: Document, page: Page, best: PageRead, ev, tier: str) -> Optional[PageRead]:
        """Fill-in forms read better as label/value lines than as one tangled table."""
        from .reading import zoom
        if best.source == "pdf_text_layer" or not zoom.tangled_form(best.text):
            return None
        self._local.prompt = self.opt("form_prompt", "PAGE_PROMPT_V2_FILE")
        try:
            again = self._read_page(doc, page, self._reader(self.opt("form_reader") or self.ctx_reader_spec),
                                    ev, tier, escalation="form")
        finally:
            self._local.prompt = None
        if again is None or any(p.fatal for p in again.problems) or again.source == "pdf_text_layer":
            return None
        page.properties["form_reread"] = True
        return again

    def _handwriting_route(self, doc: Document, page: Page, best: PageRead, ev, tier: str) -> Optional[PageRead]:
        """a handwritten scan is read once by the handwriting reader (a stronger model)
        and that read becomes the page's content. Detection uses run-time signals only
        (``reading/handwriting.py``). The first read is kept when the second one fails, is refused,
        is truncated or comes back nearly empty against a first read that had text."""
        import os

        from .reading import handwriting
        if best.source == "pdf_text_layer":
            return None
        sig = handwriting.detect(
            page, best.text, is_pdf=doc.file_type == "pdf",
            min_illegible=int(self.opt("handwriting_min_illegible", 1)),
            max_confidence=float(self.opt("handwriting_max_ocr_confidence", 0.5)),
            max_agreement=float(self.opt("handwriting_max_ocr_agreement", 0.5)),
            illegible_max_agreement=self.opt("handwriting_illegible_max_agreement"),
            illegible_sure_agreement=self.opt("handwriting_illegible_sure_agreement"),   # off by default
            illegible_min_density=float(self.opt("handwriting_illegible_min_density", 0.03)),
            print_ocr=bool(self.opt("handwriting_print_ocr", False)),                    # off by default
            print_min_confidence=float(self.opt("handwriting_print_min_confidence", handwriting.PRINT_MIN_CONFIDENCE)),
            print_min_words=int(self.opt("handwriting_print_min_words", handwriting.PRINT_MIN_WORDS)))
        info = sig.as_dict()
        page.properties["handwriting_route"] = info
        if not sig.route:
            return None
        spec = os.environ.get("READANYDOC_HANDWRITING_READER") or self.opt("handwriting_reader", "anthropic_api:sonnet")
        from .reading import budget
        if not budget.allow(doc, page, "handwriting", reader=spec):  # spending cap
            info.update(used=False, why="spending cap reached; first read kept")
            return None
        again = self._read_page(doc, page, self._reader(spec), ev, tier, escalation="handwriting")
        if again is None or again.source == "pdf_text_layer" or again.result.refused or again.result.truncated \
                or any(p.fatal for p in again.problems):
            info.update(used=False, why="handwriting read failed; first read kept")
            doc.note(self.name, f"page {page.index}: handwriting read failed; first read kept", "warning")
            return None
        if len(read_tokens(again.text)) < 0.5 * len(read_tokens(best.text)):
            info.update(used=False, why="handwriting read has under half the words of the first read; first read kept")
            doc.note(self.name, f"page {page.index}: handwriting read much shorter; first read kept", "warning")
            again.tier = "superseded"
            return None
        info.update(used=True, reader=again.source)
        doc.note(self.name, f"page {page.index}: handwriting ({sig.reason}); read by {again.source}")
        return again

    def _zoom_vote(self, doc: Document, page: Page, best: PageRead) -> None:
        from ..readers import prompts, sizing
        from .reading import zoom
        if page.properties.get("text_layer_exact") or best.source == "pdf_text_layer":
            return
        if doc.file_type == "pdf" and page.kind != "scanned":
            return  # a born-digital page renders sharp at any size; an embedded logo is not a scan
        if best.text.count("[illegible") >= int(self.opt("zoom_skip_illegible", 3)):
            page.properties["zoom_skipped"] = "handwriting or damaged text"
            return  # where the page read cannot read the writing, zoomed reads agree by chance (judged: more errors)
        src = doc.meta.get("path")
        try:
            if doc.file_type == "pdf":
                z = zoom.source_zoom(src, page.original_page, doc.meta.get("password"))
            else:
                from PIL import Image
                image_path = next(iter(page.renders.values()), None) or src
                with Image.open(image_path) as im:
                    z = 1.0 / max(sizing.native_scale(*im.size), 1e-6)
        except Exception as e:
            doc.note(self.name, f"page {page.index}: zoom check failed ({e})", "warning")
            return
        page.properties["source_zoom"] = round(z, 2)
        if z < float(self.opt("zoom_min", 0.0)):
            return
        cap = float(self.opt("zoom_max", 3.0))  # times the whole-page scale; strips may be upsampled
        from .reading import budget, strips as _strips
        reader = self._reader(_strips.reader_spec(self))
        second = str(self.opt("zoom_second") or "")  # a tighter second pass (off by default)
        parts_a: list[str] = []

        def strips(n: int, tag: str, keep=None) -> Optional[str]:
            from concurrent.futures import ThreadPoolExecutor

            def one(item):
                i, box = item
                if doc.file_type == "pdf":
                    data = sizing.render_pdf_native(src, page.original_page, box=box, crop_zoom=cap,
                                                    password=doc.meta.get("password"))
                else:
                    data = sizing.resize_image_native(image_path, box=box, max_scale=cap)
                return reader.read(data, None, ReadContext(stem=f"{doc.stem}#{tag}{i}", page_index=page.index,
                                                           prompt=prompts.STRIP_PROMPT_FILE))
            fut = self._prefetched.get((id(doc), page.index), {}).pop(tag, None)  # already running
            if fut is not None:
                results = fut.result()
            else:
                from .reading import zoom_regions  # strips only over table regions (off by default)
                cut = zoom_regions.boxes(page, n, self.opt("zoom_regions"), int(self.opt("zoom_strips", 3)))
                if keep is not None:  # only the strips of this set that overlap a disputed strip of the first set
                    cut = [b for b in cut if keep(b)]
                    page.properties["zoom_second"] = {"mode": second, "strips": len(cut), "of": n}
                if not budget.allow(doc, page, "zoom_strip", calls=len(cut), tag=tag):
                    return None
                with ThreadPoolExecutor(max_workers=max(len(cut), 1)) as ex:  # strips are independent: read them together
                    results = list(ex.map(one, enumerate(cut)))
            parts = []
            for res in results:
                doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "zoom_strip",
                                  "reason": tag, **res.usage.model_dump(exclude_none=True)})
                if not res.text.strip() or res.refused:
                    return None
                parts.append(res.text)
            if tag == "za":
                parts_a.extend(parts)
            return "\n\n".join(parts)

        try:
            a = strips(int(self.opt("zoom_strips", 3)), "za")
            if a is None:
                return
            n = zoom.disputes(best.text, a)
            page.properties["zoom_disputes"] = n
            if not n:
                return
            keep = None
            if second:
                from .reading import zoom_regions as _zr, zoom_schedule
                cut_a = _zr.boxes(page, int(self.opt("zoom_strips", 3)), self.opt("zoom_regions"), int(self.opt("zoom_strips", 3)))
                run_b, keep, page.properties["zoom_schedule"] = zoom_schedule.plan(second, best.text, parts_a, cut_a)
                if not run_b:
                    return
            b = strips(int(self.opt("zoom_strips", 3)) + 1, "zb", keep)
            if b is None:
                return
        except Exception as e:
            doc.note(self.name, f"page {page.index}: zoom read failed ({e})", "warning")
            return
        text, changes = zoom.vote(best.text, a, b)
        text, added = zoom.insert_agreed(text, a, b)
        if added:
            page.properties["zoom_insertions"] = [list(x) for x in added]
            changes = changes + [("", x[1]) for x in added]
        if changes:
            best.text = text
            best.result.text = text
            page.properties["zoom_changes"] = [list(c) for c in changes]
            doc.note(self.name, f"page {page.index}: {len(changes)} token(s) corrected by two zoomed reads")

    # ---- reading ----
    _readers: dict[str, Reader] = {}
    _local = threading.local()
    _prefetched: dict = {}  # (id(doc), page index) -> {strip set tag: future}; see reading/strips.py

    def _reader(self, spec: str) -> Reader:
        if spec not in self._readers:
            self._readers[spec] = make_reader(spec)
        return self._readers[spec]

    def _image(self, doc: Document, page: Page, reader: Reader) -> Optional[str]:
        if not reader.needs_image:
            return page.renders.get("150")
        if self.opt("native_size", True) and doc.file_type == "pdf":  # largest size the model keeps
            from ..readers.sizing import render_pdf_native
            out = paths.cache_dir() / "renders" / f"{doc.id}_p{page.original_page}_native.png"
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(render_pdf_native(doc.meta["path"], page.original_page))
            page.renders["native"] = str(out)
            return str(out)
        dpi = int(self.opt("dpi", 150))
        if str(dpi) in page.renders:
            return page.renders[str(dpi)]
        if doc.file_type == "pdf":
            from .intake import render_page
            out = paths.cache_dir() / "renders" / f"{doc.id}_p{page.original_page}_{dpi}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            render_page(doc.meta["path"], page.original_page, dpi).save(out)
            page.renders[str(dpi)] = str(out)
            return str(out)
        return next(iter(page.renders.values()), None)

    def _read_page(self, doc: Document, page: Page, reader: Reader, ev, tier: str,
                   escalation: Optional[str] = None) -> Optional[PageRead]:
        def check(res: ReadResult):
            toks = read_tokens(res.text) if ev is not None else []
            return detect_problems(res.text, res.stop_reason, res.refused, res.truncated,
                                   evidence_recall=ev.recall(toks) if ev is not None else None,
                                   n_evidence=len(ev) if ev is not None else 0)
        def text_layer() -> Optional[ReadResult]:
            """Last resort (as olmOCR does): the lines of an exact PDF text layer, unformatted."""
            if not page.properties.get("text_layer_exact"):
                return None
            lines: dict[int, list[str]] = {}
            for w in page.words.get("pdf", []):
                lines.setdefault(w.line or 0, []).append(w.text)
            text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
            return ReadResult(text=text, format="text", stop_reason="text_layer_fallback") if text.strip() else None
        calls: list[ReadResult] = []  # every attempt, so that retried reads are counted in the usage too

        class _Counting:
            name, needs_image = reader.name, getattr(reader, "needs_image", True)

            def read(self, *a, **k):
                res = reader.read(*a, **k)
                calls.append(res)
                return res
        try:
            image = self._image(doc, page, reader)
            result, log = read_with_retry(
                _Counting(), image, None, ReadContext(stem=doc.stem, page_index=page.index, prompt=self._page_prompt()),
                policy=RetryPolicy(max_attempts=int(self.opt("max_attempts", 3))) if self.opt("robust", True)
                else RetryPolicy(max_attempts=1, trim_repetition=False, use_fallback=False),
                check=check, fallback=text_layer if self.opt("robust", True) else None)
        except Exception as e:
            doc.note(self.name, f"page {page.index}: reader {reader.name} failed ({e})", "error")
            return None
        if result is not None and result.text:
            from .reading.robust import strip_restart
            cleaned = strip_restart(result.text)
            if cleaned != result.text:
                doc.note(self.name, f"page {page.index}: dropped an abandoned first attempt from the read", "warning")
                result.text = cleaned
        for a in log:
            if a.action != "accept":
                doc.note(self.name, f"page {page.index}: {a.reader}: {a.action} ({a.reason or 'problem'}: "
                                    f"{'; '.join(p.kind + ' ' + p.detail for p in a.problems)[:160]})", "warning")
        if result is None:
            reasons = " ".join(f"{a.reason or ''} {' '.join(p.detail for p in a.problems)}" for a in log).lower()
            if "content filtering" in reasons or "refus" in reasons:
                doc.note(self.name, f"page {page.index}: the model provider declined to return this page's text "
                                    f"(content filtering); the page is empty", "error")
            else:
                doc.note(self.name, f"page {page.index}: reader {reader.name} gave no usable output", "error")
            return None
        strong = tier in ("strong", "expert")
        entry = {"page": page.index, "reader": reader.name, **result.usage.model_dump(exclude_none=True),
                 "est_cost_usd": self._cost.strong_page if strong else self._cost.fast_page}
        if tier == "expert":
            entry["est_cost_usd"] = self._cost.strong_page * 4
        if escalation:
            entry.update({"escalation": "page", "area": 1.0, "reason": escalation})
        doc.usage.append(entry)
        for res in calls:
            if res.usage is not result.usage and res.usage.input_tokens is not None:
                doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "retry",
                                  **res.usage.model_dump(exclude_none=True)})
        if result.refused or result.truncated:
            doc.note(self.name, f"page {page.index}: stop_reason={result.stop_reason}", "warning")
        if result.stop_reason == "text_layer_fallback":
            doc.note(self.name, f"page {page.index}: {reader.name} unusable; PDF text layer used instead", "warning")
            pr = PageRead("pdf_text_layer", result, "fast")
            pr.problems = []
            return pr
        pr = PageRead(reader.name, result, "strong" if strong else "fast")
        pr.problems = check(result) if result.text.strip() else detect_problems(result.text)
        return pr

    # ---- cheap profile: page escalation ----
    @staticmethod
    def _layout_text(layout: list[Region]) -> Optional[str]:
        parts = [lr.candidates[0].content for lr in layout if lr.candidates]
        return "\n\n".join(parts) if parts else None

    def _escalate_page(self, doc: Document, page: Page, reads: list[PageRead], policy: EscalationPolicy,
                       esc_opt: dict, ev) -> Optional[PageRead]:
        fast = reads[0] if reads else None
        second = reads[1].text if len(reads) > 1 else self._layout_text(page.regions)
        reason = "no fast read" if fast is None else policy.page_reason(fast.text, second, fast.problems)
        if reason is None:
            return None
        strong = self._read_page(doc, page, self._reader(esc_opt.get("reader", "cached:haiku_hires")), ev,
                                 "strong", escalation=reason)
        if strong is None or any(p.fatal for p in strong.problems):
            doc.note(self.name, f"page {page.index}: escalation ({reason}) failed; fast read kept", "warning")
            return None
        if fast is not None and ev is not None and len(ev) >= 80:  # never worse: the strong read must not drop text
            if ev.recall(read_tokens(strong.text)) < ev.recall(read_tokens(fast.text)) - 0.15:
                doc.note(self.name, f"page {page.index}: strong read covers fewer of the page's words; "
                                    "fast read kept", "warning")
                page.properties["escalation"] = {"level": "page", "reason": reason, "used": False}
                reads.append(strong)
                return None
        page.properties["escalation"] = {"level": "page", "reason": reason, "used": True}
        doc.note(self.name, f"page {page.index}: escalated to {strong.source} ({reason})")
        return strong

    # ---- region construction ----
    def _from_blocks(self, result: ReadResult, source: str) -> list[Region]:
        return [Region(type=b.type or RegionType.TEXT, bbox=b.bbox, content=b.text, text=plain_text(b.text),
                       native_label=b.native_label, source=source,
                       candidates=[Candidate(source=source, content=b.text)],
                       meta={"bbox_source": "reader"} if b.bbox else {})
                for b in result.blocks]

    def _segment(self, page: Page, layout: list[Region], read: PageRead) -> None:
        """Split a read into blocks and match each block to a layout region (cached on the read)."""
        if read.blocks is not None:
            return
        read.blocks = split_markdown(read.text) if self.opt("segment", True) else []
        layout_tokens = [self._layout_tokens(page, lr) for lr in layout]
        match_at = float(self.opt("match_at", 0.6))
        for b in read.blocks:
            bt = norm_tokens(b.text)
            best: Match = (None, 0.0, 0.0)
            if bt:
                for j, lt in enumerate(layout_tokens):
                    if not lt:
                        continue
                    if len(bt) <= 2 and bt != lt:  # tiny blocks ("3", "Notes") only match exactly
                        continue
                    fwd = containment(bt, lt)
                    if fwd > best[1] or (fwd == best[1] and fwd > 0 and containment(lt, bt) > best[2]):
                        best = (j, fwd, containment(lt, bt))
            read.matches.append(best if best[1] >= match_at else (None, best[1], best[2]))

    def _map_page(self, doc: Document, page: Page, layout: list[Region], read: PageRead,
                  others: list[PageRead]) -> list[Region]:
        self._segment(page, layout, read)
        blocks, matches = read.blocks or [], read.matches
        usage = read.result.usage.model_dump(exclude_none=True)
        if not blocks:
            doc.note(self.name, f"page {page.index}: no block mapping; page-level content in one Text region",
                     "warning")
            return [Region(type=RegionType.TEXT, content=read.text.strip(), text=plain_text(read.text),
                           source=read.source, meta={"page_level": True},
                           candidates=[Candidate(source=read.source, content=read.text, usage=usage)])]

        match_at = float(self.opt("match_at", 0.6))
        use_furniture = bool(self.opt("layout_furniture", False))
        band = float(self.opt("furniture_band", 1.0))  # only trust furniture labels this close to an edge
        consensus = bool(self.opt("furniture_consensus", True)) and bool(others)
        other_tokens = [norm_tokens(o.text) for o in others] if consensus else []
        uses: dict[int, int] = {}
        for j, _, _ in matches:
            if j is not None:
                uses[j] = uses.get(j, 0) + 1

        regions: list[Region] = []
        for b, (j, fwd, rev) in zip(blocks, matches):
            r = Region(type=b.type, heading_level=b.heading_level, content=b.text, text=plain_text(b.text),
                       source=read.source, candidates=[Candidate(source=read.source, content=b.text)])
            if j is not None:
                lr = layout[j]
                r.native_label = lr.native_label
                r.meta.update({"layout_region": lr.id, "layout_match": round(fwd, 3)})
                r.candidates.extend(c.model_copy() for c in lr.candidates)
                if lr.type in FURNITURE:
                    in_band = lr.bbox is not None and (lr.bbox.bottom <= band or lr.bbox.top >= 1 - band)
                    if use_furniture and rev >= match_at and in_band:
                        r.type = lr.type
                    elif consensus and rev >= match_at and fwd >= 0.8 and b.type != RegionType.TABLE:
                        bt = norm_tokens(b.text)
                        # the reader was asked to omit running headers/footers: another read that
                        # did omit this block, plus the layout label, are two votes against one
                        if bt and all(containment(bt, ot) < 0.5 for ot in other_tokens):
                            r.type = lr.type
                            r.meta["furniture_by"] = "layout label + omitted by another read"
                elif b.type == RegionType.TEXT:
                    r.type = lr.type
                if lr.bbox is not None:
                    if uses[j] == 1 and fwd >= 0.8 and rev >= 0.8:
                        r.bbox = lr.bbox.model_copy()
                        r.meta["bbox_source"] = "layout"
                    else:  # partial match: Ground narrows the box down to this block's words
                        r.meta["search_bbox"] = lr.bbox.model_dump()
            regions.append(r)
        if regions:
            regions[0].candidates[0].usage = usage

        if layout and not uses:
            doc.note(self.name, f"page {page.index}: no markdown block matched a layout region", "warning")
        if self.opt("keep_layout_furniture", True):
            head, tail = [], []
            for j, lr in enumerate(layout):
                if j in uses or lr.type not in FURNITURE or not lr.candidates:
                    continue
                text = lr.candidates[0].content
                fr = Region(type=lr.type, bbox=lr.bbox, content=text, text=plain_text(text), source=lr.source,
                            native_label=lr.native_label, candidates=[c.model_copy() for c in lr.candidates],
                            meta={"bbox_source": "layout", "layout_region": lr.id, "from_layout_only": True})
                (head if (lr.bbox and lr.bbox.top < 0.5) else tail).append(fr)
            regions = head + regions + tail
        return regions

    # ---- tables ----
    @staticmethod
    def _box_tokens(page: Page, lr: Region) -> list[str]:
        src = pick_word_source(page)
        if not src or lr.bbox is None:
            return []
        words = page.words[src]
        return norm_tokens(" ".join(words[i].text for i in words_in(words, lr.bbox)))

    def _tables(self, page: Page, layout: list[Region]):
        """(layout table, indices of the page regions holding its content, words in its box).
        Indices are computed when each item is produced, so replacing regions between items is safe."""
        for lr in layout:
            if lr.type != RegionType.TABLE or lr.bbox is None:
                continue
            idxs = [i for i, r in enumerate(page.regions)
                    if r.meta.get("layout_region") == lr.id and not r.meta.get("from_layout_only")]
            yield lr, idxs, self._box_tokens(page, lr)

    @staticmethod
    def _content_of(page: Page, idxs: list[int]) -> str:
        return "\n\n".join(page.regions[i].content for i in idxs)

    def _replace(self, page: Page, idxs: list[int], lr: Region, content: str, source: str, note: dict) -> None:
        """Put ``content`` where the regions ``idxs`` were (their text stays in the candidates)."""
        old = [page.regions[i] for i in idxs]
        new = Region(type=RegionType.TABLE, content=content, text=plain_text(content), source=source,
                     native_label=lr.native_label, bbox=lr.bbox.model_copy() if lr.bbox else None,
                     candidates=[Candidate(source=source, content=content)],
                     meta={"layout_region": lr.id, "bbox_source": "layout", "tier": "strong", **note})
        seen = set()
        for o in old:
            for c in o.candidates:
                if (c.source, c.content) not in seen:
                    seen.add((c.source, c.content))
                    new.candidates.append(c.model_copy(update={"meta": {**c.meta, "replaced": True}}))
        if idxs:
            first = idxs[0]
            page.regions = [r for i, r in enumerate(page.regions) if i not in set(idxs[1:])]
            page.regions[first] = new
        else:  # the read had nothing for this table: place it by the layout's vertical position
            pos = len(page.regions)
            for i, r in enumerate(page.regions):
                sb = r.bbox or (r.meta.get("search_bbox") and type(lr.bbox)(**r.meta["search_bbox"]))
                if sb is not None and sb.top > lr.bbox.top and not r.meta.get("from_layout_only"):
                    pos = i
                    break
            page.regions.insert(pos, new)

    def _select_tables(self, doc: Document, page: Page, layout: list[Region], best: PageRead,
                       others: list[PageRead]) -> None:
        """Quality profile: verify each table region against the words in its box and against the
        other read. A table both reads agree on is kept. A disputed one is re-read as a crop when
        ``table_reader`` is set, and the candidate best supported by the evidence wins; ours stays
        on a tie."""
        for o in others:
            self._segment(page, layout, o)
        spec = self.opt("table_reader")
        report = []
        for lr, idxs, wt in self._tables(page, layout):
            if len(wt) < 8 or not idxs:
                continue  # nothing of ours is mapped to this table: we cannot tell where its content went
            j = layout.index(lr)
            mine = self._content_of(page, idxs)
            cands: list[tuple[str, str]] = [(best.source, mine)]
            for o in others:
                theirs = "\n\n".join(b.text for b, m in zip(o.blocks or [], o.matches) if m[0] == j)
                if has_table(theirs):
                    cands.append((o.source, theirs))
            chk = check_table(mine, wt, cands[1][1] if len(cands) > 1 else None)
            entry = {"layout_region": lr.id, "area": round(lr.bbox.area, 4), "ok": chk.ok, "reasons": chk.reasons}
            report.append(entry)
            if chk.ok:
                continue
            if spec:
                res = self._read_crop(doc, page, lr, spec, "; ".join(chk.reasons))
                entry["escalated"] = res is not None
                if res is not None and has_table(res.text):
                    cands.append((spec, res.text.strip()))
            pick, why = choose_table(cands, wt, margin=float(self.opt("table_margin", 0.03)))
            entry["chosen"] = cands[pick][0]
            if pick != 0:
                self._replace(page, idxs, lr, cands[pick][1], cands[pick][0],
                              {"table_selected": f"{'; '.join(chk.reasons)} -> {why}"})
                doc.note(self.name, f"page {page.index}: table from {cands[pick][0]} ({why})")
        if report:
            page.properties["table_checks"] = report

    def _select_tables_no_layout(self, doc: Document, page: Page, best: PageRead, others: list[PageRead],
                                 ev) -> None:
        """No layout detector: pair each table of the chosen read with the most similar table of the
        other read; when they disagree, keep the one the page's words back better (numbers weigh
        double). The evidence is limited to tokens either table claims, so table size does not decide."""
        if ev is None:
            return
        other_tables = [(o.source, t) for o in others for t in find_tables(o.text)]
        report = []
        for r in page.regions:
            mine_tables = find_tables(r.content)
            if len(mine_tables) != 1 or r.content.strip() != mine_tables[0].strip() or not other_tables:
                continue
            mine = mine_tables[0]
            g = parse_grid(mine)
            src, theirs = max(other_tables, key=lambda st: cell_agreement(g, parse_grid(st[1])))
            og = parse_grid(theirs)
            agree = cell_agreement(g, og)
            if agree < 0.3:
                continue  # not the same table
            claimed = set(g.tokens()) | set(og.tokens())
            wt = [t.lower() for t in ev.tokens if t.lower() in claimed]
            chk = check_table(mine, wt, theirs)
            entry = {"region": r.id, "ok": chk.ok, "reasons": chk.reasons, "agreement": round(agree, 3)}
            report.append(entry)
            if chk.ok or len(wt) < 8:
                continue
            pick, why = choose_table([(best.source, mine), (src, theirs)], wt,
                                     margin=float(self.opt("table_margin", 0.03)))
            entry["chosen"] = src if pick else best.source
            if pick:
                r.candidates.append(Candidate(source=src, content=theirs, meta={"selected": True}))
                r.candidates[0].meta["replaced"] = True
                r.content, r.text, r.source = theirs, plain_text(theirs), src
                r.meta["table_selected"] = f"{'; '.join(chk.reasons)} -> {why}"
                doc.note(self.name, f"page {page.index}: table from {src} ({why})")
        if report:
            page.properties["table_checks"] = report

    # ---- crop re-read of every detected table, decided by numbers (GPU-free table repair) ----
    def _evidence_numbers(self, page: Page, lr: Region) -> tuple[list[str], str]:
        """Independent numbers inside the table box: an exact PDF layer, else OCR words read with
        high confidence."""
        if page.properties.get("text_layer_exact") and page.words.get("pdf"):
            ws = page.words["pdf"]
            return [n for i in words_in(ws, lr.bbox) for n in number_tokens(ws[i].text)], "pdf"
        min_conf = float(self.opt("ocr_number_confidence", 0.9))
        for src in ("ppocr", "tesseract"):
            ws = page.words.get(src) or []
            if ws:
                return [n for i in words_in(ws, lr.bbox) if (ws[i].confidence.score or 0) >= min_conf
                        for n in number_tokens(ws[i].text)], src
        return [], "none"

    def _crop_tables(self, doc: Document, page: Page, layout: list[Region],
                     others: Optional[list[PageRead]] = None) -> None:
        spec = self.opt("table_reader")
        tables = sorted((lr for lr in layout if lr.type == RegionType.TABLE and lr.bbox is not None),
                        key=lambda lr: -lr.bbox.area)[: int(self.opt("max_table_crops", 3))]
        tables.sort(key=lambda lr: (lr.bbox.top, lr.bbox.left))
        report = []
        if not spec or not tables:
            return
        all_tables = [lr for lr in layout if lr.type == RegionType.TABLE and lr.bbox is not None]
        table_regions = [i for i, r in enumerate(page.regions) if has_table(r.content)]
        in_order = len(table_regions) == len(all_tables)  # fallback pairing when words cannot map them
        for lr in tables:
            idxs = [i for i, r in enumerate(page.regions)
                    if r.meta.get("layout_region") == lr.id and has_table(r.content)]
            how = "words"
            if not idxs and in_order:
                k = sorted(all_tables, key=lambda x: (x.bbox.top, x.bbox.left)).index(lr)
                idxs, how = [table_regions[k]], "order"
            entry = {"layout_region": lr.id, "area": round(lr.bbox.area, 4), "mapped_by": how if idxs else None}
            report.append(entry)
            if len(idxs) != 1 or len(find_tables(page.regions[idxs[0]].content)) != 1:
                entry["skipped"] = "no single table of ours maps to this box"
                continue
            region = page.regions[idxs[0]]
            mine = region.content
            res = self._read_crop(doc, page, lr, spec, "table crop re-read", strict=True)
            if res is None:
                entry["skipped"] = "crop read unavailable"
                continue
            crops = find_tables(res.text)
            crop = max(crops, key=len) if crops else ""
            numbers, src = self._evidence_numbers(page, lr)
            choice = choose_table_numeric(mine, crop, numbers, margin=float(self.opt("table_numeric_margin", 0.05)))
            entry.update({"crop_read": True, "replaced": choice.replace, "reason": choice.reason,
                          "disputed": choice.disputed, "evidence": src, **choice.detail})
            if choice.replace:
                extra = mine.replace(find_tables(mine)[0], "\x00")  # text of ours around the table is kept
                content = extra.replace("\x00", crop) if extra.strip("\x00").strip() else crop
                self._replace(page, idxs, lr, content, spec, {"table_selected": choice.reason})
                doc.note(self.name, f"page {page.index}: table replaced by crop read ({choice.reason})")
            else:
                if crop:
                    region.candidates.append(Candidate(source=spec, content=crop, meta={"crop_read": True}))
                if choice.disputed and crop and self.opt("table_cell_votes", True):
                    table = find_tables(mine)[0]
                    g = parse_grid(table)
                    other = max((t for o in (others or []) for t in find_tables(o.text)),
                                key=lambda t: cell_agreement(g, parse_grid(t)), default=None)
                    fixed, edits, open_ = vote_number_cells(table, crop, other, numbers)
                    entry.update({"cell_edits": edits, "cells_unresolved": len(open_)})
                    if edits:
                        region.candidates.append(Candidate(source=region.source or "reader", content=mine,
                                                           meta={"before_cell_votes": True}))
                        region.content = mine.replace(table, fixed)
                        region.text = plain_text(region.content)
                        region.meta["cell_edits"] = edits
                        doc.note(self.name, f"page {page.index}: {len(edits)} number cell(s) corrected by evidence")
                    if open_:
                        region.meta["table_disputed"] = f"{len(open_)} number cell(s) differ between reads"
                elif choice.disputed:
                    region.meta["table_disputed"] = choice.reason
                if choice.disputed and self.opt("expert_table_reader"):
                    self._expert_table(doc, page, lr, region, crop, others, entry)
        page.properties["table_checks"] = report

    def _expert_table(self, doc: Document, page: Page, lr: Region, region: Region, crop: str,
                      others: Optional[list[PageRead]], entry: dict) -> None:
        """Expert tier as arbiter for a disputed table: one more crop read, then a cell-level
        majority among the other page read, the strong crop read and the expert crop read."""
        spec = self.opt("expert_table_reader")
        res = self._read_crop(doc, page, lr, spec, "expert arbiter for a disputed table", strict=True)
        if res is None:
            return
        xt = find_tables(res.text)
        xcrop = max(xt, key=len) if xt else ""
        tables = find_tables(region.content)
        if not xcrop or len(tables) != 1:
            entry["expert"] = "no usable expert table"
            return
        g = parse_grid(tables[0])
        voters = [(o.source, max(find_tables(o.text), key=lambda t: cell_agreement(g, parse_grid(t))), False)
                  for o in (others or []) if find_tables(o.text)]
        if crop:
            voters.append((self.opt("table_reader"), crop, True))
        voters.append((spec, xcrop, True))
        fixed, edits, open_ = vote_cells_majority(tables[0], voters)
        entry.update({"expert": "read", "expert_cell_edits": edits, "expert_cells_open": open_})
        region.candidates.append(Candidate(source=spec, content=xcrop, meta={"crop_read": True, "tier": "expert"}))
        if edits:
            region.candidates.append(Candidate(source=region.source or "reader", content=region.content,
                                               meta={"before_expert_votes": True}))
            region.content = region.content.replace(tables[0], fixed)
            region.text = plain_text(region.content)
            region.meta.setdefault("cell_edits", []).extend(edits)
            doc.note(self.name, f"page {page.index}: {len(edits)} table cell(s) changed by majority with the expert read")
        if open_:
            region.meta["table_disputed"] = f"{open_} cell(s) still differ between reads"
        elif edits:
            region.meta.pop("table_disputed", None)

    def _escalate_tables(self, doc: Document, page: Page, layout: list[Region], best: PageRead,
                         policy: EscalationPolicy, esc_opt: dict) -> None:
        """Cheap profile: verify every table region; re-read the failing ones as crops with the
        strong tier. The crop read replaces the fast content only if it contains a table."""
        spec = esc_opt.get("table_reader", "crop_cache:haiku")
        report = []
        for lr, idxs, wt in self._tables(page, layout):
            mine = self._content_of(page, idxs)
            second = lr.candidates[0].content if lr.candidates else None
            chk = check_table(mine, wt, second if second and has_table(second) else None)
            reason = policy.table_reason(chk)
            entry = {"layout_region": lr.id, "area": round(lr.bbox.area, 4), "ok": chk.ok, "reasons": chk.reasons}
            report.append(entry)
            if reason is None:
                continue
            if not idxs and wt and containment(wt, norm_tokens(best.text)) >= 0.5:
                entry["skipped"] = "table text is in the read but not mapped to this region"
                continue  # replacing would duplicate it
            res = self._read_crop(doc, page, lr, spec, reason)
            if res is None:
                entry["escalated"] = False
                continue
            entry["escalated"] = True
            if not has_table(res.text):
                entry["used"] = False
                doc.note(self.name, f"page {page.index}: strong crop read has no table; fast read kept", "warning")
                continue
            entry["used"] = True
            self._replace(page, idxs, lr, res.text.strip(), spec, {"escalated": reason})
            doc.note(self.name, f"page {page.index}: table re-read by {spec} ({reason})")
        if report:
            page.properties["table_checks"] = report

    def _read_crop(self, doc: Document, page: Page, lr: Region, spec: str, reason: str,
                   strict: bool = False) -> Optional[ReadResult]:
        from ..readers import prompts
        from ..readers.sizing import pad_box, render_pdf_native, resize_image_native
        reader = self._reader(spec)
        box = pad_box((lr.bbox.left, lr.bbox.top, lr.bbox.right, lr.bbox.bottom))
        ctx = ReadContext(stem=doc.stem, page_index=page.index, region_id=lr.id, bbox=lr.bbox,
                          prompt=prompts.TABLE_CROP_STRICT_FILE if strict else prompts.TABLE_CROP_PROMPT_FILE)
        zoom = float(self.opt("crop_zoom", 1.0))  # <= 0: as large as the model's limits allow
        try:
            image = None
            if reader.needs_image:
                if doc.file_type == "pdf":
                    # zoom 1.0: the crop costs its share of a native-size page (the cost model)
                    image = render_pdf_native(doc.meta["path"], page.original_page, box,
                                              crop_zoom=zoom if zoom > 0 else None,
                                              max_dpi=float(self.opt("crop_max_dpi", 400.0)))
                else:
                    src = next(iter(page.renders.values()), None)
                    image = resize_image_native(src, box) if src else None
            res = reader.read(image, RegionType.TABLE, ctx)
        except Exception as e:
            doc.note(self.name, f"page {page.index}: crop read unavailable ({str(e)[:120]}); fast read kept",
                     "warning")
            return None
        if any(p.fatal for p in detect_problems(res.text, res.stop_reason, res.refused, res.truncated)):
            doc.note(self.name, f"page {page.index}: crop read is broken; fast read kept", "warning")
            return None
        doc.usage.append({"page": page.index, "reader": reader.name, "escalation": "region",
                          "area": round(lr.bbox.area, 4), "reason": reason,
                          **res.usage.model_dump(exclude_none=True),
                          "est_cost_usd": round(self._cost.crop(lr.bbox.area), 6)})
        return res

    @staticmethod
    def _layout_tokens(page: Page, lr: Region) -> list[str]:
        if lr.candidates:
            return norm_tokens(lr.candidates[0].content)
        src = pick_word_source(page)
        if src and lr.bbox is not None:
            words = page.words[src]
            return norm_tokens(" ".join(words[i].text for i in words_in(words, lr.bbox)))
        return []
