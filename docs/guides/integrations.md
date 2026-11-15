---
title: LangChain and LlamaIndex
description: Use ReadAnyDoc as a document loader in LangChain and a reader in LlamaIndex to load PDFs, scans and Office files as clean Markdown with page metadata for RAG.
---

# LangChain and LlamaIndex

ReadAnyDoc plugs into both frameworks as a loader. Each page becomes one document by default, as Markdown with tables
in HTML, and carries its page number, so answers can point back to the page.

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

## Options

| Argument | Values |
|---|---|
| `split` | `"page"` (default), `"block"` (one document per title, paragraph, table...; metadata adds `block_type`, `bbox`, `confidence`), `"document"` (the whole file) |
| any `readanydoc.parse` argument | `mode="model-only"`, `model=...`, `pages=[1, 2]`, ... |

The first document of a file carries `warnings` in its metadata when there are any (a page the provider declined,
an illegible value). Typed extraction with citations is not part of the loaders; call
[`readanydoc.extract`](extract.md) for that.
