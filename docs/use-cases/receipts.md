---
title: Extract data from receipts
description: Read photos and scans of receipts into merchant, date, items, tax and total with citations, using Claude Haiku and an open-source parser.
---

# Extract data from receipts

Receipts are usually phone photos: tilted, creased, thermal print fading at the edges. ReadAnyDoc takes images directly (PNG, JPG, TIFF, WEBP; HEIC when the machine can decode it), fixes rotation and reads them, and returns each value with the place it came from.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class Receipt(BaseModel):
    merchant: str
    date: Optional[str]
    items: list[str]
    tax: Optional[float]
    total: Optional[float]
    payment_method: Optional[str]

out = readanydoc.extract("receipt.jpg", schema=Receipt)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- Values the model cannot read come back as `null` with a warning; nothing is guessed.
- `mode="model-only"` is faster and cheaper for simple single-page receipts.
- A whole folder: loop over files, or run the server and send async jobs.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
