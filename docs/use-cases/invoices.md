---
title: Extract data from invoices
description: Turn PDF and scanned invoices into typed data (vendor, invoice number, dates, line items, totals) with a citation for every value, in Python, using Claude Haiku or any model.
---

# Extract data from invoices

Invoices come as born-digital PDFs, scans, phone photos and Word files, with line-item tables that run across pages. ReadAnyDoc reads each page with a vision model, rebuilds the tables, and maps the document onto your own pydantic class.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class LineItem(BaseModel):
    description: str
    quantity: Optional[float]
    unit_price: Optional[float]
    amount: Optional[float]

class Invoice(BaseModel):
    vendor: str
    invoice_number: Optional[str]
    invoice_date: Optional[str] = Field(description="as printed")
    due_date: Optional[str]
    currency: Optional[str]
    line_items: list[LineItem]
    subtotal: Optional[float]
    tax: Optional[float]
    total: Optional[float]

out = readanydoc.extract("invoice.pdf", schema=Invoice)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- Check `out.citations["total"]` to show reviewers where the total was read.
- Long invoices with many lines: pass `array_extract=True`; rows are checked against the parsed tables and a gap is reported in `warnings`.
- Add `instructions="Amounts as numbers without currency symbols"` to steer formatting.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
