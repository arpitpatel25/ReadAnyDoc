"""Stage 1, Intake: sniff the file type and turn the input into pages.

- PDF (pypdfium2): pages, password, page_range, properties; link annotations and comments (pikepdf)
  when ``formatting.include`` asks for ``hyperlinks`` / ``comments``.
- Images (Pillow): PNG, JPG, GIF, BMP, WEBP, multi-page TIFF. HEIC is decoded to PNG first by whatever
  decoder the machine has (``intake/heic.py``); 415 when there is none.
- Digital-native files (DOCX, XLSX/XLSM, CSV, TXT, PPTX, EML, MSG) are read straight into blocks by
  ``readanydoc.intake``: no rendering and no model calls. Their pages are parked in
  ``doc.meta["_native_pages"]`` so the model stages (triage, layout, read, verify, ground) see an
  empty document and do nothing; Assemble (or Shape) puts them back.
- Legacy and foreign office formats (DOC, XLS, PPT, RTF, ODT, ...) are converted by LibreOffice, or by
  macOS ``textutil`` for the word-processing ones (``intake/legacy.py``); 415 when no converter exists.
- A password-protected PDF opened with ``settings.document_password`` is decrypted to a working copy for
  the later stages; the password itself is never stored in the document.
- ``settings.page_range`` that selects no page is a 400 INVALID_CONFIG, as on Reducto.

Stage options: ``request``, the raw Reducto parse request (``shaping.options.apply_request``);
``sheet_titles`` (default True), start each workbook sheet with a Title block holding its name.
"""

from __future__ import annotations

from ..readers.sizing import pdfium_locked

from pathlib import Path
from typing import Any, Optional

from .. import paths
from ..intake import IMAGE_KINDS, LEGACY_KINDS, NATIVE_KINDS, IntakeError, unsupported
from ..intake.errors import damaged, no_pages_in_range
from ..intake.sniff import IMAGE_MAGIC, sniff  # noqa: F401  (re-exported)
from ..model import Document, Page
from .base import PipelineContext, Stage, register_stage
from .shaping.options import Options, resolve
from .shaping.page_range import PageRangeError, resolve_page_range

MAX_IMAGE_PIXELS = 50_000_000  # Reducto: images up to 50 MP / 15,000 px per side
MAX_IMAGE_SIDE = 15_000
NATIVE_KEY = "_native_pages"


