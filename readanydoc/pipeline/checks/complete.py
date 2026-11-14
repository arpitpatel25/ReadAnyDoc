"""Check 2, completeness. Text that is on the page and absent from the page read is put back.

Digital pages (``text``): a line segment of the PDF text layer is *trusted* when the layer passed
triage as born-digital (``text_layer_exact``), the segment is not a duplicate (doubled glyphs), lies
on the page, and local OCR sees the same words at the same place (so it is visible, not hidden text).
It is *absent* when its normalised text is not in the read, not nearly in it (partial ratio), and its
words are not just reordered in it. A *simple* absent segment (plain words, outside tables and
figures) is restored from the file text.

Scans and images (``scan``), and optionally digital pages (``crop``): the candidate lines (OCR lines
with confident words, or trusted text-layer segments) only point at a place. That band of the page is
read again by the model from an enlarged crop, and a line of that crop read is inserted only when
the page read lacks it and the candidate that pointed there agrees with it. OCR text is never output.

Restored blocks are typed Header (top margin), Footer (bottom margin) or Text, placed by their
position among the located blocks, and carry ``meta['checks']`` with status ``restored``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

from ...model import Region, RegionType, Word
from ...readers import prompts
from . import reread
from .common import PageChecks

_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>|<!--.*?-->", re.S)  # real tags only: "< 5" and "> 3" are text
TOP, BOTTOM = 0.075, 0.925


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", _TAG.sub(" ", s or "")).lower()
    return re.sub(r"[\W_]+", " ", s, flags=re.UNICODE).strip()


@dataclass
class Seg:
    text: str
    box: reread.Box
    conf: float = 1.0
    key: str = field(default="")

    def __post_init__(self) -> None:
        self.key = norm(self.text)

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def band(self) -> Optional[str]:
        return "top" if self.cy < TOP else "bottom" if self.cy > BOTTOM else None


# ---------------------------------------------------------------------------------- candidates
def _dedupe(words: list[Word]) -> list[Word]:
    """Doubled glyphs (fake bold, shadow text) put the same word twice at the same place."""
    seen: set = set()
    out = []
    for w in words:
        if w.bbox is None or not w.text.strip():
            continue
        b = w.bbox
        if b.left < -0.001 or b.top < -0.001 or b.left + b.width > 1.001 or b.top + b.height > 1.001:
            continue  # outside the visible page
        if b.height < 0.0025 or b.width <= 0:
            continue  # too small to be read by anyone
        k = (w.text, round(b.left, 2), round(b.top, 2))
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out


def _lines(words: list[Word]) -> list[list[Word]]:
    by: dict[int, list[Word]] = {}
    for w in words:
        by.setdefault(w.line if w.line is not None else -1, []).append(w)
    return [sorted(ws, key=lambda w: w.bbox.left) for _, ws in sorted(by.items())]


def _seg(ws: list[Word]) -> Seg:
    x0 = min(w.bbox.left for w in ws)
    y0 = min(w.bbox.top for w in ws)
    x1 = max(w.bbox.left + w.bbox.width for w in ws)
    y1 = max(w.bbox.top + w.bbox.height for w in ws)
    confs = [w.confidence.score for w in ws if w.confidence.score is not None]
    return Seg(" ".join(w.text for w in ws), (x0, y0, x1, y1), sum(confs) / len(confs) if confs else 1.0)


def pdf_segments(page, gap: float = 1.8) -> list[Seg]:
    """Text-layer lines cut where the horizontal gap exceeds ``gap`` line heights (columns, table cells)."""
    aspect = (page.width / page.height) if page.width and page.height else 0.77
    out = []
    for ws in _lines(_dedupe(page.words.get("pdf", []))):
        run = [ws[0]]
        for w in ws[1:]:
            p = run[-1]
            h = max(p.bbox.height, w.bbox.height)
            if (w.bbox.left - (p.bbox.left + p.bbox.width)) * aspect > gap * h:
                out.append(_seg(run))
                run = []
            run.append(w)
        out.append(_seg(run))
    return out


def ocr_segments(page, source: str = "tesseract", min_conf: float = 0.75) -> list[Seg]:
    """OCR lines reduced to their confident words; a line needs two of them (or one long one)."""
    out = []
    for ws in _lines([w for w in page.words.get(source, []) if w.bbox is not None]):
        good = [w for w in ws if (w.confidence.score or 0) >= min_conf and len(re.sub(r"[\W_]", "", w.text)) >= 2]
        letters = sum(len(re.sub(r"[\W_]", "", w.text)) for w in good)
        if len(good) < 0.7 * len(ws):
            continue  # a line OCR mostly could not read is not a pointer to anything
        if (len(good) >= 2 and letters >= 6) or (len(good) == 1 and letters >= 5 and (good[0].confidence.score or 0) >= 0.9):
            out.append(_seg(good))
    return out


def visible(seg: Seg, ocr: list[Word], at: float = 0.6, exact: bool = False) -> bool:
    """Local OCR reads the segment's words at the segment's place. ``exact``: the same words, letter for
    letter (needed before text is copied from the file: small capitals and glued words fail here)."""
    x0, y0, x1, y1 = reread.pad(seg.box, 0.01, 0.006)
    near = [norm(w.text) for w in ocr if w.bbox is not None and x0 <= w.bbox.center[0] <= x1 and y0 <= w.bbox.center[1] <= y1]
    near = [t for n in near for t in n.split()]
    toks = [t for t in seg.key.split() if len(t) >= 2 or t.isdigit()]
    if not toks or not near:
        return False
    if exact:
        have = set(near)
        return sum(1 for t in toks if t in have) / len(toks) >= max(at, 0.8)
    hit = sum(1 for t in toks if any(t == n or (len(t) >= 4 and fuzz.ratio(t, n) >= 75) for n in near))
    return hit / len(toks) >= at


def simple(seg: Seg) -> bool:
    """Plain words that can be copied from the file as they are: no glued words, doubled glyphs,
    unmapped glyphs or formula fragments."""
    t = seg.text
    if "(cid:" in t or "�" in t or re.search(r"(?:(\w)\1){3,}", t):
        return False
    if any(len(w) > 28 for w in t.split()):
        return False
    plain = sum(1 for ch in t if ch.isalnum() or ch in " .,;:'\"()-–—/&%$#@!?’“”©®|+[]")
    if plain / max(len(t), 1) < 0.92:
        return False
    words = [w for w in seg.key.split() if w.isalpha() and len(w) >= 2]
    return len(seg.key) >= 3 and (len(words) >= 1 or (seg.band is not None and seg.key.replace(" ", "").isdigit()))


def _wordy(k: str, band: Optional[str]) -> bool:
    """Enough to call it a line of text: a word of three letters, or a bare number in a page margin."""
    if any(t.isalpha() and len(t) >= 3 for t in k.split()):
        return True
    return band is not None and k.replace(" ", "").isdigit() and len(k) >= 2


def absent(seg: Seg, read: str, read_tokens: set, near_at: float = 88, cover_at: float = 0.7, fuzzy: bool = False) -> bool:
    """Is the segment missing from the read? Not when its text is there as words, as the same characters with
    other word breaks (text layers glue and split words), nearly (partial ratio), or as the same words in
    another order (table cells, formulas)."""
    k = seg.key
    ks = k.replace(" ", "")
    if len(ks) < 3 or not _wordy(k, seg.band):
        return False
    if f" {k} " in f" {read} ":
        return False
    tight = read.replace(" ", "")
    if len(ks) >= 4 and ks in tight:
        return False
    if len(k) >= 6 and fuzz.partial_ratio(k, read) >= near_at:
        return False
    if len(ks) >= 8 and fuzz.partial_ratio(ks, tight) >= near_at:
        return False
    toks = [t for t in k.split() if len(t) >= 2 or t.isdigit()]
    if not toks:
        return False
    if fuzzy:
        hit = sum(1 for t in toks if t in read_tokens or (len(t) >= 5 and any(abs(len(t) - len(r)) <= 2 and fuzz.ratio(t, r) >= 80
                                                                                 for r in read_tokens)))
    else:
        hit = sum(1 for t in toks if t in read_tokens)
    cover = hit / len(toks)
    if seg.band is None and cover >= cover_at:
        return False  # the words are there in another order or split by markup (tables, formulas)
    if seg.band is not None and cover >= 1.0 and len(toks) <= 4:
        return False  # a few words that the page has anyway: not enough to call a margin line missing
    return True


# ---------------------------------------------------------------------------------- placing
def _inside(seg: Seg, box: reread.Box) -> bool:
    cx = (seg.box[0] + seg.box[2]) / 2
    return box[0] <= cx <= box[2] and box[1] <= seg.cy <= box[3]


def _insert(pc: PageChecks, seg: Seg, content: str, how: str, detail: str) -> Region:
    page = pc.page
    rtype = RegionType.HEADER if seg.band == "top" else RegionType.FOOTER if seg.band == "bottom" else RegionType.TEXT
    region = Region(type=rtype, content=content, text=content, source=how,
                    meta={"restored": True})
    g = pc.located()
    idx = 0 if seg.band == "top" else len(page.regions)
    if seg.band is None and g is not None:
        idx, best = len(page.regions), None
        for i, r in enumerate(g.regions[:len(page.regions)]):
            if r.bbox is None or page.regions[i].meta.get("restored"):
                continue
            b = reread.xyxy(r.bbox)
            overlap = min(b[2], seg.box[2]) - max(b[0], seg.box[0]) > 0
            if b[1] <= seg.cy and overlap and (best is None or b[1] >= best):
                best, idx = b[1], i + 1
        if best is None:
            first_below = next((i for i, r in enumerate(g.regions[:len(page.regions)])
                                if r.bbox is not None and r.bbox.top > seg.cy), None)
            idx = first_below if first_below is not None else len(page.regions)
    if seg.band == "top":  # keep several restored header lines in their printed order
        while idx < len(page.regions) and page.regions[idx].meta.get("restored") and page.regions[idx].type == RegionType.HEADER:
            idx += 1
    tier = next((r.meta.get("tier") for r in page.regions if r.meta.get("tier")), None)
    if tier:
        region.meta["tier"] = tier
    page.regions.insert(idx, region)
    pc.cache.clear()  # the located copy no longer matches the block list
    pc.record(region, "completeness", "restored", detail, how=how, box=[round(v, 4) for v in seg.box])
    return region


def _read_state(pc: PageChecks) -> tuple[str, set]:
    read = norm("\n".join(r.content for r in pc.page.regions))
    return read, set(read.split())


def _skip_boxes(pc: PageChecks, kinds=(RegionType.TABLE, RegionType.FIGURE)) -> list[reread.Box]:
    g = pc.located()
    if g is None:
        return []
    return [reread.pad(reread.xyxy(r.bbox), 0.005, 0.005) for r in g.regions if r.bbox is not None and r.type in kinds]


# ---------------------------------------------------------------------------------- digital: file text
def restore_from_text_layer(pc: PageChecks) -> int:
    page = pc.page
    if not page.properties.get("text_layer_exact") or not page.words.get("pdf"):
        return 0
    ocr = next((page.words[k] for k in page.words if k != "pdf" and page.words[k]), [])
    if not ocr:
        return 0  # nothing independent says the layer's text is visible
    read, toks = _read_state(pc)
    cands = [s for s in pdf_segments(page) if absent(s, read, toks)]
    if not cands:
        return 0
    skip = _skip_boxes(pc)
    cands = [s for s in cands if visible(s, ocr)]
    body = [s for s in cands if s.band is None]
    ok = [s for s in cands if simple(s) and visible(s, ocr, exact=True) and not any(_inside(s, b) for b in skip)]
    ok = _paragraphs(ok)
    max_body = int(pc.opt("complete_max_body", 6))
    n_body_ok = sum(1 for s in ok if s.band is None)
    if n_body_ok > max_body:  # that much missing body text is a figure, a diagram or a failed read: not a dropped line
        ok = [s for s in ok if s.band is not None]
    n = 0
    for s in sorted(ok, key=lambda s: (s.band != "top", s.cy, s.box[0])):
        read, toks = _read_state(pc)
        if not absent(s, read, toks):
            continue
        _insert(pc, s, re.sub(r"[\ue000-\uf8ff]", "", s.text).strip(), "pdf_text_layer", "line of the PDF text layer that the page read lacked; local OCR sees it too")
        n += 1
    kept = " ".join(s.key for s in ok)
    left = [s for s in body if s.key not in kept]
    if left and pc.page.regions and bool(pc.opt("complete_flag_left", True)):
        _flag_left(pc, left, "text-layer")
    return n


def _paragraphs(segs: list[Seg]) -> list[Seg]:
    """Consecutive missing lines of one column are one block (a dropped paragraph), not one block per line."""
    out: list[Seg] = []
    for s in sorted(segs, key=lambda s: (s.band or "", s.box[1], s.box[0])):
        p = out[-1] if out else None
        h = s.box[3] - s.box[1]
        if (p is not None and p.band == s.band and s.band is None and 0 <= s.box[1] - p.box[3] < 1.2 * h
                and min(p.box[2], s.box[2]) - max(p.box[0], s.box[0]) > 0):
            out[-1] = Seg(p.text + " " + s.text, (min(p.box[0], s.box[0]), p.box[1], max(p.box[2], s.box[2]), s.box[3]))
        else:
            out.append(s)
    return out


def _flag_left(pc: PageChecks, left: list[Seg], what: str) -> None:
    """Missing text that was not restored lowers the confidence of the block it belongs to (or is nearest to)."""
    g = pc.located()
    if g is None:
        return
    per: dict[int, list[Seg]] = {}
    for s in left:
        best, bi = None, None
        for i, r in enumerate(g.regions[:len(pc.page.regions)]):
            if r.bbox is None:
                continue
            b = reread.xyxy(r.bbox)
            d = 0.0 if _inside(s, b) else min(abs(s.cy - b[1]), abs(s.cy - b[3])) + 0.001
            if best is None or d < best:
                best, bi = d, i
        if bi is not None:
            per.setdefault(bi, []).append(s)
    for i, segs in per.items():
        pc.record(pc.page.regions[i], "completeness", "unresolved",
                  f"{len(segs)} {what} line(s) here have no match in the read", sample=[s.text[:60] for s in segs[:3]])


# ---------------------------------------------------------------------------------- crops: the model writes
def _bands(cands: list[Seg], gap: float = 0.025, pad_y: float = 0.02, max_height: float = 0.3) -> list[tuple[reread.Box, list[Seg]]]:
    out: list[tuple[list[float], list[Seg]]] = []
    for s in sorted(cands, key=lambda s: s.cy):
        if out and s.box[1] - out[-1][0][1] < gap and s.box[3] - out[-1][0][0] < max_height:
            out[-1][0][1] = max(out[-1][0][1], s.box[3])
            out[-1][1].append(s)
        else:
            out.append(([s.box[1], s.box[3]], [s]))
    return [((0.0, max(0.0, y[0] - pad_y), 1.0, min(1.0, y[1] + pad_y)), segs) for y, segs in out]


def _crop_lines(text: str) -> list[str]:
    lines = []
    for raw in re.sub(r"</tr\s*>|<br\s*/?>", "\n", text or "", flags=re.I).split("\n"):
        t = re.sub(r"\s+", " ", _TAG.sub(" ", raw)).strip()
        t = re.sub(r"^#{1,6}\s+|^\s*[-*]\s+|\*\*|__", "", t).strip()
        if t and "[illegible" not in t and not t.startswith("!["):
            lines.append(t)
    return lines


def restore_from_crops(pc: PageChecks, cands: list[Seg], how: str, agree_at: float = 70, fuzzy: bool = True,
                       margins_only: bool = False) -> int:
    """``cands`` point at places; the model reads each place again and only its text is inserted."""
    n = 0
    for box, segs in _bands(cands)[: int(pc.opt("complete_max_crops", 3))]:
        text = pc.ask(box, prompts.STRIP_PROMPT_FILE, "complete", zoom=float(pc.opt("complete_zoom", 3.0)))
        if not text:
            continue
        for line in _crop_lines(text):
            read, toks = _read_state(pc)
            k = norm(line)
            if len(k) < 4:
                continue
            best = max(segs, key=lambda s: max(fuzz.partial_ratio(s.key, k), fuzz.ratio(s.key, k)))
            score = fuzz.partial_ratio(best.key, k) if len(best.key) >= 6 else fuzz.ratio(best.key, k)
            if score < agree_at:
                continue  # the crop read shows text the pointer did not point at: not evidence of a gap
            if len(k) > 3 * len(best.key) + 20:
                continue  # a whole paragraph came back for a one-line pointer: the read has it or it is not this line
            if margins_only and best.band is None:
                continue
            probe = Seg(line, (best.box[0], 0.5, best.box[2], 0.51))  # judged as body text: words the read already
            if not absent(probe, read, toks, near_at=85, fuzzy=fuzzy):  # has, in any order, are not a missing line
                continue
            _insert(pc, best, line, how, "line read again from an enlarged crop where OCR or the text layer showed "
                                         "text the page read lacked")
            n += 1
    return n


def restore_scan(pc: PageChecks) -> int:
    page = pc.page
    if page.properties.get("text_layer_exact"):
        return 0
    source = pc.opt("complete_ocr_source") or next((k for k in page.words if k != "pdf" and page.words[k]), None)
    if not source or not page.words.get(source):
        return 0
    text = "\n".join(r.content for r in page.regions)
    if text.count("[illegible") >= int(pc.opt("complete_skip_illegible", 3)):
        return 0  # handwriting or damage: OCR "lines" there are noise
    read, toks = _read_state(pc)
    if not read:
        return 0
    skip = _skip_boxes(pc, kinds=(RegionType.FIGURE,))
    cands = [s for s in ocr_segments(page, source, float(pc.opt("complete_min_conf", 0.75)))
             if absent(s, read, toks, near_at=80, cover_at=0.5, fuzzy=True) and not any(_inside(s, b) for b in skip)]
    if not cands:
        return 0
    if len(cands) > int(pc.opt("complete_max_lines", 12)):
        page.properties["completeness_skipped"] = f"{len(cands)} OCR lines without a match: OCR or the read is unreliable here"
        return 0
    if bool(pc.opt("complete_margins_only", False)):
        # Guard: OCR lines in the body that the read lacks are mostly text inside pictures (screenshots,
        # advertisements), which the read describes instead of transcribing. Only the page margins are restored.
        cands = [s for s in cands if s.band is not None]
        if not cands:
            return 0
        return restore_from_crops(pc, cands, "crop_reread", margins_only=True)
    return restore_from_crops(pc, cands, "crop_reread")


def restore_digital_crop(pc: PageChecks) -> int:
    page = pc.page
    if not page.properties.get("text_layer_exact") or not page.words.get("pdf"):
        return 0
    ocr = next((page.words[k] for k in page.words if k != "pdf" and page.words[k]), [])
    read, toks = _read_state(pc)
    skip = _skip_boxes(pc)
    cands = [s for s in pdf_segments(page) if absent(s, read, toks) and visible(s, ocr) and not any(_inside(s, b) for b in skip)]
    if not cands or len(cands) > int(pc.opt("complete_max_lines", 12)):
        return 0
    return restore_from_crops(pc, cands, "crop_reread", agree_at=85, fuzzy=False)


def run(pc: PageChecks, modes: list[str]) -> None:
    n = 0
    if "text" in modes:
        n += restore_from_text_layer(pc)
    if "crop" in modes:
        n += restore_digital_crop(pc)
    if "scan" in modes:
        n += restore_scan(pc)
    if n:
        pc.page.properties["completeness_restored"] = n
        pc.doc.note("verify", f"page {pc.page.index}: {n} missing line(s) restored")
