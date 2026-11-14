"""Stage 6, Ground: word boxes, region boxes, table cell boxes, confidence.

Per page, block by block:
1. Word sources are tried in order of trust: the PDF text layer when triage accepted it, PP-OCR
   words, Tesseract words (run only when coverage is poor and the binary exists), and last an
   untrusted text layer, from which only exact runs of three or more tokens are taken.
2. A block with a box (its layout box, or the layout box it shares with other blocks) is aligned
   to the unclaimed source words inside that box plus a margin. A block without a box, or whose
   box holds under half of its words, is aligned against the unclaimed words of the whole page
   and kept only where its matches form compact groups of lines. One- and two-token blocks are
   placed last, by exact text, using the boxes of their reading-order neighbours.
3. A source word is cited by one block only. Matches are checked by position (``_filter``).
4. The region box is the layout box when the words confirm it, else the union of the block's
   cited words. A block with no word evidence keeps ``bbox=None`` and gets
   ``meta["bbox_estimate"]`` (an unused figure box, the shared layout box, or the gap between its
   neighbours); the serializer emits that estimate marked low. No word box is ever made up.
5. Table cells: box = union of the cited words in the cell; cells whose text wraps are retried
   inside the row band and column band measured from the other cells.
6. Confidence per region from the evidence on the Document (``grounding/confidence.py``).

Options: ``word_source`` (force one source), ``tesseract`` ("auto" | "off" | "always"),
``tesseract_below`` (page coverage that triggers it, default 0.85), ``confidence`` (default on),
``high_at`` (default 0.8), ``min_coverage_for_box`` (default 0.3).

7. Block types that depend on position (Header, Footer, Page Number, the page's Title, headings set in
   larger type) are decided here, once the boxes exist (``typing/layout.py``). Options: ``layout_types``
   ("auto", the default: on for pages without layout-detector regions; True / False force it), ``page_numbers`` (default on; off types a bare page number Header / Footer, which is
   what Reducto r-1 returns), ``font_headings`` (default on).
"""

from __future__ import annotations

from typing import Optional

from ..model import BBox, Confidence, Document, Evidence, Line, Page, Region, RegionType, Word
from .base import PipelineContext, Stage, register_stage
from .grounding.align import Match, align
from .grounding.confidence import score_region
from .grounding.sources import Source, fix_cropped_pdf_words, page_image, source_priority, tesseract_available, tesseract_words
from .grounding.tables import parse_table_cells, table_cells  # noqa: F401  (parse_table_cells: public API)
from .grounding.tokens import is_figure_note, scan
from .typing.layout import retype_page

__all__ = ["Ground", "parse_table_cells"]

_FIGURE_LABELS = {"image", "chart", "figure", "header_image", "footer_image", "seal"}


class _Hit:
    """One region token grounded on source words."""

    __slots__ = ("source", "words", "tokens", "kind", "score", "how")

    def __init__(self, source: str, words: tuple[int, ...], tokens: tuple[int, ...], kind: str, score: float,
                 how: str):
        self.source = source
        self.words = words  # word indices in the source
        self.tokens = tokens  # flat token indices in the source (for claiming)
        self.kind = kind
        self.score = score
        self.how = how  # "box" | "page" | "tiny" | "cell"


class _State:
    def __init__(self, region: Region, order: int):
        self.region = region
        self.order = order
        self.toks, self.cwords = scan(region.content)
        self.norms = [t.norm for t in self.toks]
        self.hits: dict[int, _Hit] = {}
        hint, self.hint_kind = None, None
        if region.bbox is not None:
            hint, self.hint_kind = region.bbox, "own"
        elif region.meta.get("search_bbox"):
            hint, self.hint_kind = BBox(**region.meta["search_bbox"]), "shared"
        self.hint: Optional[BBox] = hint
        self.hint_ok = False  # the hint box holds at least half of the tokens
        self.found: Optional[BBox] = None  # union of the source words cited so far

    @property
    def n(self) -> int:
        return len(self.toks)

    def unmatched(self) -> list[int]:
        return [i for i in range(self.n) if i not in self.hits]

    def coverage(self) -> float:
        return len(self.hits) / self.n if self.n else 0.0


def _union(boxes: list[BBox]) -> Optional[BBox]:
    return BBox.union(boxes) if boxes else None


