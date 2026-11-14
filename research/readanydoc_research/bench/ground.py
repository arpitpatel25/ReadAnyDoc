"""Grounding report: citation coverage, box correctness, and how well confidence predicts failures.

    uv run python -m readanydoc_research bench --ground --profile cached-haiku            # 30-page dev subset
    uv run python -m readanydoc_research bench --ground --profile cached-haiku --full     # all 140 pages

Coverage
- ``words``: share of content words (``grounding/tokens.py``) with a citation.
- ``tokens_all``: share of all alphanumeric tokens of the block content with a box. This is the
  spine's definition (it also counts the reader's own figure descriptions and LaTeX commands) and
  is kept so numbers stay comparable with earlier runs.
Each is given as the mean over pages (macro) and pooled over all words (micro).

Correctness
- Digital pages with a trustworthy text layer: every cited token is compared with the PDF words
  that carry the same token. ``unique`` = tokens that occur exactly once on the page in both the
  text layer and the output (no ambiguity about which word is meant): correct when IoU >= 0.5.
  ``any`` = all cited tokens found in the text layer: correct when some PDF word with that token
  has IoU >= 0.5. The same pages are also grounded from PP-OCR words alone, which makes the PDF
  boxes an independent reference for the OCR path. ``inside`` additionally accepts a box that lies
  within the PDF word (OCR splits "O'BRIEN," into pieces).
- Scanned pages: the page is grounded once from PP-OCR words and once from Tesseract words; for
  tokens cited by both, agreement = IoU >= 0.5 (and the looser "centres inside each other").

Confidence: see ``confidence_report``.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter, defaultdict
from typing import Any, Optional

from rapidfuzz import fuzz

from readanydoc.model import BBox, Document, Page, RegionType
from readanydoc.pipeline import Pipeline, PipelineConfig
from readanydoc.pipeline.ground import Ground
from readanydoc.pipeline.grounding.sources import page_image, tesseract_available, tesseract_words
from readanydoc.pipeline.grounding.tokens import normalize, scan, tokens_of
from readanydoc.serialize import document_markdown
from readanydoc.textnorm import tokenize
from . import olm
from .run import bench_config

FURNITURE = {RegionType.HEADER, RegionType.FOOTER, RegionType.PAGE_NUMBER}


def page_group(page: Page) -> str:
    cls = page.properties.get("text_layer_class")
    if page.kind == "digital":
        return "digital / trustworthy text layer" if cls == "trustworthy" else "digital / unusable text layer"
    return "scanned / OCR text layer" if cls == "ocr_layer" else "scanned / no usable text layer"


def run_with_snapshot(pipe: Pipeline, stem: str) -> tuple[Document, Document]:
    """(final Document, deep copy taken just before Ground)."""
    doc = Document(source=stem)
    doc.meta.setdefault("profile", pipe.config.profile)
    doc.meta.setdefault("tier", pipe.config.tier)
    doc.meta.setdefault("reader", pipe.config.reader)
    pre: Optional[Document] = None
    for stage in pipe.stages:
        if stage.name == "ground":
            pre = doc.model_copy(deep=True)
        if stage.enabled:
            doc = stage.run(doc, pipe.ctx)
    return doc, (pre if pre is not None else doc.model_copy(deep=True))


def reground(pre: Document, **options: Any) -> Document:
    doc = pre.model_copy(deep=True)
    return Ground(**options).run(doc, None)  # type: ignore[arg-type]


# ---- coverage ----

def page_coverage(page: Page) -> dict[str, int]:
    words = cited = toks = toks_boxed = regions = boxed = estimated = 0
    for r in page.regions:
        regions += 1
        boxed += r.bbox is not None
        estimated += r.bbox is None and bool(r.meta.get("bbox_estimate"))
        spans = [(w.char_start, w.char_end) for w in r.iter_words() if w.bbox is not None]
        starts = {s for s, _ in spans}
        tk, cwords = scan(r.content)
        hit_words = {t.word for t in tk if t.start in starts}
        words += len(cwords)
        cited += len(hit_words)
        spans.sort()
        span_starts = [a for a, _ in spans]
        for t in tokenize(r.content):
            toks += 1
            k = bisect.bisect_left(span_starts, t.end)
            toks_boxed += k > 0 and spans[k - 1][1] > t.start
    return {"words": words, "cited": cited, "tokens_all": toks, "tokens_all_boxed": toks_boxed,
            "regions": regions, "boxed": boxed, "estimated": estimated}


# ---- correctness ----

def _inside(a: BBox, b: BBox) -> float:
    return a.intersection_area(b) / a.area if a.area > 0 else 0.0


def digital_correctness(doc: Document) -> dict[str, int]:
    """Cited boxes against the trustworthy text layer of the same page."""
    out: Counter[str] = Counter()
    for page in doc.pages:
        ref: dict[str, list[BBox]] = defaultdict(list)
        for w in page.words.get("pdf", []):
            if w.bbox is None:
                continue
            for t in tokens_of(w.text):
                ref[t].append(w.bbox)
        seen: Counter[str] = Counter()
        cited: list[tuple[str, BBox]] = []
        for r in page.regions:
            for w in r.iter_words():
                n = normalize(w.text)
                seen[n] += 1
                if w.bbox is not None:
                    cited.append((n, w.bbox))
        for n, box in cited:
            boxes = ref.get(n)
            if not boxes:
                out["not_in_layer"] += 1
                continue
            iou = max(box.iou(b) for b in boxes)
            ins = max(_inside(box, b) for b in boxes)
            out["any"] += 1
            out["any_ok"] += iou >= 0.5
            out["any_ok_inside"] += iou >= 0.5 or ins >= 0.8
            if len(boxes) == 1 and seen[n] == 1:
                out["unique"] += 1
                out["unique_ok"] += iou >= 0.5
                out["unique_ok_inside"] += iou >= 0.5 or ins >= 0.8
    return dict(out)


def cross_agreement(a: Document, b: Document) -> dict[str, int]:
    """Boxes of the same output token grounded from two different word sources."""
    out: Counter[str] = Counter()
    for pa, pb in zip(a.pages, b.pages):
        for ra, rb in zip(pa.regions, pb.regions):
            wb = {w.char_start: w.bbox for w in rb.iter_words() if w.bbox is not None}
            for w in ra.iter_words():
                if w.bbox is None:
                    continue
                out["a_cited"] += 1
                other = wb.get(w.char_start)
                if other is None:
                    continue
                out["both"] += 1
                out["iou_ok"] += w.bbox.iou(other) >= 0.5
                out["centre_ok"] += (other.contains_point(*w.bbox.center, 0.002)
                                     or w.bbox.contains_point(*other.center, 0.002))
        out["b_cited"] += sum(1 for r in pb.regions for w in r.iter_words() if w.bbox is not None)
    return dict(out)


# ---- confidence vs failed tests ----

def _auc(scores_pos: list[float], scores_neg: list[float]) -> Optional[float]:
    """P(score of a positive > score of a negative), ties count half."""
    if not scores_pos or not scores_neg:
        return None
    allv = sorted([(s, 1) for s in scores_pos] + [(s, 0) for s in scores_neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0
        rank_sum += avg * sum(lab for _, lab in allv[i:j])
        i = j
    n1, n0 = len(scores_pos), len(scores_neg)
    return round((rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0), 3)


def _test_texts(test) -> list[str]:
    kind = getattr(test, "type", "")
    if kind == "order":
        return [test.before, test.after]
    if kind == "table":
        return [test.cell]
    if kind == "present":
        return [test.text]
    return []  # absent / baseline tests are not about one block's transcription


def _region_for(text: str, regions: list) -> Optional[Any]:
    want = " ".join(tokens_of(text))
    if not want:
        return None
    best, best_s = None, 0.0
    for r, body in regions:
        s = fuzz.partial_ratio(want, body)
        if s > best_s:
            best, best_s = r, s
    return best if best_s >= 60 else None


def confidence_report(docs: dict[str, Document], markdown: dict[str, str]) -> dict[str, Any]:
    """Does low confidence point at failed bench tests? Page level and block level."""
    page_fail, page_tests = Counter(), Counter()
    rows = []  # (failed, region score, region label) per located test text
    unlocated = unlocated_failed = 0
    cache: dict[str, list] = {}
    for cat in olm.CATS:
        for t in olm.tests_by_cat()[cat]:
            stem = olm.stem_of(t)
            if stem not in docs:
                continue
            try:
                ok, _ = t.run(markdown[stem])
            except Exception:
                ok = False
            page_tests[stem] += 1
            page_fail[stem] += not ok
            texts = _test_texts(t)
            if not texts:
                continue
            if stem not in cache:
                cache[stem] = [(r, " ".join(tk.norm for tk in scan(r.content, drop_notes=False)[0]))
                               for p in docs[stem].pages for r in p.regions if r.type not in FURNITURE]
            found = [_region_for(x, cache[stem]) for x in texts]
            found = [r for r in found if r is not None]
            if not found:
                unlocated += 1
                unlocated_failed += not ok
                continue
            score = min((r.confidence.score if r.confidence.score is not None else 0.0) for r in found)
            label = "low" if any(r.confidence.label == "low" for r in found) else "high"
            rows.append((not ok, score, label))
    # page level
    pscore, pmin, plow = {}, {}, {}
    for stem, doc in docs.items():
        regs = [r for p in doc.pages for r in p.regions
                if r.type not in FURNITURE and r.meta.get("ground_tokens")]
        w = sum(r.meta["ground_tokens"] for r in regs)
        sc = [(r.confidence.score or 0.0) for r in regs]
        pscore[stem] = sum(s * r.meta["ground_tokens"] for s, r in zip(sc, regs)) / w if w else 0.0
        pmin[stem] = min(sc) if sc else 0.0
        plow[stem] = (sum(r.meta["ground_tokens"] for r in regs if r.confidence.label == "low") / w) if w else 1.0
    stems = [s for s in docs if page_tests[s]]
    bad = [s for s in stems if page_fail[s]]
    good = [s for s in stems if not page_fail[s]]
    out: dict[str, Any] = {
        "pages": len(stems), "pages_with_failed_test": len(bad),
        "page_auc_mean_score": _auc([-pscore[s] for s in bad], [-pscore[s] for s in good]),
        "page_auc_low_share": _auc([plow[s] for s in bad], [plow[s] for s in good]),
    }
    order = sorted(stems, key=lambda s: pscore[s])
    k = max(len(order) // 3, 1)
    for name, part in (("lowest_third", order[:k]), ("middle_third", order[k:2 * k]), ("highest_third", order[2 * k:])):
        n = sum(page_tests[s] for s in part)
        out[f"test_fail_rate_pages_{name}"] = round(100 * sum(page_fail[s] for s in part) / n, 1) if n else None
    # block level
    low = [r for r in rows if r[2] == "low"]
    high = [r for r in rows if r[2] == "high"]
    failed = sum(r[0] for r in rows)
    out.update({
        "tests_located": len(rows), "tests_located_failed": failed,
        "tests_unlocated": unlocated, "tests_unlocated_failed": unlocated_failed,
        "fail_rate_in_low_blocks": round(100 * sum(r[0] for r in low) / len(low), 1) if low else None,
        "fail_rate_in_high_blocks": round(100 * sum(r[0] for r in high) / len(high), 1) if high else None,
        "tests_in_low_blocks": len(low), "tests_in_high_blocks": len(high),
        "failed_tests_caught_by_low_pct": round(100 * sum(r[0] for r in low) / failed, 1) if failed else None,
        "block_auc_score": _auc([-r[1] for r in rows if r[0]], [-r[1] for r in rows if not r[0]]),
    })
    return out


# ---- the report ----

def _pct(a: float, b: float) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def ground_report(profile: str | PipelineConfig, stems: Optional[list[str]] = None,
                  tesseract: bool = True) -> dict[str, Any]:
    cfg = bench_config(profile)
    pipe = Pipeline(cfg)
    stems = stems if stems is not None else olm.all_stems()
    docs: dict[str, Document] = {}
    md: dict[str, str] = {}
    groups: dict[str, dict[str, Any]] = {}
    kinds: dict[str, dict[str, Any]] = {}
    dig_default: Counter[str] = Counter()
    dig_ocr: Counter[str] = Counter()
    dig_cross: Counter[str] = Counter()
    scan_cross: Counter[str] = Counter()
    have_tess = tesseract and tesseract_available()
    for stem in stems:
        doc, pre = run_with_snapshot(pipe, stem)
        docs[stem] = doc
        md[stem] = document_markdown(doc)
        page = doc.pages[0]
        cov = page_coverage(page)
        for key, table in ((page_group(page), groups), (page.kind or "unknown", kinds)):
            g = table.setdefault(key, {"pages": 0, "macro_words": [], "macro_tokens_all": [], **{k: 0 for k in cov}})
            g["pages"] += 1
            for k, v in cov.items():
                g[k] += v
            if cov["words"]:
                g["macro_words"].append(cov["cited"] / cov["words"])
            if cov["tokens_all"]:
                g["macro_tokens_all"].append(cov["tokens_all_boxed"] / cov["tokens_all"])
        if page.properties.get("text_layer_class") == "trustworthy":
            dig_default.update(digital_correctness(doc))
            ocr_doc = reground(pre, word_source="ppocr", tesseract="off")
            dig_ocr.update(digital_correctness(ocr_doc))
            dig_cross.update(cross_agreement(reground(pre, word_source="pdf", tesseract="off"), ocr_doc))
        elif page.kind == "scanned" and have_tess and page.words.get("ppocr"):
            img = page_image(pre, pre.pages[0])
            words = tesseract_words(img, 0) if img else []
            if words:
                pre.pages[0].words["tesseract"] = words
                scan_cross.update(cross_agreement(reground(pre, word_source="ppocr", tesseract="off"),
                                                  reground(pre, word_source="tesseract", tesseract="off")))

    def finish(g: dict[str, Any]) -> dict[str, Any]:
        return {"pages": g["pages"],
                "coverage_words_macro": round(100 * statistics.mean(g["macro_words"]), 1) if g["macro_words"] else None,
                "coverage_words_micro": _pct(g["cited"], g["words"]),
                "coverage_tokens_all_macro": round(100 * statistics.mean(g["macro_tokens_all"]), 1)
                if g["macro_tokens_all"] else None,
                "coverage_tokens_all_micro": _pct(g["tokens_all_boxed"], g["tokens_all"]),
                "regions": g["regions"], "regions_real_box_pct": _pct(g["boxed"], g["regions"]),
                "regions_estimate_only_pct": _pct(g["estimated"], g["regions"]),
                "regions_no_box_pct": _pct(g["regions"] - g["boxed"] - g["estimated"], g["regions"])}

    def corr(c: Counter) -> dict[str, Any]:
        return {"cited_tokens_checked": c["any"], "cited_tokens_not_in_text_layer": c["not_in_layer"],
                "unique_tokens": c["unique"], "unique_iou_ok_pct": _pct(c["unique_ok"], c["unique"]),
                "unique_iou_or_inside_pct": _pct(c["unique_ok_inside"], c["unique"]),
                "any_iou_ok_pct": _pct(c["any_ok"], c["any"]),
                "any_iou_or_inside_pct": _pct(c["any_ok_inside"], c["any"])}

    def cross(c: Counter) -> dict[str, Any]:
        return {"tokens_cited_by_first": c["a_cited"], "tokens_cited_by_second": c["b_cited"],
                "tokens_cited_by_both": c["both"], "iou_ok_pct": _pct(c["iou_ok"], c["both"]),
                "centres_agree_pct": _pct(c["centre_ok"], c["both"])}

    return {
        "profile": cfg.profile, "pages": len(stems),
        "by_kind": {k: finish(v) for k, v in sorted(kinds.items())},
        "by_group": {k: finish(v) for k, v in sorted(groups.items())},
        "correctness_digital_default": corr(dig_default),
        "correctness_digital_ppocr_only": corr(dig_ocr),
        "agreement_digital_pdf_vs_ppocr": cross(dig_cross),
        "agreement_scanned_ppocr_vs_tesseract": cross(scan_cross) if have_tess else "tesseract not installed",
        "confidence": confidence_report(docs, md),
    }


def format_report(rep: dict[str, Any]) -> str:
    lines = [f"== ground report: {rep['profile']} ({rep['pages']} pages)"]
    head = ("pages", "coverage_words_macro", "coverage_words_micro", "coverage_tokens_all_macro",
            "coverage_tokens_all_micro", "regions_real_box_pct", "regions_estimate_only_pct", "regions_no_box_pct")
    lines.append(f"{'page kind':36s} pages  words%(macro/micro)  all-tokens%(macro/micro)  boxes%(real/est/none)")
    for section in ("by_kind", "by_group"):
        for k, v in rep[section].items():
            a = [v[h] for h in head]
            lines.append(f"{k:36s} {a[0]:5d}  {a[1]!s:>6} / {a[2]!s:<6}      {a[3]!s:>6} / {a[4]!s:<6}"
                         f"        {a[5]} / {a[6]} / {a[7]}")
    for key in ("correctness_digital_default", "correctness_digital_ppocr_only", "agreement_digital_pdf_vs_ppocr",
                "agreement_scanned_ppocr_vs_tesseract", "confidence"):
        lines.append(f"{key}: {rep[key]}")
    return "\n".join(lines)
