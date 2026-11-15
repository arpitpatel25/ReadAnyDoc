---
title: Extract transactions from bank statements
description: Parse bank statement PDFs into a list of transactions (date, description, amount, balance) with citations, using an open-source Reducto alternative.
---

# Extract transactions from bank statements

Statements are long tables that continue page after page, often with a running balance and headers repeated on every page. ReadAnyDoc reads every page, keeps the table structure, and extracts long lists in parts so rows are not lost.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class Transaction(BaseModel):
    date: str
    description: str
    amount: float = Field(description="negative for money out")
    balance: Optional[float]

class Statement(BaseModel):
    bank: Optional[str]
    account_number_last4: Optional[str]
    period_start: Optional[str]
    period_end: Optional[str]
    opening_balance: Optional[float]
    closing_balance: Optional[float]
    transactions: list[Transaction]

out = readanydoc.extract("statement.pdf", schema=Statement, array_extract=True)

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- `array_extract=True` splits a long statement into parts and merges the lists; a part that returns far fewer rows than its tables have is extracted again.
- Check that opening balance plus transactions equals the closing balance in your own code; when it does not, `out.citations` shows which rows to look at.
- Use `pages=[1, 2]` while you develop the schema to keep cost down.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
