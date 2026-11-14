"""``settings.embed_pdf_metadata``: a searchable copy of the input.

The words we placed on the page (the reader's text grounded on word boxes; where a block has none, the OCR
words of the page) are written into the PDF as an invisible text layer (text render mode 3), so the file can be
searched, selected and copied. Pages whose own text layer is usable are left alone. Image inputs become a
one-image-per-page PDF first (``embed_pdf_metadata_dpi`` sets the image resolution written into that PDF).

The layer uses one font object without glyphs (Type0, Identity-H, ToUnicode = the code itself), the same idea
as Tesseract's PDF output: any Unicode text in the Basic Multilingual Plane can be found, nothing is drawn.
Every glyph is 0.5 em wide, so a word is stretched to its box with the horizontal-scaling operator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ...model import Document, Page, Word

OCR_SOURCES = ("rapidocr", "ppocr", "tesseract")
GLYPH_EM = 0.5
_TOUNICODE = (b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
              b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
              b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
              b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
              b"1 beginbfrange\n<0000> <FFFF> <0000>\nendbfrange\n"
              b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")


def wanted(request: Optional[dict[str, Any]]) -> bool:
    return bool(((request or {}).get("settings") or {}).get("embed_pdf_metadata"))


def _same_box(a, b) -> bool:
    return abs(a.left - b.left) < 1e-6 and abs(a.top - b.top) < 1e-6 and abs(a.width - b.width) < 1e-6


def _centre_in(b, boxes) -> bool:
    cx, cy = b.left + b.width / 2, b.top + b.height / 2
    return any(o.left <= cx <= o.left + o.width and o.top <= cy <= o.top + o.height for o in boxes)


def page_words(page: Page) -> tuple[list[tuple[str, Any]], str]:
    """(text, box) for every word of one page and where they came from ("grounded", an OCR source name, "")."""
    out: list[tuple[str, Any]] = []
    for r in page.regions:
        if r.meta.get("merged_into"):
            continue
        run: list[Word] = []

        def flush() -> None:
            if not run:
                return
            first, last = run[0], run[-1]
            if len(run) > 1 and first.char_start is not None and last.char_end is not None \
                    and 0 <= first.char_start < last.char_end <= len(r.content):
                text = r.content[first.char_start:last.char_end]  # "Employer" + "s" on one box -> "Employer's"
                if "<" in text or "\n" in text or len(text) > 80:
                    text = " ".join(w.text for w in run)
            else:
                text = " ".join(w.text for w in run)
            text = " ".join(text.split())
            # the same word placed twice (two blocks grounded on one place): keep the first
            if text and not any(t == text and _centre_in(first.bbox, [b]) for t, b in out):
                out.append((text, first.bbox))
            run.clear()

        for w in r.iter_words():
            if w.bbox is None or not (w.text or "").strip():
                continue
            if run and not _same_box(run[-1].bbox, w.bbox):
                flush()
            run.append(w)
        flush()
    source = "grounded" if out else ""
    placed = [b for _, b in out]
    for name in OCR_SOURCES:  # OCR words where we placed nothing: better found roughly than not at all
        ocr = [w for w in page.words.get(name) or [] if w.bbox is not None and (w.text or "").strip()]
        if not ocr:
            continue
        extra = [(" ".join(w.text.split()), w.bbox) for w in ocr
                 if not _centre_in(w.bbox, placed) and not any(_centre_in(b, [w.bbox]) for b in placed)]
        out += extra
        source = source + ("+" if source and extra else "") + (name if extra else "")
        break
    return out, source


def _font(pdf) -> Any:
    import pikepdf
    cid = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=pikepdf.Name("/ReadanydocInvisible"),
        CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"),
                                         Supplement=0),
        FontDescriptor=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/ReadanydocInvisible"), Flags=5,
            FontBBox=pikepdf.Array([0, -200, 500, 800]), ItalicAngle=0, Ascent=800, Descent=-200, CapHeight=700,
            StemV=80)),
        DW=int(GLYPH_EM * 1000), CIDToGIDMap=pikepdf.Name.Identity))
    return pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=pikepdf.Name("/ReadanydocInvisible"),
        Encoding=pikepdf.Name("/Identity-H"), DescendantFonts=pikepdf.Array([cid]),
        ToUnicode=pdf.make_stream(_TOUNICODE)))


def _hex(text: str) -> str:
    return "".join(f"{ord(ch) if ord(ch) <= 0xFFFF else 0xFFFD:04X}" for ch in text)


def _layer(words: list[tuple[str, Any]], box: tuple[float, float, float, float], rotate: int, font_name: str) -> bytes:
    """Content stream for the invisible words of one page. ``box`` is the visible page box in PDF units."""
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    if rotate in (90, 270):
        vw, vh = ph, pw  # the size the reader saw
    else:
        vw, vh = pw, ph
    ops = ["q", "BT", "3 Tr"]
    for text, b in words:
        if not text or b.width <= 0 or b.height <= 0:
            continue
        # box in viewed coordinates (origin top-left), then back to the unrotated page (origin bottom-left)
        left, bottom = b.left * vw, (b.top + b.height) * vh
        width, height = b.width * vw, b.height * vh
        size = max(height * 0.86, 1.0)
        scale = max(min(width / (GLYPH_EM * size * len(text)) * 100.0, 1000.0), 1.0)
        if rotate == 0:
            m = (1, 0, 0, 1, x0 + left, y1 - bottom)
        elif rotate == 90:
            m = (0, 1, -1, 0, x0 + bottom, y0 + left)
        elif rotate == 180:
            m = (-1, 0, 0, -1, x1 - left, y0 + bottom)
        else:  # 270
            m = (0, -1, 1, 0, x1 - bottom, y1 - left)
        ops.append(f"{font_name} {size:.2f} Tf {scale:.1f} Tz "
                   + " ".join(f"{v:.2f}" for v in m) + f" Tm <{_hex(text + ' ')}> Tj")
    ops += ["ET", "Q"]
    return "\n".join(ops).encode()


def _image_pdf(doc: Document, out: Path, dpi: Optional[int]) -> bool:
    """One page per page image. False when a page has no image."""
    from PIL import Image
    images = []
    for page in doc.pages:
        path = next((p for p in page.renders.values() if p and Path(p).is_file()), None) or doc.meta.get("path")
        if not path or not Path(path).is_file():
            return False
        im = Image.open(path)
        if getattr(im, "n_frames", 1) > 1:
            im.seek((page.original_page or 1) - 1)
        images.append(im.convert("RGB"))
    if not images:
        return False
    res = float(dpi or 150)
    images[0].save(out, "PDF", resolution=res, save_all=True, append_images=images[1:])
    return True


def _rasterise(pdf, n: int, src: str, page_number: int, dpi: Optional[int], password: Optional[str]):
    """Replace page ``n`` of ``pdf`` by a picture of itself (same size). Returns the helper PDF to keep open."""
    import io

    import pikepdf

    from ..intake import render_page
    res = int(min(max(int(dpi or 150), 50), 250))
    im = render_page(src, page_number, dpi=res, password=password).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PDF", resolution=float(res))
    buf.seek(0)
    one = pikepdf.open(buf)
    pdf.pages[n] = one.pages[0]
    return one


def build(doc: Document, out_path: str | Path, dpi: Optional[int] = None,
          password: Optional[str] = None) -> dict[str, Any]:
    """Write the searchable copy of ``doc``'s source to ``out_path``. Returns
    {"path" | None, "pages", "pages_with_layer", "words", "skipped": {reason: n}}."""
    import pikepdf
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {"path": None, "pages": len(doc.pages), "pages_with_layer": 0, "words": 0, "skipped": {}}

    def skip(reason: str) -> None:
        stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1

    src = doc.meta.get("path")
    from_image = doc.file_type != "pdf"
    if from_image:
        if doc.meta.get("native") or not _image_pdf(doc, out_path, dpi):
            return stats  # office files and the like: no page image to carry a layer
        pdf = pikepdf.open(str(out_path), allow_overwriting_input=True)
    else:
        if not src or not Path(str(src)).is_file():
            return stats
        try:
            pdf = pikepdf.open(str(src), password=password or doc.meta.get("_password") or "")
        except Exception:
            return stats
    keep: list[Any] = []  # one-page PDFs whose pages were copied in; open until the save
    with pdf:
        font = None
        for i, page in enumerate(doc.pages):
            n = i if from_image else (page.original_page or page.index + 1) - 1
            if n >= len(pdf.pages):
                skip("page not in file")
                continue
            if not from_image and page.text_layer_usable:
                skip("page already has a text layer")
                continue
            if page.properties.get("rotation_applied") or page.properties.get("skew_applied"):
                skip("page was turned before reading")
                continue
            words, _source = page_words(page)
            if not words:
                skip("no word positions")
                continue
            if not from_image and page.words.get("pdf"):
                # the file has a text layer we judged unusable (old OCR): a second layer on top would garble
                # both, so the page is replaced by its picture first, as Reducto's searchable PDF does
                try:
                    keep.append(_rasterise(pdf, n, str(src), page.original_page or n + 1, dpi, password
                                           or doc.meta.get("_password")))
                    stats["pages_rasterised"] = stats.get("pages_rasterised", 0) + 1
                except Exception:
                    skip("page has an unusable text layer and could not be redrawn")
                    continue
            pg = pdf.pages[n]
            box = pg.obj.get("/CropBox") or pg.mediabox
            bx = [float(v) for v in box]
            rect = (min(bx[0], bx[2]), min(bx[1], bx[3]), max(bx[0], bx[2]), max(bx[1], bx[3]))
            rotate = int(pg.obj.get("/Rotate") or 0) % 360
            if font is None:
                font = _font(pdf)
            name = pg.add_resource(font, pikepdf.Name.Font, prefix="DFInv")
            pg.contents_add(pdf.make_stream(b"q"), prepend=True)  # shield from whatever state the page leaves
            pg.contents_add(pdf.make_stream(b"Q\n" + _layer(words, rect, rotate, str(name))))
            stats["pages_with_layer"] += 1
            stats["words"] += len(words)
        if not from_image and not stats["pages_with_layer"]:
            return stats  # nothing to add: the caller links the original file
        pdf.save(str(out_path))
    for k in keep:
        k.close()
    stats["path"] = str(out_path)
    return stats
