"""Word sources of one page (PDF text layer, PP-OCR, Tesseract) prepared for alignment.

A ``Source`` holds the words that have a box, their tokens, and reading "segments": runs of words
on one baseline with no column-sized gap between them. Segments are what keeps candidates in a
usable order on multi-column pages (pdfplumber's own lines run across columns).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Optional

from ... import paths
from ...model import BBox, Confidence, Document, Page, Word
from .tokens import tokens_of


class Source:
    def __init__(self, name: str, words: list[Word], aspect: float = 1.0, trusted: bool = True):
        self.name = name
        self.trusted = trusted
        self.words: list[Word] = [w for w in words if w.bbox is not None and w.text and w.text.strip()]
        self.aspect = aspect or 1.0  # page height / page width, to compare x and y distances
        self.tokens: list[str] = []  # flat token norms
        self.tok_word: list[int] = []  # flat token -> word index
        self.word_toks: list[list[int]] = []  # word -> flat token indices
        for wi, w in enumerate(self.words):
            ids = []
            for t in tokens_of(w.text):
                ids.append(len(self.tokens))
                self.tokens.append(t)
                self.tok_word.append(wi)
            self.word_toks.append(ids)
        self.claimed: set[int] = set()  # flat token indices already cited by a region
        heights = [w.bbox.height for w in self.words if w.bbox.height > 0]
        self.median_height = statistics.median(heights) if heights else 0.01
        self.segment_of: list[int] = [0] * len(self.words)
        self.order: list[int] = []  # word indices in reading order (segment by segment)
        self._segment()

    # ---- geometry ----
    def _segment(self) -> None:
        """Lines by vertical overlap, split at column-sized horizontal gaps, ordered top-down."""
        self.segments: list[list[int]] = []
        if not self.words:
            return
        if self.name != "pdf" and all(w.line is not None for w in self.words):
            # OCR engines report their own text lines, which are already column-local
            by_line: dict[int, list[int]] = {}
            for i, w in enumerate(self.words):
                by_line.setdefault(w.line, []).append(i)
            segs = list(by_line.values())
            for seg in segs:
                seg.sort(key=lambda i: self.words[i].bbox.left)
            self._finish(segs)
            return
        idx = sorted(range(len(self.words)), key=lambda i: (self.words[i].bbox.top + self.words[i].bbox.height / 2,
                                                            self.words[i].bbox.left))
        lines: list[list[int]] = []
        bands: list[tuple[float, float]] = []  # (centre, height) of each open line
        for i in idx:
            b = self.words[i].bbox
            cy = b.top + b.height / 2
            placed = False
            for k in range(len(lines) - 1, max(len(lines) - 40, -1), -1):
                lc, lh = bands[k]
                tol = 0.45 * max(min(lh, b.height), 0.5 * max(lh, b.height), 1e-4)
                if abs(cy - lc) <= tol:
                    lines[k].append(i)
                    n = len(lines[k])
                    bands[k] = (lc + (cy - lc) / n, lh + (b.height - lh) / n)
                    placed = True
                    break
            if not placed:
                lines.append([i])
                bands.append((cy, b.height))
        segs: list[list[int]] = []
        for line in lines:
            line.sort(key=lambda i: self.words[i].bbox.left)
            cur = [line[0]]
            for i in line[1:]:
                prev, b = self.words[cur[-1]].bbox, self.words[i].bbox
                gap = b.left - prev.right
                em = max(prev.height, b.height) * self.aspect  # one em expressed in x units
                if gap > 1.6 * em:
                    segs.append(cur)
                    cur = [i]
                else:
                    cur.append(i)
            segs.append(cur)
        self._finish(segs)

    def _finish(self, segs: list[list[int]]) -> None:
        def key(seg: list[int]) -> tuple[float, float]:
            cy = statistics.median(self.words[i].bbox.top + self.words[i].bbox.height / 2 for i in seg)
            return (round(cy / max(self.median_height * 0.6, 1e-4)), self.words[seg[0]].bbox.left)
        segs.sort(key=key)
        for si, seg in enumerate(segs):
            for i in seg:
                self.segment_of[i] = si
        self.order = [i for seg in segs for i in seg]
        self.segments = segs

    def words_in(self, box: BBox, pad_x: float, pad_y: float) -> list[int]:
        """Word indices (reading order) whose centre lies inside the padded box."""
        l, r, t, b = box.left - pad_x, box.right + pad_x, box.top - pad_y, box.bottom + pad_y
        out = []
        for i in self.order:
            wb = self.words[i].bbox
            cx, cy = wb.left + wb.width / 2, wb.top + wb.height / 2
            if l <= cx <= r and t <= cy <= b:
                out.append(i)
        return out

    def free_tokens(self, word_ids: list[int]) -> list[int]:
        return [t for wi in word_ids for t in self.word_toks[wi] if t not in self.claimed]


# ---- Tesseract (last resort; needs the `tesseract` binary, which is optional) ----

def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def tesseract_words(image_path: str | Path, page_index: int = 0, lang: str = "eng", psm: int = 3,
                    source: str = "tesseract") -> list[Word]:
    """Tesseract word boxes for a page image, normalised. Disk-cached; [] when unavailable.
    ``psm`` 3 is automatic page segmentation; 6 (one uniform block) and 11 (sparse text) find words
    on pages where the automatic layout analysis gives up (faint typescript, forms with few words)."""
    p = Path(image_path)
    if not p.exists():
        return []
    real = p.resolve()  # Leptonica rewrites paths that start with /tmp (macOS: /tmp -> /private/tmp); give it the real path
    tag = lang if psm == 3 else f"{lang}|psm{psm}"
    key = hashlib.sha1(f"{p.resolve()}|{p.stat().st_mtime_ns}|{tag}".encode()).hexdigest()[:20]
    cache = paths.cache_dir() / "tesseract" / f"{key}.json"
    if not cache.exists():
        # by content as well: the same page uploaded again (another server, another job) is not read twice
        h = hashlib.sha1(real.read_bytes())
        h.update(f"|{tag}".encode())
        by_content = paths.cache_dir() / "tesseract" / f"c{h.hexdigest()[:24]}.json"
        if by_content.exists() or os.environ.get("READANYDOC_OCR_CACHE_BY_PATH") != "1":
            cache = by_content
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        exe = shutil.which("tesseract")
        if exe is None:
            return []
        try:
            # bytes, decoded leniently: an error message that quotes binary data must not raise UnicodeDecodeError
            out = subprocess.run([exe, str(real), "-", "--psm", str(psm), "-l", lang, "tsv"], capture_output=True,
                                 timeout=180).stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return []
        rows, W, H = [], 0.0, 0.0
        for ln in out.splitlines()[1:]:
            f = ln.split("\t")
            if len(f) < 12:
                continue
            try:
                level, block, par, line = int(f[0]), int(f[2]), int(f[3]), int(f[4])
                x, y, w, h, conf = float(f[6]), float(f[7]), float(f[8]), float(f[9]), float(f[10])
            except ValueError:
                continue
            if level == 1:
                W, H = w, h
            elif level == 5 and f[11].strip() and W and H and conf >= 0:
                rows.append([f[11], x / W, y / H, (x + w) / W, (y + h) / H, block * 1_000_000 + par * 1000 + line,
                             round(conf / 100.0, 4)])
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # another request may read it now
        tmp.write_text(json.dumps(rows))
        tmp.replace(cache)
    lines: dict[int, int] = {}
    words = []
    for t, x0, y0, x1, y1, ln, conf in rows:
        words.append(Word(text=t, bbox=BBox.from_xyxy(x0, y0, x1, y1, page_index), source=source,
                          line=lines.setdefault(ln, len(lines)), confidence=Confidence.of(conf)))
    return words


def page_image(doc: Document, page: Page) -> Optional[str]:
    """A raster of the page for Tesseract: an existing render, the cached bench image, or the file
    itself when the input was an image."""
    for dpi in sorted(page.renders, key=lambda d: -int(d) if str(d).isdigit() else 0):
        if Path(page.renders[dpi]).exists():
            return page.renders[dpi]
    if doc.stem and len(doc.pages) == 1:
        img = paths.bench_image(doc.stem)
        if img:
            return str(img)
    path = doc.meta.get("path")
    from ...intake.sniff import IMAGE_KINDS
    if path and doc.file_type in ("image", *IMAGE_KINDS) and len(doc.pages) == 1 and Path(path).exists():
        return str(path)
    return None


def source_priority(page: Page) -> list[tuple[str, bool]]:
    """(source name, trusted) in the order they are tried. A text layer that failed triage comes
    last and untrusted: only long exact runs of it may be cited."""
    out: list[tuple[str, bool]] = []
    if page.text_layer_usable and page.words.get("pdf"):
        out.append(("pdf", True))
    if page.words.get("ppocr"):
        out.append(("ppocr", True))
    if page.words.get("rapidocr"):  # live PP-OCR next to Tesseract (words "tesseract+rapidocr"): tried first
        out.append(("rapidocr", True))
    if page.words.get("tesseract"):
        out.append(("tesseract", True))
    for name, words in page.words.items():
        if words and name not in ("pdf", "ppocr", "rapidocr", "tesseract"):
            out.append((name, True))
    if not page.text_layer_usable and page.words.get("pdf"):
        out.append(("pdf", False))
    return out


# ---- PDF text layer on pages whose visible area is not the whole media box ----

def fix_cropped_pdf_words(doc: Document, page: Page) -> bool:
    """Triage normalises text-layer words by the media box (ignoring its origin, and clamping what
    falls outside 0-1); the page image, and so every other coordinate, shows the crop box. Where the
    two differ, the words of this page are read again in the visible page's coordinates and those
    outside it are dropped. Done once per page (``page.properties["pdf_words_crop"]``).
    Returns True when the words were replaced."""
    if "pdf_words_crop" in page.properties or not page.words.get("pdf"):
        return False
    page.properties["pdf_words_crop"] = None
    path = doc.meta.get("path") or doc.source
    if not path or doc.file_type != "pdf" or not Path(str(path)).exists():
        return False
    try:
        import logging

        import pdfplumber
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        with pdfplumber.open(str(path)) as pdf:
            pg = pdf.pages[page.original_page - 1]
            W, H = float(pg.width), float(pg.height)
            mx0, mtop, mx1, mbot = (float(v) for v in pg.bbox)
            cx0, ctop, cx1, cbot = (float(v) for v in pg.cropbox)
            x0, top, x1, bot = max(mx0, cx0), max(mtop, ctop), min(mx1, cx1), min(mbot, cbot)
            if int(pg.rotation or 0) or x1 - x0 <= 1 or bot - top <= 1 or not W or not H:
                return False
            if max(abs(x0), abs(top), abs(x1 - x0 - W), abs(bot - top - H)) < 0.75:
                return False  # the visible page is the media box at the origin: nothing to do
            raw = pg.extract_words(keep_blank_chars=False, use_text_flow=False)
    except Exception:
        return False
    kept: list[Word] = []
    line, prev_top = -1, None
    for w in raw:
        if prev_top is None or abs(w["top"] - prev_top) > 3:
            line += 1
        prev_top = w["top"]
        l, r = (w["x0"] - x0) / (x1 - x0), (w["x1"] - x0) / (x1 - x0)
        t, bt = (w["top"] - top) / (bot - top), (w["bottom"] - top) / (bot - top)
        if not (0 <= (l + r) / 2 <= 1 and 0 <= (t + bt) / 2 <= 1):
            continue
        kept.append(Word(text=w["text"], bbox=BBox.from_xyxy(l, t, r, bt, page.index), source="pdf", line=line))
    page.properties["pdf_words_crop"] = {"visible": [round(v, 2) for v in (x0, top, x1, bot)],
                                         "media": [round(W, 2), round(H, 2)],
                                         "dropped": len(raw) - len(kept)}
    page.words["pdf"] = kept
    return True
