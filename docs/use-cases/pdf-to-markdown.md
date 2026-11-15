---
title: Convert PDF to Markdown for RAG and LLMs
description: Turn PDFs, scans and Office files into clean Markdown with headings, lists and tables, ready for chunking, RAG and LLM context.
---

# Convert PDF to Markdown for RAG and LLMs

Language models work best with clean Markdown: real headings, lists, tables that keep their rows, no page headers in the middle of a sentence. ReadAnyDoc returns the document as typed blocks (title, section header, text, table, figure) and as Markdown.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc

result = readanydoc.parse("handbook.pdf")
markdown = result.markdown              # the whole document
for block in result.blocks:             # or block by block, with type and position
    print(block.type, block.bbox.page, block.content[:80])
```

## Tips

- Blocks keep their page and box, so a RAG answer can link back to the page it came from.
- Headers and footers are typed as such and can be dropped before chunking.
- From the command line: `readanydoc parse handbook.pdf --markdown > handbook.md`.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
