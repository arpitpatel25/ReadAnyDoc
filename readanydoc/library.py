"""readanydoc as a Python library: parse and extract documents without running a server.

    import readanydoc

    result = readanydoc.parse("invoice.pdf")
    print(result.markdown)

    class Invoice(pydantic.BaseModel):
        number: str
        total: float

    invoice = readanydoc.extract("invoice.pdf", schema=Invoice)
    invoice.data.total                    # an Invoice instance
    invoice.citations["total"]            # where on the page the value was read

Every call runs the same pipeline as the HTTP server and calls the model provider with your own key
(``ANTHROPIC_API_KEY``, or ``OPENAI_API_KEY`` for OpenAI models).

Choices, all optional:

    mode             "engine" (default) or "model-only"; see readanydoc/profiles.py and BENCHMARKS.md
    model            the page-reading model, default "claude-haiku-5-5" (any Claude model, "gpt-..." models, or
                     "<provider>:<model>", e.g. "openai_compatible:<model>@<base_url>")
    effort           the model's effort level, default "high"
    model_options    further fields for the provider's request, e.g. {"max_tokens": 32000, "inference_geo": "us"}
    handwriting_model, handwriting_effort, handwriting_options
                     the reader for handwritten pages in engine mode, default "claude-sonnet-5-5";
                     None reads handwritten pages like any other page
    extract_model, extract_effort, extract_options
                     the model that maps the document onto your schema (extract only); default: ``model``
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_HANDWRITING_MODEL = "claude-sonnet-5-5"
MODES = {"engine": "engine", "model-only": "model-only"}

Source = Union[str, Path, bytes]
_UNSET: Any = object()


# ---------------------------------------------------------------- results

class BoundingBox(BaseModel):
    """Position on a page as fractions of the page width and height; ``page`` starts at 1."""
    left: float
    top: float
    width: float
    height: float
    page: int


class Block(BaseModel):
    type: str
    content: str
    bbox: Optional[BoundingBox] = None
    confidence: Optional[str] = None


class Usage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Optional[float] = Field(None, description="from token counts; None when a model has no known price")
    seconds: float = 0.0
    by_model: dict[str, dict[str, int]] = Field(default_factory=dict)


class ParseResult(BaseModel):
    blocks: list[Block]
    pages: int
    usage: Usage
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, description="the Reducto-shaped parse response", repr=False)

    @property
    def markdown(self) -> str:
        return "\n\n".join(b.content for b in self.blocks if b.content)

    @property
    def tables(self) -> list[Block]:
        return [b for b in self.blocks if b.type == "Table"]


class Citation(BaseModel):
    content: str
    bbox: Optional[BoundingBox] = None
    confidence: Optional[str] = None


class ExtractResult(BaseModel):
    data: Any = Field(description="an instance of the schema class when one was given and the result validates; "
                                  "otherwise a dict")
    citations: dict[str, list[Citation]] = Field(default_factory=dict,
                                                 description="by field path, e.g. 'lines[0].amount'")
    usage: Usage
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, description="the Reducto-shaped extract response", repr=False)


# ---------------------------------------------------------------- calls

def parse(source: Source, *, mode: str = "engine", model: str = DEFAULT_MODEL, effort: Optional[str] = DEFAULT_EFFORT,
          model_options: Optional[dict[str, Any]] = None, handwriting_model: Optional[str] = _UNSET,
          handwriting_effort: Optional[str] = None, handwriting_options: Optional[dict[str, Any]] = None,
          pages: Optional[list[int]] = None, citations: bool = False, filename: Optional[str] = None,
          options: Optional[dict[str, Any]] = None, timeout: Optional[float] = None) -> ParseResult:
    """Parse a document (a path, or bytes with ``filename``) into typed blocks.

    ``options`` takes any Reducto parse option (``retrieval``, ``formatting``, ``settings``, ...) as in the HTTP API.
    ``timeout`` (seconds) raises ``readanydoc.pipeline.PipelineTimeout`` when the document takes longer.
    """
    t0 = time.time()
    with _local_file(source, filename) as path:
        doc = _run_pipeline(path, mode=mode, model=model, effort=effort, model_options=model_options,
                            handwriting_model=handwriting_model, handwriting_effort=handwriting_effort,
                            handwriting_options=handwriting_options, pages=pages, citations=citations,
                            options=options, timeout=timeout)
    from .serialize import to_parse_response
    raw = to_parse_response(doc)
    raw.setdefault("usage", {})["model_usage"] = _model_usage(doc)
    blocks = [_block(b) for chunk in (raw.get("result") or {}).get("chunks", []) for b in chunk.get("blocks", [])]
    return ParseResult(blocks=blocks, pages=len(doc.pages), usage=_usage(doc, time.time() - t0),
                       warnings=_warnings(doc), raw=raw)


def extract(source: Source, schema: Union[dict[str, Any], type[BaseModel]], *, instructions: Optional[str] = None,
            citations: bool = True, array_extract: bool = False, mode: str = "engine", model: str = DEFAULT_MODEL,
            effort: Optional[str] = DEFAULT_EFFORT, model_options: Optional[dict[str, Any]] = None,
            extract_model: Optional[str] = None, extract_effort: Optional[str] = None,
            extract_options: Optional[dict[str, Any]] = None, handwriting_model: Optional[str] = _UNSET,
            handwriting_effort: Optional[str] = None, handwriting_options: Optional[dict[str, Any]] = None,
            pages: Optional[list[int]] = None, filename: Optional[str] = None,
            settings: Optional[dict[str, Any]] = None, timeout: Optional[float] = None) -> ExtractResult:
    """Extract the fields of ``schema`` (a JSON Schema dict or a pydantic model class) from a document.

    ``instructions`` is the system prompt for the extraction model. ``array_extract`` processes long documents in
    parts and merges their arrays. ``settings`` takes any further Reducto extract setting.
    """
    from .tasks import extract as extract_task
    from .tasks.llm import get_llm
    from .modelspec import make_spec

    t0 = time.time()
    schema_class = schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None
    json_schema = schema_class.model_json_schema() if schema_class else schema
    with _local_file(source, filename) as path:
        doc = _run_pipeline(path, mode=mode, model=model, effort=effort, model_options=model_options,
                            handwriting_model=handwriting_model, handwriting_effort=handwriting_effort,
                            handwriting_options=handwriting_options, pages=pages, citations=False, options=None,
                            timeout=timeout)
        config: dict[str, Any] = {
            "instructions": {"schema": json_schema, **({"system_prompt": instructions} if instructions else {})},
            "settings": {**(settings or {}), "citations": {"enabled": citations}, "array_extract": array_extract},
        }
        llm = get_llm(make_spec(extract_model or model, extract_effort or effort, extract_options))
        raw = extract_task.run(doc, config, llm=llm)
    result = raw.get("result")
    if isinstance(result, list) and len(result) == 1:  # Reducto's shape without citations: one object per document
        result = result[0]
    data, cites = _unwrap(result, citations)
    warnings = _warnings(doc) + [str(w) for w in raw.get("warnings") or []]
    if schema_class is not None:
        try:
            data = schema_class.model_validate(data)
        except Exception as e:  # keep the extracted values; say why they are not an instance
            warnings.append(f"result does not validate against {schema_class.__name__}: {e}")
    return ExtractResult(data=data, citations=cites, usage=_usage(doc, time.time() - t0), warnings=warnings, raw=raw)


# ---------------------------------------------------------------- internals

def _run_pipeline(path: str, *, mode: str, model: str, effort: Optional[str], model_options: Optional[dict],
                  handwriting_model: Optional[str], handwriting_effort: Optional[str],
                  handwriting_options: Optional[dict], pages: Optional[list[int]], citations: bool,
                  options: Optional[dict], timeout: Optional[float] = None):
    from .modelspec import make_spec
    from .pipeline import Pipeline
    from .pipeline.request import apply_parse_request
    from .profiles import get_profile

    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}, not {mode!r}")
    cfg = get_profile(MODES[mode])
    cfg.reader = make_spec(model, effort, model_options)
    if handwriting_model is _UNSET:
        handwriting_model = DEFAULT_HANDWRITING_MODEL
    if handwriting_model is None:
        cfg = cfg.with_stage("read", handwriting_route=False)
    elif cfg.stage("read").options.get("handwriting_route"):
        cfg = cfg.with_stage("read", handwriting_reader=make_spec(handwriting_model, handwriting_effort,
                                                                  handwriting_options))
    request = dict(options or {})
    request_settings = dict(request.get("settings") or {})
    if pages:
        request_settings["page_range"] = list(pages)
    if citations:
        request_settings["return_citations"] = True
    request["settings"] = request_settings
    cfg = apply_parse_request(cfg, request)
    return Pipeline(cfg).run(path, timeout=timeout)


class _local_file:
    """A path for the pipeline: the given path, or bytes written to a temporary file."""

    def __init__(self, source: Source, filename: Optional[str]):
        self.source, self.filename, self._tmp = source, filename, None

    def __enter__(self) -> str:
        if isinstance(self.source, (str, Path)):
            path = Path(self.source)
            if not path.is_file():
                raise FileNotFoundError(path)
            return str(path)
        suffix = Path(self.filename or "document").suffix or ""
        fd, self._tmp = tempfile.mkstemp(prefix="readanydoc-", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(self.source)
        return self._tmp

    def __exit__(self, *exc) -> None:
        if self._tmp:
            Path(self._tmp).unlink(missing_ok=True)


def _bbox(b: Optional[dict]) -> Optional[BoundingBox]:
    if not b or b.get("left") is None:
        return None
    return BoundingBox(left=b["left"], top=b["top"], width=b["width"], height=b["height"], page=b.get("page", 1))


def _block(b: dict) -> Block:
    return Block(type=b.get("type", "Text"), content=b.get("content") or "", bbox=_bbox(b.get("bbox")),
                 confidence=b.get("confidence"))


def _model_usage(doc) -> dict[str, Any]:
    from .readers.usage import summarize
    return summarize(doc.usage)


def _usage(doc, seconds: float) -> Usage:
    from .readers.usage import cost_usd, summarize
    mu = _model_usage(doc)
    # page reads are priced from their tokens; task calls (extract, split, ...) carry their own cost
    reads = cost_usd(summarize(e for e in doc.usage if not e.get("task")))
    tasks = [e.get("cost_usd") for e in doc.usage if e.get("task")]
    if any(not isinstance(c, (int, float)) for c in tasks) or (reads is None and any(not e.get("task") for e in doc.usage
                                                                                      if e.get("input_tokens"))):
        cost = None
    else:
        cost = round((reads or 0.0) + sum(tasks), 6)
    return Usage(calls=mu["calls"], input_tokens=mu["input_tokens"], output_tokens=mu["output_tokens"],
                 cost_usd=cost, seconds=round(seconds, 2), by_model=mu.get("models", {}))


def _warnings(doc) -> list[str]:
    return [f"{n.stage}: {n.message}" for n in doc.notes if n.level in ("warning", "error")]


def _unwrap(value: Any, with_citations: bool, path: str = "") -> tuple[Any, dict[str, list[Citation]]]:
    """Extract results with citations wrap every leaf as {"value": ..., "citations": [...]}: split values and
    citations (keyed by field path)."""
    cites: dict[str, list[Citation]] = {}
    if isinstance(value, dict) and with_citations and set(value) <= {"value", "citations"} and "value" in value:
        found = [Citation(content=c.get("content") or "", bbox=_bbox(c.get("bbox")), confidence=c.get("confidence"))
                 for c in value.get("citations") or []]
        if found:
            cites[path] = found
        inner, more = _unwrap(value["value"], with_citations, path)
        return inner, {**cites, **more}
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k], more = _unwrap(v, with_citations, f"{path}.{k}" if path else k)
            cites.update(more)
        return out, cites
    if isinstance(value, list):
        out_list = []
        for i, v in enumerate(value):
            item, more = _unwrap(v, with_citations, f"{path}[{i}]")
            out_list.append(item)
            cites.update(more)
        return out_list, cites
    return value, cites
