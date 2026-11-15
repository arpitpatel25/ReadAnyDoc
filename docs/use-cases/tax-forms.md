---
title: Extract data from tax forms (W-2, W-9, 1099)
description: Pull fields and checkboxes from US tax forms such as the W-2, W-9 and 1099, scanned or fillable, with word-level citations.
---

# Extract data from tax forms (W-2, W-9, 1099)

Tax forms are dense grids of numbered boxes, checkboxes and small print, and are often scanned or faxed. ReadAnyDoc reads the form layout, fillable PDF field values and checkboxes, and maps them onto your schema.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class W2(BaseModel):
    employee_name: Optional[str]
    employer_name: Optional[str]
    employer_ein: Optional[str]
    wages_box1: Optional[float]
    federal_tax_withheld_box2: Optional[float]
    social_security_wages_box3: Optional[float]
    state: Optional[str]
    tax_year: Optional[str]

out = readanydoc.extract("w2.pdf", schema=W2)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- Field descriptions that name the box (`Field(description="box 1")`) help on forms with many similar numbers.
- Blank forms return `null` for every field with warnings, rather than labels mistaken for values.
- The server can also fill forms: see [Edit](../guides/edit.md).

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
