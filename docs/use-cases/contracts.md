---
title: Extract clauses and terms from contracts
description: Pull parties, dates, terms and clauses out of long contracts and agreements (PDF or Word) into structured data with citations.
---

# Extract clauses and terms from contracts

Contracts are long, mostly prose, and the answer to a question is often one sentence on page 23. ReadAnyDoc parses the whole document, keeps section structure, and extracts into your schema with a citation for each value so a reviewer can jump straight to the clause.

```bash
pip install "readanydoc[engine]"
export ANTHROPIC_API_KEY=...
```

```python
import readanydoc
from typing import Optional
from pydantic import BaseModel, Field

class Contract(BaseModel):
    parties: list[str]
    effective_date: Optional[str]
    term: Optional[str]
    governing_law: Optional[str]
    termination_notice_days: Optional[int]
    auto_renews: Optional[bool]
    liability_cap: Optional[str] = Field(description="as written")

out = readanydoc.extract("agreement.pdf", schema=Contract,
                         instructions="Quote amounts and periods as written.")

print(out.data)            # an instance of your class
print(out.citations)       # where each value was read: text, page, box, confidence
print(out.warnings)        # anything to check
```

## Tips

- DOCX works directly, without converting to PDF.
- Yes/no answers that are not printed as one phrase cite the passage they rest on, with `low` confidence.
- Long contracts: the document is read in parts automatically above about 120,000 characters.

## Next

- [See two real documents end to end](../examples/real-documents.md), with every citation drawn on the page
- [Extract guide](../guides/extract.md) and [Parse guide](../guides/parse.md)
- Already on Reducto? [Switch in one URL](../migrate/from-reducto.md)
- Questions: [GitHub Discussions](https://github.com/arpitpatel25/ReadAnyDoc/discussions)
