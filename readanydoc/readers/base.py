"""Model interface.

``Reader.read(image, hint, context) -> ReadResult``. ``image`` is a page image or a region crop
(path or PNG/JPEG bytes); cached readers ignore it and use ``context.stem``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from ..model import BBox, RegionType, Word

ImageInput = Union[str, Path, bytes, None]


class Usage(BaseModel):
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    seconds: Optional[float] = None
    cached: bool = False  # True when replayed from disk (no model call made)
    batch: bool = False  # True when the call went through the Message Batches API (billed at half price)


class ReadContext(BaseModel):
    """Everything a reader may need besides the pixels."""

    stem: Optional[str] = None  # cache key (cached adapters)
    page_index: int = 0
    region_id: Optional[str] = None
    bbox: Optional[BBox] = None  # set when ``image`` is a crop of this region
    prompt: Optional[str] = None  # override the adapter's default prompt
    max_tokens: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ReadBlock(BaseModel):
    """Optional region-level output when the model returns its own blocks/boxes."""

    text: str
    bbox: Optional[BBox] = None
    type: Optional[RegionType] = None
    native_label: Optional[str] = None


class ReadResult(BaseModel):
    text: str = ""
    format: Literal["markdown", "html", "text"] = "markdown"
    blocks: list[ReadBlock] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stop_reason: Optional[str] = None  # "end_turn" | "max_tokens" | "refusal" | ...
    truncated: bool = False
    refused: bool = False
    raw: Any = None  # provider response, for debugging only


class ReaderError(RuntimeError):
    pass


class Reader(ABC):
    """One swappable model behind one interface."""

    name: str = "reader"
    #: "page" readers transcribe whole pages; "region" readers are meant for crops.
    granularity: Literal["page", "region"] = "page"
    #: False for adapters that replay stored outputs and never look at pixels.
    needs_image: bool = True

    @abstractmethod
    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult: ...


class WordBoxProvider(ABC):
    """Word boxes for a page (PP-OCR; cached locally, HTTP sidecar on GPU later)."""

    name: str = "words"

    @abstractmethod
    def words(self, image: ImageInput, context: Optional[ReadContext] = None) -> list[Word]: ...


def strip_code_fence(text: str) -> str:
    """Models sometimes wrap the whole answer in ``` fences; remove one outer fence."""
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("\n", 1)
        if len(parts) == 2:
            return parts[1].rsplit("```", 1)[0].strip("\n")
    return text


def image_bytes(image: ImageInput) -> bytes:
    if image is None:
        raise ReaderError("this reader needs an image")
    if isinstance(image, bytes):
        return image
    return Path(image).read_bytes()


API_MAX_BYTES = 5 * 1024 * 1024  # the API refuses images over 10 MB (base64) or 8,000 px a side; keep a margin
API_MAX_SIDE = 8000


def fit_for_api(data: bytes) -> bytes:
    """``data`` unchanged when the API accepts it; otherwise the image at the largest size the model keeps
    (``sizing.native_size``: Claude reads at most 2,576 px a side), as PNG, or JPEG if PNG is still too big.
    Only images the API would refuse are touched, so every other request stays byte-for-byte the same."""
    if len(data) <= API_MAX_BYTES:
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as im:
                if max(im.size) <= API_MAX_SIDE:
                    return data
        except Exception:
            return data
    from PIL import Image
    import io
    from .sizing import native_size
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, 200_000_000)
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im = im.resize(native_size(*im.size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        if buf.tell() > API_MAX_BYTES:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=92)
        return buf.getvalue()


def media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"
