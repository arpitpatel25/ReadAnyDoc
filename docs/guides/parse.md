# Parse

Parse turns a document into typed blocks with positions: titles, text, tables, form fields (key/value), figures,
headers, footers and page numbers, plus Markdown and chunks ready for an LLM or a search index.

## Python

```python
import readanydoc

result = readanydoc.parse(
    "statement.pdf",
    mode="engine",            # or "model-only"
    pages=[1, 2, 5],          # 1-based; default all pages
    citations=True,           # word and cell positions
    options={                 # any Reducto parse option, as in the HTTP API
        "formatting": {"table_output_format": "md"},
        "retrieval": {"chunking": {"chunk_mode": "page"}},
    },
    timeout=600,              # seconds; raises readanydoc.pipeline.PipelineTimeout
)
```

| Result | What it holds |
|---|---|
| `result.blocks` | `list[Block]`: `type`, `content`, `bbox` (page, left, top, width, height as fractions), `confidence` |
| `result.tables` | the `Table` blocks |
| `result.markdown` | the whole document as Markdown |
| `result.pages` | number of pages parsed |
| `result.usage` | `calls`, `input_tokens`, `output_tokens`, `cost_usd`, `seconds`, `by_model` |
| `result.warnings` | one line per problem: a page the provider would not return, a missing layout model, a failed check |
| `result.raw` | the full Reducto-shaped response |

Bytes work too: `readanydoc.parse(data, filename="scan.png")`.

## HTTP

```bash
curl -s -X POST http://localhost:8000/parse -H "content-type: application/json" -d '{
  "input": "https://example.com/statement.pdf",
  "formatting": {"table_output_format": "md", "add_page_markers": true},
  "retrieval": {"chunking": {"chunk_mode": "section"}, "filter_blocks": ["Header", "Footer"]},
  "settings": {"page_range": {"start": 1, "end": 3}, "return_citations": true}
}'
```

## Options

| Option | Values | Notes |
|---|---|---|
| `retrieval.chunking.chunk_mode` | `disabled`, `page`, `block`, `section`, `page_sections`, `variable` | Same rules as Reducto; chunks repeat the headings they sit under |
| `retrieval.chunking.chunk_size`, `chunk_overlap` | numbers | For `variable` chunks; overlap in characters |
| `retrieval.filter_blocks` | block types | Filtered blocks stay listed but leave `content` / `embed` |
| `retrieval.embedding_optimized` | bool | Tables get a model-written summary in `embed` |
| `formatting.table_output_format` | `html`, `md`, `json`, `csv`, `dynamic`, `jsonbbox` | `dynamic` = Markdown for small simple tables, HTML otherwise |
| `formatting.add_page_markers` | bool | `[[START OF PAGE n]]` markers |
| `formatting.merge_tables` | bool | Consecutive tables with the same columns merge |
| `formatting.include` | `hyperlinks`, `comments`, `change_tracking` | DOCX, PPTX and PDF annotations |
| `settings.page_range` | object, list, ints, sheet names | Out-of-range is a 400 error |
| `settings.document_password` | string | Wrong or missing gives 442 |
| `settings.return_images` | `figure`, `table` | `image_url` crops on those blocks |
| `settings.return_citations` | bool | Word and cell positions |
| `settings.return_ocr_data` | bool | `result.ocr.words` / `lines` |
| `settings.extract_document_properties` | bool | Title, author and so on from PDF / DOCX |
| `settings.embed_pdf_metadata` | bool | `pdf_url`: a searchable copy of the PDF with our text layer |
| `settings.persist_results`, `force_url_result` | bool | Keep the result past the expiry, or always return a URL |
| `enhance.agentic` | `[{scope, prompt}]` for `figure`, `table`, `text` | A prompt applied to every block of that type, edited in place |
| `enhance.advanced_chart_agent` | bool | Chart data extracted from figures, with consistency checks |
| `spreadsheet.*` | `clustering`, `split_large_tables`, `include`, `exclude`, `max_cell_count` | Excel and CSV handling |

The full field-by-field comparison with Reducto is in [Compatibility](../migrate/compatibility.md).

## Block types

`Title`, `Section Header`, `Text`, `List Item`, `Table`, `Key Value`, `Figure`, `Header`, `Footer`, `Page Number`,
`Comment`, `Signature`. Each block has a `confidence` of `high` or `low` (see
[Citations and confidence](../concepts/citations.md)).
