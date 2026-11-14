"""Request models for Reducto's v3 API, written to match ``reference/openapi.json``.

``tests/api/test_contract.py`` checks every model here against the spec (field names, required
fields, defaults). Unknown fields are ignored, as Reducto does; v2-only knobs (``options``,
``advanced_options``, ``experimental_options``, top-level ``webhook`` / ``priority``) fall under that
rule; the v2 ``document_url`` is read as ``input`` when ``input`` is missing.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

BlockType = Literal["Header", "Footer", "Title", "Section Header", "Page Number", "List Item", "Figure", "Table",
                    "Key Value", "Text", "Comment", "Signature"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, protected_namespaces=())

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class UploadResponse(_M):
    file_id: str
    presigned_url: Optional[str] = None


Input = Union[str, list[str], UploadResponse]


class PageRange(_M):
    start: Optional[int] = None
    end: Optional[int] = None


PageRangeSpec = Union[PageRange, list[PageRange], list[int], list[str], None]


class Chunking(_M):
    chunk_mode: Literal["variable", "section", "page", "disabled", "block", "page_sections"] = "disabled"
    chunk_size: Optional[int] = None
    chunk_overlap: int = 0


class Retrieval(_M):
    chunking: Chunking = Field(default_factory=Chunking)
    filter_blocks: list[BlockType] = Field(default_factory=list)
    embedding_optimized: bool = False


class TableAgentic(_M):
    scope: Literal["table"]
    prompt: Optional[str] = None
    mode: Literal["default", "auto", "max"] = "default"


class FigureAgentic(_M):
    scope: Literal["figure"]
    prompt: Optional[str] = None
    advanced_chart_agent: bool = False


class TextAgentic(_M):
    scope: Literal["text"]
    prompt: Optional[str] = None


class Enhance(_M):
    agentic: list[Union[TableAgentic, FigureAgentic, TextAgentic]] = Field(default_factory=list)
    advanced_chart_agent: bool = False
    summarize_figures: bool = True
    intelligent_ordering: bool = False


class Formatting(_M):
    add_page_markers: bool = False
    table_output_format: Literal["html", "json", "md", "jsonbbox", "dynamic", "csv"] = "dynamic"
    merge_tables: bool = False
    include: list[Literal["change_tracking", "highlight", "comments", "hyperlinks", "signatures",
                          "ignore_watermarks"]] = Field(default_factory=list)


class SplitLargeTableSizes(_M):
    row: Optional[int] = None
    column: Optional[int] = None


class SplitLargeTables(_M):
    enabled: bool = True
    size: Union[int, SplitLargeTableSizes] = 50


class Spreadsheet(_M):
    split_large_tables: SplitLargeTables = Field(default_factory=SplitLargeTables)
    include: list[Literal["cell_colors", "formula", "dropdowns"]] = Field(default_factory=list)
    clustering: Literal["accurate", "fast", "disabled"] = "accurate"
    exclude: list[Literal["hidden_sheets", "hidden_rows", "hidden_cols", "styling",
                          "spreadsheet_images"]] = Field(default_factory=list)
    max_cell_count: Optional[int] = Field(default=None, ge=1)


class TenantThrottling(_M):
    tenant_id: str = Field(min_length=1, max_length=256)
    max_share: float = Field(default=0.5, gt=0.0, le=1.0)


class HybridVpcSettings(_M):
    environment: Optional[str] = None


class Settings(_M):
    ocr_system: Literal["standard", "legacy"] = "standard"
    extraction_mode: Literal["ocr", "hybrid"] = "hybrid"
    force_url_result: bool = False
    force_file_extension: Optional[str] = None
    return_ocr_data: bool = False
    return_images: list[Literal["figure", "table", "page"]] = Field(default_factory=list)
    embed_pdf_metadata: bool = False
    extract_document_properties: bool = False
    embed_pdf_metadata_dpi: int = Field(default=200, ge=50, le=250)
    persist_results: bool = False
    tenant_throttling: Optional[TenantThrottling] = None
    timeout: Optional[float] = None
    page_range: PageRangeSpec = None
    document_password: Optional[str] = None
    hybrid_vpc: HybridVpcSettings = Field(default_factory=HybridVpcSettings)
    model: Optional[Literal["r-1", "r-1-mini", "legacy"]] = None
    return_citations: bool = False

    # Additive, not in Reducto's API and therefore not declared as fields (the contract tests compare
    # the fields with Reducto's spec): ``page_cap_usd`` / ``page_cap_tokens``, a spending limit per page for this
    # request. When a page reaches it, its remaining optional re-reads are skipped and listed in
    # ``usage.model_usage.spend_cap``. Every other unknown key is dropped as before.
    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())
    ADDITIVE: ClassVar[tuple[str, ...]] = ("page_cap_usd", "page_cap_tokens")

    @model_validator(mode="after")
    def _keep_only_additive(self) -> "Settings":
        extra = self.__pydantic_extra__ or {}
        for k in list(extra):
            v = extra[k]
            ok = k in self.ADDITIVE and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            if not ok:
                if k in self.ADDITIVE and v is not None:
                    raise ValueError(f"{k} must be a number above 0")
                del extra[k]
            elif k == "page_cap_tokens":
                extra[k] = int(v)
        return self


class ParseOptions(_M):
    enhance: Enhance = Field(default_factory=Enhance)
    retrieval: Retrieval = Field(default_factory=Retrieval)
    formatting: Formatting = Field(default_factory=Formatting)
    spreadsheet: Spreadsheet = Field(default_factory=Spreadsheet)
    settings: Settings = Field(default_factory=Settings)


class SvixWebhookConfig(_M):
    mode: Literal["svix"] = "svix"
    channels: list[str] = Field(default_factory=list)


class DirectWebhookConfig(_M):
    mode: Literal["direct"] = "direct"
    url: str


class WebhookConfigNew(_M):
    mode: Literal["disabled", "svix", "direct"] = "disabled"
    url: str = ""
    metadata: Any = None
    channels: list[str] = Field(default_factory=list)


class AsyncConfig(_M):
    metadata: Any = None
    priority: bool = False
    webhook: Union[DirectWebhookConfig, SvixWebhookConfig, None] = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_webhook(cls, data: Any) -> Any:
        # v2 clients send {"mode": "disabled"}; treat it as no webhook instead of a 422
        if isinstance(data, dict) and isinstance(data.get("webhook"), dict) \
                and data["webhook"].get("mode") == "disabled":
            data = {**data, "webhook": None}
        return data


QueuePriority = Literal["auto", "standard", "batch"]


class _WithInput(_M):
    input: Input

    @model_validator(mode="before")
    @classmethod
    def _legacy_input(cls, data: Any) -> Any:
        if isinstance(data, dict) and "input" not in data and data.get("document_url") is not None:
            data = {**data, "input": data["document_url"]}
        return data


class _WithAsync(_M):
    async_: AsyncConfig = Field(default_factory=AsyncConfig, alias="async")


class SyncParseConfig(_WithInput, ParseOptions):
    pass


class AsyncParseConfig(_WithAsync, SyncParseConfig):
    queue_priority: QueuePriority = "auto"


class Instructions(_M):
    # readanydoc extension: `examples` = example outputs shown with the schema. Unknown keys pass through so that
    # requests and echoes keep exactly Reducto's keys when the extra ones are not sent.
    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())
    schema_: Any = Field(default_factory=dict, alias="schema")
    system_prompt: str = "Be precise and thorough."


class Citations(_M):
    enabled: bool = False
    numerical_confidence: bool = True
    parent_block: Literal["full", "bbox_only"] = "full"


class ExtractSettings(_M):
    # readanydoc extensions: `relevance_filter`, `request_pages` pass through as extra keys (see tasks/extract_extras.py)
    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())
    include_images: bool = False
    optimize_for_latency: bool = False
    force_url_result: bool = False
    array_extract: bool = False
    deep_extract: bool = False
    citations: Citations = Field(default_factory=Citations)
    page_range: PageRangeSpec = None


class SyncExtractConfig(_WithInput):
    parsing: ParseOptions = Field(default_factory=ParseOptions)
    instructions: Instructions = Field(default_factory=Instructions)
    settings: ExtractSettings = Field(default_factory=ExtractSettings)


class AsyncExtractConfig(_WithAsync, SyncExtractConfig):
    queue_priority: QueuePriority = "auto"


class SplitCategory(_M):
    name: str
    description: str
    partition_key: Optional[str] = None


class SplitSettings(_M):
    table_cutoff: Literal["truncate", "preserve"] = "truncate"
    allow_page_overlap: bool = True
    auto_partition: bool = True
    deep_split: bool = False
    force_url_result: bool = False


class SyncSplitConfig(_WithInput):
    parsing: ParseOptions = Field(default_factory=ParseOptions)
    split_description: list[SplitCategory]
    split_rules: str = ("Split the document into the applicable sections. Sections may only overlap at "
                        "their first and last page if at all.")
    settings: SplitSettings = Field(default_factory=SplitSettings)


class AsyncSplitConfig(_WithAsync, SyncSplitConfig):
    pass


class ClassificationCategory(_M):
    category: str
    criteria: list[str]


class ClassifyConfig(_WithInput):
    model: Literal["default", "accurate"] = "default"
    priority: bool = True
    async_: AsyncConfig = Field(default_factory=AsyncConfig, alias="async")
    force_url_result: bool = False
    classification_schema: list[ClassificationCategory] = Field(default_factory=list)
    category_groups: dict[str, list[str]] = Field(default_factory=dict)
    page_range: Union[PageRange, list[PageRange], list[int], None] = None
    document_metadata: Optional[str] = None


class BoundingBox(_M):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    left: float
    top: float
    width: float
    height: float
    page: int
    original_page: Optional[int] = None


class EditWidget(_M):
    bbox: BoundingBox
    description: str
    type: Literal["text", "checkbox", "radio", "dropdown", "barcode"]
    fill: bool = True
    value: Optional[str] = None
    font_size: Optional[float] = Field(default=None, ge=1.0, le=72.0)


class EditOptions(_M):
    color: str = Field(default="#FF0000", pattern=r"^#[0-9A-Fa-f]{6}$")
    font_size: Optional[float] = Field(default=None, ge=1.0, le=72.0)
    llm_provider_preference: Optional[Literal["openai", "anthropic", "google"]] = None
    enable_overflow_pages: bool = False
    flatten: bool = False
    # ours: `flat_fields: false` draws the values on a flat page instead of adding real form fields.
    # Not declared as a field so that requests and echoes keep exactly Reducto's keys when it is not sent.
    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())


class EditConfig(_M):
    document_url: Union[str, UploadResponse]
    edit_instructions: str
    edit_options: EditOptions = Field(default_factory=EditOptions)
    form_schema: Optional[list[EditWidget]] = None
    priority: bool = True


class AsyncEditConfig(EditConfig):
    priority: bool = False
    webhook: WebhookConfigNew = Field(default_factory=WebhookConfigNew)


class PipelineSettings(_M):
    document_password: Optional[str] = None


class V3PipelineConfig(_WithInput):
    pipeline_id: str
    settings: PipelineSettings = Field(default_factory=PipelineSettings)


class V3AsyncPipelineConfig(_WithAsync, V3PipelineConfig):
    pass


# OpenAPI component name -> model (used by the contract tests)
SCHEMA_MODELS: dict[str, type[_M]] = {
    "SyncParseConfig": SyncParseConfig, "AsyncParseConfig": AsyncParseConfig,
    "SyncExtractConfig": SyncExtractConfig, "AsyncExtractConfig": AsyncExtractConfig,
    "SyncSplitConfig": SyncSplitConfig, "config__v3__AsyncSplitConfig": AsyncSplitConfig,
    "ClassifyConfig": ClassifyConfig, "EditConfig": EditConfig, "AsyncEditConfig": AsyncEditConfig,
    "V3PipelineConfig": V3PipelineConfig, "V3AsyncPipelineConfig": V3AsyncPipelineConfig,
    "ParseOptions": ParseOptions, "Enhance": Enhance, "Retrieval": Retrieval, "Chunking": Chunking,
    "Formatting": Formatting, "Spreadsheet": Spreadsheet, "SplitLargeTables": SplitLargeTables,
    "SplitLargeTableSizes": SplitLargeTableSizes, "Settings": Settings, "TenantThrottling": TenantThrottling,
    "HybridVpcSettings": HybridVpcSettings, "PageRange": PageRange, "TableAgentic": TableAgentic,
    "FigureAgentic": FigureAgentic, "TextAgentic": TextAgentic, "config__v3__AsyncConfig": AsyncConfig,
    "SvixWebhookConfig": SvixWebhookConfig, "DirectWebhookConfig": DirectWebhookConfig,
    "WebhookConfigNew": WebhookConfigNew, "Instructions": Instructions, "ExtractSettings": ExtractSettings,
    "Citations": Citations, "SplitCategory": SplitCategory, "SplitSettings": SplitSettings,
    "ClassificationCategory": ClassificationCategory, "EditOptions": EditOptions, "EditWidget": EditWidget,
    "PipelineSettings": PipelineSettings, "UploadResponse": UploadResponse,
}
