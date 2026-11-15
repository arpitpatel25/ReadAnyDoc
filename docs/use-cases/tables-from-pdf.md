---
title: Extract tables from PDFs
description: Convert tables in PDFs and scans to HTML, Markdown or structured rows, including merged cells and tables that span pages, with Python.
---

# Extract tables from PDFs

Tables are where plain text extraction breaks: merged header cells, columns without lines, numbers that shift by one row. ReadAnyDoc reads each table with a vision model, re-reads table regions on their own, and checks grids and printed totals without a model.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

result = readanydoc.parse("report.pdf")
for table in result.tables:          # blocks of type Table, as HTML
    print(table.content)

# or straight into rows
class Row(BaseModel):
    region: str
    revenue: Optional[float]
    growth_percent: Optional[float]

class Report(BaseModel):
    rows: list[Row]

out = readanydoc.extract("report.pdf", schema=Report, array_extract=True)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- Markdown tables instead of HTML: `options={"formatting": {"table_output_format": "md"}}`.
- On RD-TableBench, ReadAnyDoc is level with Reducto (see [benchmarks](../resources/benchmarks.md)).
- Each table block has a bounding box; with `citations=True` every cell has one.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
