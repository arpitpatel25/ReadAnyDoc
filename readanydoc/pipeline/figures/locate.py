"""Where the pictures are on a page, without a layout model.

The reader describes a figure in words but gives no box. This module finds the graphics on the page
image itself: everything that is ink and is not text. Text is removed first (the words of the PDF
text layer when there is one, and the boxes of every located text or table block), what is left is
grouped into connected blobs on a coarse grid, and the blobs are matched to the page's figure
blocks. Pillow only; no model call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

GRID_W = 160  # cells across the page
MIN_AREA = 0.0012  # blobs under 0.12% of the page are specks, rules or stray marks
MERGE_GAP = 0.035  # blobs closer than this (share of page width) belong to one figure
HOLLOW_MIN = 0.03  # blobs from this size up are checked for being a bordered box
SIBLING_GAP = 0.2  # side-by-side drawings this close share one description
HINT_REACH = 0.45  # a blob further than this from a block's rough position is not its figure
FRAME_TEXT = 0.12  # a blob whose box is this much text is a table or form frame
INK_FILL = 40  # of 255: share of dark pixels that makes a grid cell "ink" (about 16%)

Box = tuple[float, float, float, float]  # left, top, width, height in 0-1 page coordinates


@dataclass
class Blob:
    left: float
    top: float
    right: float
    bottom: float
    cells: int = 0

    @property
    def box(self) -> Box:
        return (self.left, self.top, self.right - self.left, self.bottom - self.top)

    @property
    def area(self) -> float:
        return (self.right - self.left) * (self.bottom - self.top)

    def gap(self, o: "Blob") -> float:
        dx = max(o.left - self.right, self.left - o.right, 0.0)
        dy = max(o.top - self.bottom, self.top - o.bottom, 0.0)
        return max(dx, dy)

    def join(self, o: "Blob") -> "Blob":
        return Blob(min(self.left, o.left), min(self.top, o.top), max(self.right, o.right),
                    max(self.bottom, o.bottom), self.cells + o.cells)


def ink_blobs(image, text_boxes: Sequence[Box] = (), grid_w: int = GRID_W,
              outlines: Sequence[Box] = ()) -> list[Blob]:
    """Connected blobs of non-text ink on ``image`` (a PIL image), largest first. The border of each
    box in ``outlines`` is erased first, so a drawing inside a bordered box stands alone."""
    from PIL import Image, ImageDraw, ImageFilter
    g = image.convert("L")
    w, h = g.size
    hist = g.histogram()
    paper = max(range(128, 256), key=lambda v: hist[v]) if sum(hist[128:]) else 255  # the page's background grey
    cut = max(paper - 48, 60)
    ink = g.point(lambda v: 255 if v < cut else 0)
    draw = ImageDraw.Draw(ink)
    for left, top, bw, bh in text_boxes:  # erase text
        pad_x, pad_y = 0.004 * w, 0.003 * h
        draw.rectangle([left * w - pad_x, top * h - pad_y, (left + bw) * w + pad_x, (top + bh) * h + pad_y], fill=0)
    cell = w / grid_w
    for left, top, bw, bh in outlines:  # erase frame borders
        draw.rectangle([left * w - cell, top * h - cell, (left + bw) * w + cell, (top + bh) * h + cell],
                       outline=0, width=int(round(3.6 * cell)))
    gw = grid_w
    gh = max(int(round(gw * h / w)), 1)
    small = ink.resize((gw, gh), Image.BOX).point(lambda v: 255 if v >= INK_FILL else 0)
    grown = small.filter(ImageFilter.MaxFilter(3))  # bridge one-cell gaps
    px, raw = grown.load(), small.load()
    seen = bytearray(gw * gh)
    blobs: list[Blob] = []
    for y0 in range(gh):
        for x0 in range(gw):
            if not px[x0, y0] or seen[y0 * gw + x0]:
                continue
            stack = [(x0, y0)]
            seen[y0 * gw + x0] = 1
            x1 = x2 = x0
            y1 = y2 = y0
            cells = 0
            while stack:
                x, y = stack.pop()
                if raw[x, y]:
                    cells += 1
                    x1, x2, y1, y2 = min(x1, x), max(x2, x), min(y1, y), max(y2, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < gw and 0 <= ny < gh and px[nx, ny] and not seen[ny * gw + nx]:
                        seen[ny * gw + nx] = 1
                        stack.append((nx, ny))
            if cells:
                blobs.append(Blob(x1 / gw, y1 / gh, (x2 + 1) / gw, (y2 + 1) / gh, cells))
    return sorted(blobs, key=lambda b: -b.area)


def text_share(b: Blob, text_boxes: Sequence[Box]) -> float:
    """Share of the blob's box that is covered by text boxes: high for a table or form frame, low for a drawing."""
    if b.area <= 0:
        return 0.0
    covered = 0.0
    for left, top, w, h in text_boxes:
        iw = min(b.right, left + w) - max(b.left, left)
        ih = min(b.bottom, top + h) - max(b.top, top)
        if iw > 0 and ih > 0:
            covered += iw * ih
    return covered / b.area


