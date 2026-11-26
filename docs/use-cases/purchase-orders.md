---
title: Extract data from purchase orders, packing lists and bills of lading
description: Turn PDF and scanned purchase orders, packing lists and bills of lading into typed data (parties, numbers, dates, line items, weights, ports) with a citation for every value, in Python, using Claude Haiku or any model.
---

# Extract data from purchase orders, packing lists and bills of lading

Purchase orders, packing lists and bills of lading often arrive as scanned PDFs, phone photos or Word files, and the item tables run across pages. ReadAnyDoc reads each page with a vision model, rebuilds the tables, and maps the document onto your own pydantic class.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class OrderLine(BaseModel):
    line_no: Optional[str]
    sku: Optional[str]
    description: str
    quantity: Optional[float]
    unit: Optional[str]
    unit_price: Optional[float]
    amount: Optional[float]

class PurchaseOrder(BaseModel):
    buyer: str
    supplier: Optional[str]
    po_number: Optional[str]
    order_date: Optional[str] = Field(description="as printed")
    requested_delivery_date: Optional[str]
    ship_to: Optional[str]
    incoterms: Optional[str]
    currency: Optional[str]
    line_items: list[OrderLine]
    subtotal: Optional[float]
    tax: Optional[float]
    total: Optional[float]

out = readanydoc.extract("purchase_order.pdf", schema=PurchaseOrder)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

Packing lists and bills of lading use the same call with a different schema. For a bill of lading:

```python
from typing import Optional
from pydantic import BaseModel, Field

class Package(BaseModel):
    marks: Optional[str]
    description: str
    packages: Optional[int]
    gross_weight: Optional[float]
    volume: Optional[float]

class BillOfLading(BaseModel):
    bl_number: Optional[str]
    shipper: str
    consignee: Optional[str]
    notify_party: Optional[str]
    carrier: Optional[str]
    vessel_voyage: Optional[str]
    port_of_loading: Optional[str]
    port_of_discharge: Optional[str]
    issue_date: Optional[str] = Field(description="as printed")
    packages: list[Package]
    total_gross_weight: Optional[float]
    weight_unit: Optional[str]

bl = readanydoc.extract("bill_of_lading.pdf", schema=BillOfLading)
```

## Tips

- Check `out.citations["total"]` to show reviewers where the order total was read.
- Long orders and packing lists with many rows: pass `array_extract=True`; rows are checked against the parsed tables and a gap is reported in `warnings`.
- Keep units with the numbers. Ask for `weight_unit` or `unit` as its own field rather than stripping it from the value, so `12.5 KGS` and `12.5 LBS` are not confused.
- Keep identifiers as strings. PO numbers, SKUs and B/L numbers can have leading zeros or letters.
- Add `instructions="Amounts as numbers without currency symbols; dates as printed"` to steer formatting.

## Next

- [Extract data from invoices](invoices.md), the same pattern for billing documents
- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
