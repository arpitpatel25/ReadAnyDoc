"""Extract fields into your own pydantic class, with the place on the page each value came from.

    python examples/extract_typed.py invoice.pdf
"""

import sys
from typing import Optional

from pydantic import BaseModel

import readanydoc


class LineItem(BaseModel):
    description: str
    amount: float


class Invoice(BaseModel):
    number: str
    issued: Optional[str]
    total: float
    lines: list[LineItem]


out = readanydoc.extract(sys.argv[1], schema=Invoice)

invoice = out.data                      # an Invoice instance (a dict, with a warning, if it did not validate)
print(invoice)
for citation in out.citations.get("total", []):
    if citation.bbox:
        print(f"total {citation.content!r} read on page {citation.bbox.page}")