def _thin(b: Blob, gw: int = GRID_W) -> bool:
    """A rule or a border line: up to three cells thick and long."""
    short, long_ = sorted((b.right - b.left, b.bottom - b.top))
    return short <= 2.2 / gw or (short <= 3.6 / gw and long_ >= 8 * short)


def _inside(b: Blob, f: Blob) -> bool:
    return f.left <= b.left and b.right <= f.right and f.top <= b.top and b.bottom <= f.bottom


def candidates(image, text_boxes: Sequence[Box]) -> list[Blob]:
    """Blobs that can be pictures. A frame is not one: a blob whose box is largely text (a table, a form), or
    a bordered box that holds some text and falls apart into small pieces once its border is erased. What
    stands inside a frame is looked at on its own."""
    first = [b for b in ink_blobs(image, text_boxes) if not _thin(b)]
    out = [b for b in first if text_share(b, text_boxes) < FRAME_TEXT]
    frames = [b for b in first if text_share(b, text_boxes) >= FRAME_TEXT and b.area >= 4 * MIN_AREA]
    maybe = [b for b in out if b.area >= HOLLOW_MIN and text_share(b, text_boxes) >= 0.02]
    if not frames and not maybe:
        return out
    inner = [b for b in ink_blobs(image, text_boxes, outlines=[f.box for f in frames + maybe]) if not _thin(b)]
    for f in maybe:
        parts = [b.area for b in inner if _inside(b, f)]
        if not parts or max(parts) < 0.4 * f.area:
            out.remove(f)
            frames.append(f)
    for b in inner:
        holders = [f for f in frames if _inside(b, f)]
        if holders and b.area >= MIN_AREA and text_share(b, text_boxes) < FRAME_TEXT \
                and not any(b.area >= 0.75 * f.area for f in holders):
            out.append(b)
    return out


def figure_boxes(image, text_boxes: Sequence[Box], wanted: int,
                 hints: Optional[Sequence[Optional[Box]]] = None,
                 spare: Optional[list[Box]] = None) -> list[Optional[Box]]:
    """Boxes for ``wanted`` figure blocks, in the order of ``hints`` (each block's rough position, or
    None). Returns None for a block no graphic could be matched to."""
    if wanted <= 0:
        return []
    blobs = candidates(image, text_boxes)
    # parts of one drawing (bars, legend swatches, axis labels) lie close together: join them
    merged = True
    while merged and len(blobs) > 1:
        merged = False
        best = None
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                d = blobs[i].gap(blobs[j])
                if d <= MERGE_GAP and (best is None or d < best[0]):
                    best = (d, i, j)
        if best is not None:
            _, i, j = best
            blobs[i] = blobs[i].join(blobs[j])
            del blobs[j]
            merged = True
    small = [b for b in blobs if b.area < MIN_AREA]
    blobs = [b for b in blobs if b.area >= MIN_AREA]
    ranked = sorted(blobs, key=lambda b: -b.area)
    blobs, rest = ranked[:max(wanted * 2, wanted + 2)], ranked[max(wanted * 2, wanted + 2):]
    if spare is not None:
        spare.extend(b.box for b in small + rest)
    hints = list(hints or [None] * wanted)
    out: list[Optional[Box]] = [None] * wanted
    free = list(blobs)
    # blocks with a position hint take the nearest blob; the rest take what is left in reading order
    order = sorted(range(wanted), key=lambda i: hints[i] is None)
    for i in order:
        if not free:
            break
        if hints[i] is not None:
            hl, ht, hw, hh = hints[i]  # type: ignore[misc]
            cx, cy = hl + hw / 2, ht + hh / 2
            pick = min(free, key=lambda b: abs((b.left + b.right) / 2 - cx) + abs((b.top + b.bottom) / 2 - cy))
            if abs((pick.left + pick.right) / 2 - cx) + abs((pick.top + pick.bottom) / 2 - cy) > HINT_REACH:
                continue  # nothing drawn near where the reader put this figure
        else:
            pick = min(free, key=lambda b: (round(b.top, 1), b.left))
        out[i] = pick.box
        free.remove(pick)
    # one description often covers drawings that stand side by side (two pie charts): a sizeable blob
    # left over on the same band as a matched figure belongs to it
    taken = [Blob(b[0], b[1], b[0] + b[2], b[1] + b[3]) if b else None for b in out]
    for b in free:
        best = None
        for i, t in enumerate(taken):
            if t is None or b.area < 0.25 * t.area:
                continue
            overlap = min(t.bottom, b.bottom) - max(t.top, b.top)
            if overlap >= 0.6 * min(t.bottom - t.top, b.bottom - b.top) and (best is None or t.gap(b) < best[0]):
                best = (t.gap(b), i)
        if best is not None and best[0] <= SIBLING_GAP:
            taken[best[1]] = taken[best[1]].join(b)  # type: ignore[union-attr]
            out[best[1]] = taken[best[1]].box  # type: ignore[union-attr]
        elif spare is not None:
            spare.append(b.box)  # unmatched ink (legend swatches, marks): the caller may attach it
    return out