@pdfium_locked
def render_page(pdf_path: str | Path, page_number: int = 1, dpi: int = 150, password: str | None = None):
    """Render one PDF page to a PIL image (used by live readers; cached runs never render)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path), password=password)
    try:
        return pdf[page_number - 1].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()


class _LazyReader:
    """Stands in for ``ctx.reader`` until something really uses it; then becomes the real reader."""

    def __init__(self, spec: str):
        self._spec = spec
        self._real = None

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        if self.__dict__.get("_real") is None:
            from ..readers import make_reader
            self.__dict__["_real"] = make_reader(self.__dict__["_spec"])
        return getattr(self.__dict__["_real"], name)


def restore_native_pages(doc: Document) -> None:
    """Put the parked pages of a digital-native document back (called by Assemble and Shape)."""
    pages = doc.meta.pop(NATIVE_KEY, None)
    if pages is not None:
        doc.pages = pages


def load_native(path: Path, kind: str, opts: Options,
                sheet_titles: bool = True) -> tuple[list[Page], dict[str, Any], dict[str, Any]]:
    """(pages, document properties, meta) for a digital-native file."""
    from ..intake import docx_reader, pptx_reader, sheets, text
    # Reducto shows a document's own hyperlinks as markdown links without being asked (measured on DOCX)
    include = list(dict.fromkeys([*opts.include, "hyperlinks"]))
    if kind == "docx":
        return docx_reader.load_docx(path, include)
    if kind == "pptx":
        return pptx_reader.load_pptx(path, include)
    if kind == "txt":
        return text.load_txt(path)
    if kind == "eml":
        return text.load_eml(path)
    if kind == "msg":
        from ..intake import msg
        return msg.load_msg(path)
    if kind in ("xlsx", "csv"):
        book, info = sheets.read_xlsx(path, opts.spreadsheet) if kind == "xlsx" else sheets.read_csv(path)
        pages, meta = sheets.load_sheets(book, opts.spreadsheet, opts.page_range, titles=sheet_titles and kind == "xlsx")
        meta.update({k: v for k, v in info.items() if k != "properties"})
        return pages, info.get("properties") or {}, meta
    raise unsupported(kind)


@register_stage("intake")
class Intake(Stage):
    required = True

    def run(self, doc: Document, ctx: PipelineContext) -> Document:
        src = doc.source or ""
        path = Path(src)
        if not path.exists():  # a cached bench stem
            stem = Path(src).stem if src.lower().endswith((".pdf", ".png")) else src
            pdf = paths.bench_pdf(stem)
            img = paths.bench_image(stem)
            if pdf is None and img is None:
                raise IntakeError(f"input not found (neither a file nor a cached stem): {src!r}")
            path = pdf or img  # type: ignore[assignment]
            doc.stem = stem
        else:
            doc.stem = path.stem
        doc.meta["path"] = str(path)
        kind = sniff(path)
        opts = resolve(ctx.config.parse, self.opt("request"))
        password = ctx.config.parse.document_password
        if kind in LEGACY_KINDS:
            from ..intake import legacy
            path, kind = legacy.convert(path, kind)
            doc.meta["converted_from"] = doc.meta["path"]
            doc.meta["path"] = str(path)
        doc.file_type = kind
        if kind == "pdf":
            self._pdf(doc, path, opts, password)
            if password and doc.meta.get("encrypted"):
                # Later stages (word boxes, page renders, figure crops) open the file themselves and know no
                # password; the password must not travel in the stored document either. They get a decrypted
                # working copy instead.
                plain = self._decrypt(doc, path, password)
                if plain is not None:
                    doc.meta["path"] = str(plain)
        elif kind in IMAGE_KINDS:
            self._image(doc, path, opts)
        elif kind in NATIVE_KINDS:
            self._native(doc, path, kind, opts, ctx)
        elif kind == "heic":
            from ..intake import heic
            png = heic.to_png(path)
            doc.meta["converted_from"] = doc.meta["path"]
            doc.meta["path"] = str(png)
            doc.file_type = "png"
            self._image(doc, png, opts)
        else:
            raise unsupported(kind)
        cached_img = paths.bench_image(doc.stem) if doc.stem else None
        if cached_img and len(doc.pages) == 1:
            doc.pages[0].renders.setdefault("150", str(cached_img))
        return doc

    # ---- PDF ----
    @pdfium_locked
    def _pdf(self, doc: Document, path: Path, opts: Options, password: Optional[str]) -> None:
        import pypdfium2 as pdfium
        try:
            pdf = pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as e:
            if "password" in str(e).lower():
                raise IntakeError(f"cannot open PDF ({e}); password needed or wrong") from e
            raise IntakeError(f"cannot open PDF ({e}); the file is damaged") from e
        try:
            n = len(pdf)
            if password:
                try:  # does the file open without the password? then it is not encrypted
                    pdfium.PdfDocument(str(path)).close()
                except pdfium.PdfiumError:
                    doc.meta["encrypted"] = True
            try:
                wanted = resolve_page_range(opts.page_range, n)
            except PageRangeError as e:
                raise IntakeError(str(e)) from e
            if not wanted:
                raise no_pages_in_range(n)
            for p in wanted:
                page = pdf[p - 1]
                w, h = page.get_size()
                doc.pages.append(Page(index=len(doc.pages), original_page=p, width=float(w), height=float(h),
                                      unit="pt", rotation=int(page.get_rotation() or 0)))
            try:
                from ..intake.pdf import normalise_properties
                meta = pdf.get_metadata_dict(skip_empty=True)
                doc.properties.update({k.lower(): v for k, v in meta.items()})
                doc.properties.update(normalise_properties(meta))
            except Exception:  # metadata is optional
                pass
            doc.meta["total_pages"] = n
        finally:
            pdf.close()
        if {"hyperlinks", "comments"} & set(opts.include):
            try:
                from ..intake.pdf import read_annotations
                found = read_annotations(path, [p.original_page for p in doc.pages], password)
                for page in doc.pages:
                    if page.original_page in found:
                        page.properties["annotations"] = found[page.original_page]
            except Exception as e:  # annotations are extras: never fail the parse for them
                doc.note(self.name, f"PDF annotations not read ({type(e).__name__}: {e})", "warning")

    def _decrypt(self, doc: Document, path: Path, password: str) -> Optional[Path]:
        try:
            import pikepdf
            out = paths.cache_dir() / "decrypted" / f"{doc.id}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            with pikepdf.open(str(path), password=password) as pdf:
                pdf.save(str(out))
            return out
        except Exception as e:
            doc.note(self.name, f"could not write a decrypted working copy ({type(e).__name__}); later stages "
                                "may not be able to open the file", "warning")
            return None

    # ---- images ----
    def _image(self, doc: Document, path: Path, opts: Options) -> None:
        from PIL import Image
        try:
            im = Image.open(path)
        except Exception as e:
            raise damaged("image", e) from e
        with im:
            frames = getattr(im, "n_frames", 1)
            try:
                wanted = resolve_page_range(opts.page_range, frames)
            except PageRangeError as e:
                raise IntakeError(str(e)) from e
            if not wanted:
                raise no_pages_in_range(frames)
            for n in wanted:
                im.seek(n - 1)
                w, h = im.size
                if w * h > MAX_IMAGE_PIXELS or max(w, h) > MAX_IMAGE_SIDE:
                    raise IntakeError(f"image of {w}x{h} px is not supported: the limit is 50 megapixels and "
                                      f"{MAX_IMAGE_SIDE} px per side")
                page = Page(index=len(doc.pages), original_page=n, width=float(w), height=float(h), unit="px")
                dpi = im.info.get("dpi", (150, 150))[0] or 150
                key = str(int(round(float(dpi))))
                if frames == 1 and (im.format or "").upper() in ("PNG", "JPEG", "GIF", "WEBP"):
                    page.renders[key] = str(path)
                elif self.opt("save_frames", True):  # readers need one image file per page
                    out = paths.cache_dir() / "renders" / f"{doc.id}_f{n}.png"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    im.convert("RGB").save(out)
                    page.renders[key] = str(out)
                doc.pages.append(page)
        doc.meta["total_pages"] = frames

    # ---- digital-native ----
    def _native(self, doc: Document, path: Path, kind: str, opts: Options, ctx: PipelineContext) -> None:
        pages, props, meta = load_native(path, kind, opts, sheet_titles=bool(self.opt("sheet_titles", True)))
        if kind in ("docx", "pptx", "txt", "eml", "msg") and opts.page_range is not None:
            try:
                wanted = set(resolve_page_range(opts.page_range, len(pages)))
            except PageRangeError as e:
                raise IntakeError(str(e)) from e
            meta.setdefault("total_pages", len(pages))
            if not wanted:
                raise no_pages_in_range(len(pages))
            pages = [p for p in pages if p.original_page in wanted]
            for i, p in enumerate(pages):
                p.index = i
                for r in p.regions:
                    if r.bbox is not None:
                        r.bbox.page = i
                    for c in r.cells:
                        if c.bbox is not None:
                            c.bbox.page = i
        doc.properties.update(props)
        doc.meta.update(meta)
        doc.meta.setdefault("total_pages", len(pages))
        doc.meta["native"] = True
        if kind == "xlsx" and opts.spreadsheet.clustering == "accurate":
            doc.note(self.name, "spreadsheet.clustering=accurate needs a model; used the rule-based 'fast' clustering")
        # Park the pages so the model stages have nothing to do; Assemble / Shape restore them.
        later = any(ctx.config.stage(s).enabled for s in ("assemble", "shape"))
        if later:
            doc.meta[NATIVE_KEY] = pages
            doc.pages = []
            if getattr(ctx, "_reader", True) is None:
                # Read asks for ctx.reader even when there are no pages, and building some readers fails
                # (cached outputs absent) or costs start-up time. Hand it one that is built on first use.
                ctx._reader = _LazyReader(ctx.config.reader)
        else:
            doc.pages = pages