@register_stage("ground")
class Ground(Stage):
    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        for page in doc.pages:
            self._page(doc, page)
        if (doc.meta.get("prepare") or {}).get("changed"):  # boxes back onto the page as it was given
            from ..intake.prepare import restore_original_frame
            restore_original_frame(doc)
        return doc

    # ---- page driver ----
    def _sources(self, page: Page) -> list[Source]:
        aspect = (page.height / page.width) if page.width and page.height else 1.0
        forced = self.opt("word_source")
        if forced:
            return [Source(forced, page.words[forced], aspect)] if page.words.get(forced) else []
        return [Source(name, page.words[name], aspect, trusted) for name, trusted in source_priority(page)]

    def _page(self, doc: Document, page: Page) -> None:
        if fix_cropped_pdf_words(doc, page):
            doc.note(self.name, f"page {page.index}: text-layer words moved into the crop box")
        states = [_State(r, k) for k, r in enumerate(page.regions)]
        for st in states:
            st.region.lines = []
            st.region.cells = table_cells(st.region.content) if st.region.type == RegionType.TABLE else []
        sources = self._sources(page)
        for src in sources:
            self._run_source(states, src)
        mode = self.opt("tesseract", "auto")
        if mode != "off" and not self.opt("word_source") and not page.words.get("tesseract"):
            total = sum(st.n for st in states)
            cov = sum(len(st.hits) for st in states) / total if total else 1.0
            if total and (mode == "always" or cov < float(self.opt("tesseract_below", 0.85))) \
                    and tesseract_available():
                img = page_image(doc, page)
                words = tesseract_words(img, page.index) if img else []
                if words:
                    page.words["tesseract"] = words
                    aspect = (page.height / page.width) if page.width and page.height else 1.0
                    src = Source("tesseract", words, aspect)
                    sources.append(src)
                    self._run_source(states, src)
        # second and third Tesseract passes with other page segmentations, only while most of the page
        # is still unplaced: the automatic segmentation finds almost nothing on some scans
        retry_below = float(self.opt("tesseract_retry_below", 0.6))
        if mode != "off" and not self.opt("word_source") and tesseract_available():
            for psm in self.opt("tesseract_retry_psm", (6, 11)):
                total = sum(st.n for st in states)
                cov = sum(len(st.hits) for st in states) / total if total else 1.0
                if not total or cov >= retry_below:
                    break
                name = f"tesseract_psm{psm}"
                words = page.words.get(name)
                if words is None:
                    img = page_image(doc, page)
                    words = tesseract_words(img, page.index, psm=int(psm), source=name) if img else []
                    page.words[name] = words
                if words and name not in {s.name for s in sources}:
                    aspect = (page.height / page.width) if page.width and page.height else 1.0
                    src = Source(name, words, aspect)
                    sources.append(src)
                    self._run_source(states, src)
        by_name = {s.name: s for s in sources}
        if not sources:
            doc.note(self.name, f"page {page.index}: no word boxes available", "warning")
        for st in states:
            if st.region.cells:
                self._cells(st, sources, by_name)
        self._boxes(page, states, by_name)
        layout_types = self.opt("layout_types", "auto")
        if layout_types == "auto":  # a layout detector's labels, when there are any, decide the furniture
            layout_types = not page.layout_regions
        if layout_types:
            self._layout_types(page, states, by_name)
            self._estimates(page, states)  # neighbours' types changed nothing about boxes; keep estimates fresh
        total = matched = 0
        for st in states:
            self._emit(st, by_name)
            total += st.n
            matched += len(st.hits)
        primary = sources[0].name if sources else None
        page.properties["ground_source"] = primary
        page.properties["ground_sources"] = [s.name for s in sources]
        page.properties["ground_tokens"] = total
        page.properties["ground_coverage"] = round(matched / total, 4) if total else None
        if self.opt("confidence", True):
            high_at = float(self.opt("high_at", 0.8))
            cache: dict = {}
            for st in states:
                score, parts = score_region(st.region, page, cache)
                if st.region.confidence.score is not None:
                    st.region.meta["verify_confidence"] = st.region.confidence.score
                st.region.confidence = Confidence.of(score, high_at)
                st.region.meta["confidence_parts"] = parts
                if st.region.meta.get("ocr_fallback"):  # the model declined the page; plain OCR text stands in
                    st.region.confidence = Confidence(score=min(score, 0.5), label="low")
                if st.region.meta.get("flagged"):  # an unresolved Verify check (pipeline/checks) keeps the block low
                    st.region.confidence = Confidence(score=min(score, 0.5), label="low")

    # ---- one source over all regions ----
    def _run_source(self, states: list[_State], src: Source) -> None:
        if not src.words:
            return
        strict = not src.trusted
        pad_y = 0.6 * src.median_height + 0.003
        pad_x = 0.012
        big = [st for st in states if st.n >= 3]
        own = [st for st in big if st.hint_kind == "own"]
        shared = sorted((st for st in big if st.hint_kind == "shared"), key=lambda s: -s.n)
        for st in own + shared:
            box = self._current_box(st, src)
            if box is None:
                continue
            idxs = st.unmatched()
            if not idxs:
                continue
            cand = src.free_tokens(src.words_in(box, pad_x, pad_y))
            new = self._align(st, idxs, src, cand, strict, "box")
            new = self._filter(st, new, src, inside=box)
            self._claim(st, new, src)
            if st.hint is not None and st.coverage() >= 0.5:
                st.hint_ok = True
        # blocks that are still mostly unplaced, then long unmatched tails of placed ones
        loose = sorted((st for st in big if st.coverage() < 0.5), key=lambda s: -s.n)
        for st in loose:
            # a few stray matches inside a wrong box would anchor the block there: set them aside
            held = {i: h for i, h in st.hits.items() if h.source == src.name} if st.coverage() < 0.3 else {}
            for i, h in held.items():
                del st.hits[i]
                src.claimed.difference_update(h.tokens)
            idxs = st.unmatched()
            cand = src.free_tokens(src.order)
            new = self._align(st, idxs, src, cand, strict, "page")
            new = self._filter(st, new, src, inside=None)
            # text that occurs twice on the page (a title repeated in the footer): prefer the occurrence
            # that lies between the blocks already placed before and after this one in reading order
            win = self._window(st, states) if new else None
            if win is not None:
                lo, hi = win
                inside_win = [t for t in cand if lo <= src.words[src.tok_word[t]].bbox.center[1] <= hi]
                if len(inside_win) < len(cand) and any(
                        not lo <= src.words[h.words[0]].bbox.center[1] <= hi for h in new.values()):
                    alt = self._align(st, idxs, src, inside_win, strict, "page")
                    alt = self._filter(st, alt, src, inside=None)
                    if len(alt) >= 0.9 * len(new):
                        new = alt
            if len(new) < max(2, 0.3 * len(idxs)) or len(new) <= len(held):
                new = held
            if held and new is not held:
                st.found = None
            self._claim(st, new, src)
        for st in sorted((st for st in big if st.coverage() >= 0.5), key=lambda s: -s.n):
            tail = self._long_runs(st.unmatched(), 4)
            if not tail:
                continue
            cand = src.free_tokens(src.order)
            new = self._align(st, tail, src, cand, True, "page")
            new = self._filter(st, new, src, inside=None)
            self._claim(st, new, src)
        for st in states:
            if 0 < st.n < 3 and not st.hits and not strict:
                self._tiny(st, states, src, pad_x, pad_y)

    @staticmethod
    def _window(st: _State, states: list[_State]) -> Optional[tuple[float, float]]:
        """Vertical span between the nearest well-placed blocks before and after ``st`` in reading
        order, with a margin. None when there is no such neighbour or they are in different columns."""
        def placed(s: _State) -> Optional[BBox]:
            return s.found if s.found is not None and s.n >= 3 and s.coverage() >= 0.5 else None
        prev = next((b for s in reversed(states[:st.order]) if (b := placed(s)) is not None), None)
        nxt = next((b for s in states[st.order + 1:] if (b := placed(s)) is not None), None)
        if prev is None and nxt is None:
            return None
        lo = prev.top - 0.01 if prev is not None else 0.0
        hi = nxt.bottom + 0.01 if nxt is not None else 1.0
        if prev is not None and nxt is not None and prev.top > nxt.bottom:
            return None  # column break between the neighbours
        return (lo, hi) if hi - lo > 0.01 else None

    @staticmethod
    def _long_runs(idxs: list[int], min_len: int) -> list[int]:
        out: list[int] = []
        run: list[int] = []
        for i in idxs:
            if run and i != run[-1] + 1:
                if len(run) >= min_len:
                    out.extend(run)
                run = []
            run.append(i)
        if len(run) >= min_len:
            out.extend(run)
        return out

    def _current_box(self, st: _State, src: Source) -> Optional[BBox]:
        """Where to look for this block in ``src``: its hint, else what earlier sources found."""
        if st.hint is not None and (st.hint_ok or not st.hits):
            return st.hint
        return st.found or st.hint

    def _align(self, st: _State, idxs: list[int], src: Source, cand: list[int], strict: bool,
               how: str) -> dict[int, _Hit]:
        if not idxs or not cand:
            return {}
        # unmatched tokens with sentinels where the run is broken, so nothing aligns across a hole
        rs: list[str] = []
        rmap: list[int] = []
        for n, i in enumerate(idxs):
            if n and idxs[n - 1] != i - 1:
                rs.append(f"\x00{n}")
                rmap.append(-1)
            rs.append(st.norms[i])
            rmap.append(i)
        ct = [src.tokens[j] for j in cand]
        seg = [src.segment_of[src.tok_word[j]] for j in cand]
        res = align(rs, ct, same_line=lambda a, b: seg[a] == seg[b], strict=strict)
        out: dict[int, _Hit] = {}
        for k, m in res.items():
            i = rmap[k]
            if i < 0:
                continue
            toks = tuple(cand[c] for c in m.cand)
            words = tuple(dict.fromkeys(src.tok_word[t] for t in toks))
            out[i] = _Hit(src.name, words, toks, m.kind, m.score, how)
        return out

    def _filter(self, st: _State, new: dict[int, _Hit], src: Source, inside: Optional[BBox]) -> dict[int, _Hit]:
        """Position checks. Page-wide matches must form compact groups of neighbouring segments;
        weak matches (fuzzy, slot, lone short tokens) need a matched neighbour close by."""
        if not new:
            return new
        keys = sorted(new)
        seg_of = lambda i: src.segment_of[new[i].words[0]]  # noqa: E731
        # 1. support. Consecutive region tokens grounded on nearby segments form a run. A run is
        #    credible when it holds an exact match (or touches one cited earlier); weak members
        #    (fuzzy below 0.8, slots) of other runs are dropped, and so are lone short tokens.
        keep: dict[int, _Hit] = {}
        runs: list[list[int]] = []
        for i in keys:
            if runs and i - runs[-1][-1] <= 2 and abs(seg_of(i) - seg_of(runs[-1][-1])) <= 3:
                runs[-1].append(i)
            else:
                runs.append([i])
        solid = lambda h: h.kind in ("exact", "residual", "merge") or h.score >= 0.8  # noqa: E731

        def touches_old(run: list[int]) -> bool:
            for i in (run[0] - 1, run[0] - 2, run[-1] + 1, run[-1] + 2):
                o = st.hits.get(i)
                if o is not None and o.source == src.name:
                    edge = run[0] if i < run[0] else run[-1]
                    if abs(src.segment_of[o.words[0]] - seg_of(edge)) <= 3:
                        return True
            return False

        for run in runs:
            n_solid = sum(solid(new[i]) for i in run)
            anchored = touches_old(run)
            long_exact = any(new[i].kind in ("exact", "residual", "merge") and len(st.norms[i]) >= 5 for i in run)
            if len(run) == 1:
                i = run[0]
                h = new[i]
                if anchored and (solid(h) or h.kind == "slot"):
                    keep[i] = h
                elif inside is not None and solid(h) and (len(st.norms[i]) >= 5 or st.n <= 4):
                    keep[i] = h
                continue
            credible = n_solid >= 1 and (anchored or long_exact or n_solid >= 2
                                         or (inside is not None and n_solid * 2 >= len(run)))
            for i in run:
                if solid(new[i]) and (credible or inside is not None):
                    keep[i] = new[i]
                elif credible and (n_solid * 3 >= len(run) or anchored):
                    keep[i] = new[i]
        if inside is not None or not keep:
            return keep
        # 2. page-wide: group segments that touch vertically and overlap horizontally
        segs: dict[int, list[int]] = {}
        for i, h in keep.items():
            segs.setdefault(src.segment_of[h.words[0]], []).append(i)
        boxes = {s: _union([src.words[w].bbox for w in src.segments[s]]) for s in segs}
        order = sorted(segs, key=lambda s: boxes[s].top)
        parent = {s: s for s in order}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        gap_y = 2.5 * src.median_height
        for a_i, a in enumerate(order):
            for b in order[a_i + 1:]:
                ba, bb = boxes[a], boxes[b]
                if bb.top - ba.bottom > gap_y:
                    break
                x_overlap = min(ba.right, bb.right) - max(ba.left, bb.left)
                if x_overlap > -0.02:
                    parent[find(a)] = find(b)
        groups: dict[int, list[int]] = {}
        for s in order:
            groups.setdefault(find(s), []).extend(segs[s])
        total = len(keep)
        need = max(3, 0.15 * total) if total >= 6 else max(2, 0.5 * total)
        good: dict[int, _Hit] = {}
        for members in groups.values():
            exact = sum(keep[i].kind in ("exact", "residual", "merge") for i in members)
            if len(members) >= need and exact >= max(1, 0.34 * len(members)):
                for i in members:
                    good[i] = keep[i]
        return good

    @staticmethod
    def _claim(st: _State, new: dict[int, _Hit], src: Source) -> None:
        for i, h in new.items():
            if i in st.hits:
                continue
            st.hits[i] = h
            src.claimed.update(h.tokens)
        if new:
            boxes = [src.words[w].bbox for h in new.values() for w in h.words]
            st.found = _union(boxes + ([st.found] if st.found else []))

    def _tiny(self, st: _State, states: list[_State], src: Source, pad_x: float, pad_y: float) -> None:
        """One- or two-token blocks: exact text on unclaimed, adjacent source tokens. With several
        occurrences the one nearest to the gap between the reading-order neighbours wins."""
        want = st.norms
        box = st.hint
        pool = src.words_in(box, pad_x, pad_y) if box is not None else src.order
        toks = src.free_tokens(pool)
        k = len(want)
        options: list[tuple[int, ...]] = []
        for a in range(len(toks) - k + 1):
            win = toks[a:a + k]
            if [src.tokens[t] for t in win] != want:
                continue
            ws = [src.tok_word[t] for t in win]
            if len({src.segment_of[w] for w in ws}) != 1:
                continue
            options.append(tuple(win))
        if not options and k == 2:  # the source glued the two tokens ("May22")
            joined = "".join(want)
            options = [(t,) for t in toks if src.tokens[t] == joined and len(joined) >= 4]
        if not options:
            return
        if len(options) > 1:
            lo, hi, col = self._expected(st, states)
            if lo is None and hi is None and box is None:
                return  # several candidates and nothing to tell them apart: no citation

            def cost(opt: tuple[int, ...]) -> float:
                b = src.words[src.tok_word[opt[0]]].bbox
                cy, cx = b.top + b.height / 2, b.left + b.width / 2
                c = 0.0
                if lo is not None and cy < lo:
                    c += lo - cy
                if hi is not None and cy > hi:
                    c += cy - hi
                if col is not None and not (col[0] <= cx <= col[1]):
                    c += 0.5 * min(abs(cx - col[0]), abs(cx - col[1]))
                if box is not None:
                    bc = box.center
                    c += 0.1 * (abs(cx - bc[0]) + abs(cy - bc[1]))
                return c
            options.sort(key=cost)
            if cost(options[0]) == cost(options[1]) and box is None and lo is None:
                return
        win = options[0]
        hits = {}
        for i in range(k):
            t = win[i] if len(win) == k else win[0]
            hits[i] = _Hit(src.name, (src.tok_word[t],), (t,), "exact", 1.0, "tiny")
        self._claim(st, hits, src)

    @staticmethod
    def _expected(st: _State, states: list[_State]):
        """Vertical span between the nearest placed neighbours in reading order, and their column."""
        def box_of(s: _State) -> Optional[BBox]:
            if s.hits or s.hint_kind == "own":
                return s.found or (s.hint if s.hint_kind == "own" else None)
            return None
        prev = next((b for s in reversed(states[:st.order]) if (b := box_of(s)) is not None), None)
        nxt = next((b for s in states[st.order + 1:] if (b := box_of(s)) is not None), None)
        lo = prev.bottom - 0.01 if prev is not None else None
        hi = nxt.top + 0.01 if nxt is not None else None
        if lo is not None and hi is not None and lo > hi:  # column break between the neighbours
            return None, None, None
        col = None
        if prev is not None and nxt is not None:
            col = (min(prev.left, nxt.left), max(prev.right, nxt.right))
        elif prev is not None or nxt is not None:
            b = prev or nxt
            col = (b.left, b.right)
        return lo, hi, col

    # ---- table cells ----
    def _cells(self, st: _State, sources: list[Source], by_name: dict[str, Source]) -> None:
        cells = [c for c in st.region.cells if c.char_start is not None]
        if not cells:
            return
        tok_cell: dict[int, int] = {}
        ci = 0
        ordered = sorted(range(len(cells)), key=lambda k: cells[k].char_start)
        for i, t in enumerate(st.toks):
            while ci < len(ordered) and cells[ordered[ci]].char_end <= t.start:
                ci += 1
            if ci < len(ordered) and cells[ordered[ci]].char_start <= t.start < cells[ordered[ci]].char_end:
                tok_cell[i] = ordered[ci]
        by_cell: dict[int, list[int]] = {}
        for i, k in tok_cell.items():
            by_cell.setdefault(k, []).append(i)

        def boxes_of(k: int) -> list[BBox]:
            return [by_name[st.hits[i].source].words[w].bbox for i in by_cell.get(k, []) if i in st.hits
                    for w in st.hits[i].words]

        # wrapped cells: retry unmatched tokens inside the row band x column band
        rows: dict[int, list[BBox]] = {}
        cols: dict[int, list[BBox]] = {}
        for k, c in enumerate(cells):
            bs = boxes_of(k)
            if not bs:
                continue
            if c.rowspan == 1:
                rows.setdefault(c.row, []).extend(bs)
            if c.colspan == 1:
                cols.setdefault(c.col, []).extend(bs)
        row_band = {r: (min(b.top for b in bs), max(b.bottom for b in bs)) for r, bs in rows.items()}
        col_band = {c: (min(b.left for b in bs), max(b.right for b in bs)) for c, bs in cols.items()}
        for k, c in enumerate(cells):
            idxs = [i for i in by_cell.get(k, []) if i not in st.hits]
            if not idxs:
                continue
            rb = [row_band[r] for r in range(c.row, c.row + c.rowspan) if r in row_band]
            cb = [col_band[x] for x in range(c.col, c.col + c.colspan) if x in col_band]
            if not rb or not cb:
                continue
            for src in sources:
                if not src.trusted or not idxs:
                    continue
                h = src.median_height
                area = BBox(left=min(b[0] for b in cb), top=min(b[0] for b in rb),
                            width=max(b[1] for b in cb) - min(b[0] for b in cb),
                            height=max(b[1] for b in rb) - min(b[0] for b in rb))
                cand = src.free_tokens(src.words_in(area, 0.006, 0.35 * h))
                new = self._align(st, idxs, src, cand, False, "cell")
                new = {i: hit for i, hit in new.items() if hit.kind in ("exact", "residual", "merge")
                       or hit.score >= 0.8}
                self._claim(st, new, src)
                idxs = [i for i in idxs if i not in st.hits]
        for k, c in enumerate(cells):
            c.bbox = _union(boxes_of(k))
            if c.bbox is not None:
                scores = [st.hits[i].score for i in by_cell.get(k, []) if i in st.hits]
                share = len(scores) / max(len(by_cell.get(k, [])), 1)
                c.confidence = Confidence.of(share * (sum(scores) / len(scores)))

    # ---- region boxes ----
    def _boxes(self, page: Page, states: list[_State], by_name: dict[str, Source]) -> None:
        min_cov = float(self.opt("min_coverage_for_box", 0.3))
        for st in states:
            r = st.region
            r.meta.pop("bbox_estimate", None)
            if not st.n:
                continue
            word_boxes = [by_name[h.source].words[w].bbox for h in st.hits.values() for w in h.words]
            union = _union(word_boxes)
            cov = st.coverage()
            in_hint = 0
            if st.hint is not None and st.hits:
                for h in st.hits.values():
                    b = by_name[h.source].words[h.words[0]].bbox
                    in_hint += st.hint.contains_point(*b.center, 0.02)
            if st.hint_kind == "own":
                share_in = in_hint / len(st.hits) if st.hits else 0.0
                if st.hits and share_in < 0.5 and cov >= 0.5 and union is not None and self._sane(union, st):
                    # the layout box does not hold this block's words: trust the words
                    r.meta["layout_bbox_rejected"] = st.hint.model_dump()
                    r.bbox = union
                    r.meta["bbox_source"] = "ground"
                elif st.hits and union is not None and share_in < 1.0 and cov >= 0.5:
                    # words continue past the layout box (next column, next lines): grow to them
                    grown = _union([st.hint, union])
                    if self._sane(grown, st) and grown.area <= max(4 * st.hint.area, st.hint.area + 0.05):
                        r.bbox = grown
                        r.meta["bbox_grown"] = True
            else:
                if union is not None and cov >= min_cov and self._sane(union, st):
                    r.bbox = union
                    r.meta["bbox_source"] = "ground"
                elif union is not None and st.n < 3 and st.hits and len(st.hits) == st.n:
                    r.bbox = union
                    r.meta["bbox_source"] = "ground"
            if r.bbox is not None:
                r.bbox = self._clamp(r.bbox, page.index)
        self._estimates(page, states)

    def _layout_types(self, page: Page, states: list[_State], by_name: dict[str, Source]) -> None:
        import statistics
        heights: dict = {}
        n_pdf = n_all = 0
        for st in states:
            hs = [by_name[h.source].words[w].bbox.height for h in st.hits.values() for w in h.words]
            n_all += len(st.hits)
            n_pdf += sum(h.source == "pdf" for h in st.hits.values())
            if hs:
                heights[st.region.id] = statistics.median(hs)
                st.region.meta["text_height"] = round(heights[st.region.id], 5)
        heights["__trusted__"] = bool(n_all) and n_pdf >= 0.8 * n_all
        changed = retype_page(page, heights, page_numbers=bool(self.opt("page_numbers", True)),
                              font_headings=bool(self.opt("font_headings", True)))
        page.properties["layout_types_changed"] = changed

    @staticmethod
    def _sane(box: BBox, st: _State) -> bool:
        if box.width <= 0 or box.height <= 0:
            return False
        if box.left < -0.01 or box.top < -0.01 or box.right > 1.01 or box.bottom > 1.01:
            return False
        if box.area > 0.85 and st.n < 150:  # a short block cannot cover the page
            return False
        return True

    @staticmethod
    def _clamp(b: BBox, page: int) -> BBox:
        l, t = min(max(b.left, 0.0), 1.0), min(max(b.top, 0.0), 1.0)
        r, bt = min(max(b.right, 0.0), 1.0), min(max(b.bottom, 0.0), 1.0)
        return BBox(left=l, top=t, width=max(r - l, 0.0), height=max(bt - t, 0.0), page=page)

    def _estimates(self, page: Page, states: list[_State]) -> None:
        """Blocks without word evidence: record where they probably are, never as ``bbox``."""
        used_layout = {st.region.meta.get("layout_region") for st in states if st.region.bbox is not None}
        free_figs = [lr for lr in page.layout_regions
                     if lr.bbox is not None and lr.id not in used_layout
                     and ((lr.native_label or "").lower() in _FIGURE_LABELS or lr.type == RegionType.FIGURE)]
        for st in states:
            r = st.region
            if r.bbox is not None:
                continue
            lo_box = next((s.region.bbox for s in reversed(states[:st.order]) if s.region.bbox is not None), None)
            hi_box = next((s.region.bbox for s in states[st.order + 1:] if s.region.bbox is not None), None)
            is_fig = r.type == RegionType.FIGURE or (st.n == 0 and is_figure_note(r.content))
            est, how = None, None
            if is_fig and free_figs:
                def fit(lr: Region) -> float:
                    c = lr.bbox.center[1]
                    d = 0.0
                    if lo_box is not None and c < lo_box.top:
                        d += lo_box.top - c
                    if hi_box is not None and c > hi_box.bottom:
                        d += c - hi_box.bottom
                    return d
                best = min(free_figs, key=fit)
                if len(free_figs) == 1 or fit(best) == 0.0:
                    est, how = best.bbox, "layout_figure"
                    free_figs.remove(best)
            if est is None and st.hint is not None:
                est, how = st.hint, "layout_shared"
            if est is None and lo_box is not None and hi_box is not None:
                top, bottom = lo_box.bottom, hi_box.top
                left, right = min(lo_box.left, hi_box.left), max(lo_box.right, hi_box.right)
                x_overlap = min(lo_box.right, hi_box.right) - max(lo_box.left, hi_box.left)
                if bottom - top > 0.004 and x_overlap > 0:
                    est, how = BBox(left=left, top=top, width=right - left, height=bottom - top,
                                    page=page.index), "between_neighbours"
                else:
                    # the neighbours sit side by side or in different columns: the block is next to them
                    u = _union([lo_box, hi_box])
                    if u is not None and u.area <= 0.5:
                        est, how = u, "around_neighbours"
            if est is None and lo_box is None and hi_box is not None and hi_box.top > 0.004:
                # in reading order before everything that was located: the part of the page above it
                est, how = BBox(left=0.0, top=0.0, width=1.0, height=hi_box.top, page=page.index), "above_next"
            if est is None and hi_box is None and lo_box is not None and lo_box.bottom < 0.996:
                est, how = BBox(left=0.0, top=lo_box.bottom, width=1.0, height=1.0 - lo_box.bottom,
                                page=page.index), "below_previous"
            if est is not None:
                r.meta["bbox_estimate"] = {**self._clamp(est, page.index).model_dump(), "how": how}

    # ---- write Line / Word nodes ----
    def _emit(self, st: _State, by_name: dict[str, Source]) -> None:
        r = st.region
        lines: list[Line] = []
        cur_key = None
        kinds: dict[str, int] = {}
        srcs: dict[str, int] = {}
        for i, t in enumerate(st.toks):
            raw = r.content[t.start:t.end]
            h = st.hits.get(i)
            if h is None:
                word = Word(text=raw, source=r.source, char_start=t.start, char_end=t.end)
                if not lines:
                    lines.append(Line(source=r.source))
            else:
                src = by_name[h.source]
                sw = [src.words[w] for w in h.words]
                key = (h.source, src.segment_of[h.words[0]])
                word = Word(text=raw, bbox=_union([w.bbox for w in sw]), source=r.source, bbox_source=h.source,
                            char_start=t.start, char_end=t.end, line=sw[0].line,
                            confidence=Confidence.of(h.score),
                            evidence=[Evidence(source=h.source, verdict="agree" if h.score >= 0.85 else "info",
                                               score=round(h.score, 4), text=" ".join(w.text for w in sw),
                                               note=h.kind)])
                kinds[h.kind] = kinds.get(h.kind, 0) + 1
                srcs[h.source] = srcs.get(h.source, 0) + 1
                if not lines or key != cur_key:
                    lines.append(Line(source=r.source))
                    cur_key = key
            lines[-1].words.append(word)
        for line in lines:
            line.text = " ".join(w.text for w in line.words)
            line.bbox = BBox.union(w.bbox for w in line.words if w.bbox)
        r.lines = lines
        if st.n:
            cited_words = {st.toks[i].word for i in st.hits}
            ocr = [w.confidence.score for h in st.hits.values() if h.source != "pdf"
                   for w in (by_name[h.source].words[x] for x in h.words) if w.confidence.score is not None]
            r.meta["ground_coverage"] = round(len(st.hits) / st.n, 4)
            r.meta["ground_tokens"] = st.n
            r.meta["ground_words"] = len(st.cwords)
            r.meta["ground_words_cited"] = len(cited_words)
            r.meta["ground_quality"] = round(sum(h.score for h in st.hits.values()) / st.n, 4)
            r.meta["ground_ocr_score"] = round(sum(ocr) / len(ocr), 4) if ocr else None
            r.meta["ground_kinds"] = kinds
            r.meta["ground_sources"] = srcs
            r.meta["ground_source"] = max(srcs, key=srcs.get) if srcs else None
        else:
            for k in ("ground_coverage", "ground_source"):
                r.meta.pop(k, None)
            r.meta["ground_tokens"] = 0
