"""Reducto parse options as the assembly / shaping / intake stages read them.

``ParseOptions`` (pipeline/base.py) carries the handful of fields the spine knew. The remaining
options travel as the raw Reducto request (``enhance`` / ``retrieval`` / ``formatting`` /
``spreadsheet`` / ``settings``) in the stage option ``request``; ``apply_request`` puts it there.
The API layer needs one line after it has built its PipelineConfig:

    cfg = apply_request(cfg, parsing)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

REQUEST_STAGES = ("intake", "assemble", "shape")
DEFAULT_CHUNK_SIZE = 1000  # characters; the +/-25% band gives Reducto's documented 750-1250
SIZE_BAND = 0.25


def apply_request(cfg, parsing: Optional[dict[str, Any]]):
    """Copy of ``cfg`` whose intake, assemble and shape stages see the raw parse request."""
    if not parsing:
        return cfg
    keep = {k: parsing[k] for k in ("enhance", "retrieval", "formatting", "spreadsheet", "settings")
            if isinstance(parsing.get(k), dict)}
    if isinstance(keep.get("settings"), dict) and "document_password" in keep["settings"]:
        keep["settings"] = {k: v for k, v in keep["settings"].items() if k != "document_password"}
        cfg.parse.document_password = cfg.parse.document_password or parsing["settings"].get("document_password")
    for name in REQUEST_STAGES:
        cfg = cfg.with_stage(name, request=keep)
    return cfg


@dataclass
class SplitLargeTables:
    enabled: bool = True
    rows: Optional[int] = 50
    cols: Optional[int] = None


@dataclass
class SpreadsheetOptions:
    clustering: str = "accurate"  # accurate | fast | disabled
    include: list[str] = field(default_factory=list)  # cell_colors, formula, dropdowns
    exclude: list[str] = field(default_factory=list)  # hidden_sheets, hidden_rows, hidden_cols, styling, ...
    max_cell_count: Optional[int] = None
    split: SplitLargeTables = field(default_factory=SplitLargeTables)


@dataclass
class Options:
    chunk_mode: str = "disabled"
    chunk_size: Optional[int] = None
    chunk_overlap: int = 0
    filter_blocks: list[str] = field(default_factory=list)
    add_page_markers: bool = False
    table_output_format: str = "dynamic"
    embedding_optimized: bool = False
    merge_tables: bool = False
    include: list[str] = field(default_factory=list)  # change_tracking, highlight, comments, hyperlinks, ...
    page_range: Any = None  # raw Reducto form, or a list of 1-based ints
    extract_document_properties: bool = False
    return_citations: bool = False
    spreadsheet: SpreadsheetOptions = field(default_factory=SpreadsheetOptions)

    @property
    def size_band(self) -> tuple[int, int, int]:
        """(min, target, max) characters for variable chunking."""
        target = self.chunk_size if self.chunk_size and self.chunk_size > 0 else DEFAULT_CHUNK_SIZE
        return int(target * (1 - SIZE_BAND)), int(target), int(round(target * (1 + SIZE_BAND)))


def _spreadsheet(raw: dict[str, Any]) -> SpreadsheetOptions:
    so = SpreadsheetOptions()
    if raw.get("clustering") in ("accurate", "fast", "disabled"):
        so.clustering = raw["clustering"]
    so.include = [str(x) for x in raw.get("include") or []]
    so.exclude = [str(x) for x in raw.get("exclude") or []]
    so.max_cell_count = raw.get("max_cell_count")
    slt = raw.get("split_large_tables")
    if isinstance(slt, dict):
        so.split.enabled = bool(slt.get("enabled", True))
        size = slt.get("size", 50)
        if isinstance(size, dict):
            so.split.rows, so.split.cols = size.get("row"), size.get("column")
        elif isinstance(size, int) and not isinstance(size, bool):
            so.split.rows = size
    return so


def resolve(parse: Any, request: Optional[dict[str, Any]] = None, overrides: Optional[dict[str, Any]] = None) -> Options:
    """Merge ParseOptions, the raw request and explicit stage options (strongest last)."""
    o = Options()
    if parse is not None:
        for k in ("chunk_mode", "chunk_size", "chunk_overlap", "add_page_markers", "table_output_format",
                  "page_range", "extract_document_properties"):
            setattr(o, k, getattr(parse, k))
        o.filter_blocks = list(parse.filter_blocks or [])
    req = request or {}
    retrieval = req.get("retrieval") or {}
    formatting = req.get("formatting") or {}
    settings = req.get("settings") or {}
    chunking = retrieval.get("chunking") or {}
    for k in ("chunk_mode", "chunk_size", "chunk_overlap"):
        if chunking.get(k) is not None:
            setattr(o, k, chunking[k])
    if retrieval.get("filter_blocks"):
        o.filter_blocks = list(retrieval["filter_blocks"])
    o.embedding_optimized = bool(retrieval.get("embedding_optimized", False))
    if "add_page_markers" in formatting:
        o.add_page_markers = bool(formatting["add_page_markers"])
    if formatting.get("table_output_format"):
        o.table_output_format = formatting["table_output_format"]
    o.merge_tables = bool(formatting.get("merge_tables", False))
    o.include = [str(x) for x in formatting.get("include") or []]
    if settings.get("page_range") is not None:
        o.page_range = settings["page_range"]
    if "extract_document_properties" in settings:
        o.extract_document_properties = bool(settings["extract_document_properties"])
    o.return_citations = bool(settings.get("return_citations", False))
    o.spreadsheet = _spreadsheet(req.get("spreadsheet") or {})
    for k, v in (overrides or {}).items():
        if k == "spreadsheet" and isinstance(v, dict):
            o.spreadsheet = _spreadsheet(v)
        elif hasattr(o, k) and v is not None:
            setattr(o, k, v)
    o.chunk_overlap = max(int(o.chunk_overlap or 0), 0)
    return o
