---
title: LangChain, LlamaIndex and Haystack
description: Load PDFs, scans and Office files as Markdown with page metadata in LangChain, LlamaIndex and Haystack.
---

# LangChain, LlamaIndex and Haystack

Each page becomes one document by default, as Markdown with tables in HTML. The page number in the metadata lets
answers point back to the page.

## LangChain

```bash
pip install "readanydoc[engine,langchain]"
```

```python
from readanydoc.integrations.langchain import ReadAnyDocLoader

docs = ReadAnyDocLoader("report.pdf").load()          # one Document per page
docs[0].metadata                                       # {"source": "report.pdf", "page": 1}
```

## LlamaIndex

```bash
pip install "readanydoc[engine,llamaindex]"
```

```python
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from readanydoc.integrations.llamaindex import ReadAnyDocReader

reader = ReadAnyDocReader()
docs = SimpleDirectoryReader("contracts/", file_extractor={".pdf": reader, ".docx": reader}).load_data()
index = VectorStoreIndex.from_documents(docs)
```

## Haystack

```bash
pip install "readanydoc[engine,haystack]"
```

```python
from haystack import Pipeline
from readanydoc.integrations.haystack import ReadAnyDocConverter

pipeline = Pipeline()
pipeline.add_component("converter", ReadAnyDocConverter())
result = pipeline.run({"converter": {"sources": ["report.pdf", "appendix.docx"]}})
docs = result["converter"]["documents"]
print(docs[0].content, docs[0].meta)
```

Pass local file paths in `sources` and set `split` and parse options on the converter. If a source fails to parse,
the converter surfaces the error, as the other adapters do.

## Options

| Argument | Values |
|---|---|
| `split` | `"page"` (default), `"block"` (one document per title, paragraph, table...; metadata adds `block_type`, `bbox`, `confidence`), `"document"` (the whole file) |
| any `readanydoc.parse` argument | `mode="model-only"`, `model=...`, `pages=[1, 2]`, ... |

The first document of a file carries `warnings` in its metadata when there are any (a page the provider declined,
an illegible value). For typed extraction with citations, call [`readanydoc.extract`](extract.md).
