"""Pipeline skeleton: stage registry, config, runner.

Each stage lives in its own file, registers itself under a name, takes a Document and returns a
Document. A stage can be replaced by editing only its file, or by registering another
implementation and selecting it with ``StageConfig.impl``.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, Field

from ..model import Document

STAGE_ORDER = ["intake", "triage", "layout", "read", "verify", "ground", "assemble", "shape"]
STAGES: dict[str, type["Stage"]] = {}


def register_stage(name: str):
    def deco(cls: type["Stage"]) -> type["Stage"]:
        cls.name = name
        STAGES[name] = cls
        return cls
    return deco


class ParseOptions(BaseModel):
    """The subset of Reducto's parse request the Shape stage understands (others accepted upstream)."""

    chunk_mode: Literal["disabled", "page", "block", "variable", "section", "page_sections"] = "disabled"
    chunk_size: Optional[int] = None
    chunk_overlap: int = 0
    filter_blocks: list[str] = Field(default_factory=list)
    add_page_markers: bool = False
    table_output_format: Literal["dynamic", "html", "md", "json", "csv", "jsonbbox"] = "dynamic"
    page_range: Optional[list[int]] = None  # 1-based page numbers
    document_password: Optional[str] = None
    extract_document_properties: bool = False
    return_citations: bool = False  # settings.return_citations: extra.word_citations / cell_citations
    return_ocr_data: bool = False  # settings.return_ocr_data: result.ocr words and lines


class StageConfig(BaseModel):
    enabled: bool = True
    impl: Optional[str] = None  # registry key; defaults to the stage name
    options: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    profile: str = "custom"
    tier: Literal["fast", "strong", "expert"] = "strong"
    reader: str = "cached:haiku"
    layout: str = "cached:pvl"
    words: str = "cached:ppocr"
    stages: dict[str, StageConfig] = Field(default_factory=dict)
    parse: ParseOptions = Field(default_factory=ParseOptions)

    def stage(self, name: str) -> StageConfig:
        return self.stages.get(name) or StageConfig()

    def with_stage(self, name: str, enabled: Optional[bool] = None, **options: Any) -> "PipelineConfig":
        """Copy with one stage switched or re-optioned (used by the ablation harness)."""
        cfg = self.model_copy(deep=True)
        sc = cfg.stages.get(name) or StageConfig()
        if enabled is not None:
            sc.enabled = enabled
        sc.options.update(options)
        cfg.stages[name] = sc
        return cfg


class PipelineContext:
    """Lazily built adapters shared by the stages of one run."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._reader = None
        self._layout = None
        self._words = None

    @property
    def reader(self):
        if self._reader is None:
            from ..readers import make_reader
            self._reader = make_reader(self.config.reader)
        return self._reader

    @property
    def layout(self):
        if self._layout is None:
            from ..layout import make_layout
            self._layout = make_layout(self.config.layout)
        return self._layout

    @property
    def words(self):
        if self._words is None:
            from ..readers import make_word_provider
            self._words = make_word_provider(self.config.words)
        return self._words


class Stage:
    """Base class. Subclasses implement ``run``; ``enabled`` is honoured by the Pipeline."""

    name: ClassVar[str] = "stage"
    required: ClassVar[bool] = False  # required stages cannot be switched off (intake)

    def __init__(self, enabled: bool = True, **options: Any):
        self.enabled = enabled
        self.options = options

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def run(self, doc: Document, ctx: PipelineContext) -> Document:  # pragma: no cover - interface
        raise NotImplementedError


class PipelineTimeout(RuntimeError):
    """A document took longer than the time it was given."""


class Pipeline:
    def __init__(self, config: Optional[PipelineConfig] = None):
        from . import _load_builtin_stages
        _load_builtin_stages()
        self.config = config or PipelineConfig()
        self.ctx = PipelineContext(self.config)
        self.stages: list[Stage] = []
        for name in STAGE_ORDER:
            sc = self.config.stage(name)
            cls = STAGES[sc.impl or name]
            self.stages.append(cls(enabled=sc.enabled or cls.required, **sc.options))

    def run(self, source: str | Document, timeout: Optional[float] = None) -> Document:
        """Run every stage on a document. With ``timeout`` (seconds), raise PipelineTimeout when the document
        is not done in time; the abandoned work finishes in the background and its result is discarded."""
        if timeout:
            return _run_with_deadline(lambda: self.run(source), timeout)
        doc = source if isinstance(source, Document) else Document(source=str(source))
        doc.meta.setdefault("profile", self.config.profile)
        doc.meta.setdefault("tier", self.config.tier)
        doc.meta.setdefault("reader", self.config.reader)
        t0 = time.time()
        timings: dict[str, float] = {}
        for stage in self.stages:
            if not stage.enabled:
                doc.note(stage.name, "disabled")
                continue
            t = time.time()
            doc = stage.run(doc, self.ctx)
            timings[stage.name] = round(time.time() - t, 4)
        doc.meta["stage_seconds"] = timings
        doc.meta["duration"] = round(time.time() - t0, 4)
        return doc


def _run_with_deadline(fn, timeout: float):
    import threading
    box: dict = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as e:  # re-raised in the caller's thread
            box["error"] = e

    worker = threading.Thread(target=target, name="readanydoc-document", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise PipelineTimeout(f"the document was not processed within {timeout:.0f} s")
    if "error" in box:
        raise box["error"]
    return box["result"]
