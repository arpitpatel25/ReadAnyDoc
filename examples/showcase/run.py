"""Reproduce the showcase: parse both documents and extract fields with citations.

    pip install "readanydoc[engine]"
    export ANTHROPIC_API_KEY=...
    python examples/showcase/run.py            # about 2 cents, under two minutes
    python examples/showcase/draw_citations.py # draws the citation boxes onto the Loan Estimate
"""
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

import readanydoc

HERE = Path(__file__).parent


class LoanEstimate(BaseModel):
    lender: str
    applicants: list[str]
    property_address: Optional[str]
    sale_price: Optional[float]
    loan_amount: Optional[float]
    interest_rate_percent: Optional[float]
    monthly_principal_and_interest: Optional[float]
    loan_term: Optional[str]
    has_prepayment_penalty: Optional[bool]
    estimated_closing_costs: Optional[float]
    estimated_cash_to_close: Optional[float]


class Line(BaseModel):
    description: str
    amount: Optional[float]


class Invoice1904(BaseModel):
    city: Optional[str]
    date: Optional[str] = Field(description="the date as printed or written")
    line_items: list[Line]
    total: Optional[float] = Field(description="the sum at the bottom of the amount column, if legible")


for name, schema in [("loan_estimate", LoanEstimate), ("invoice_1904", Invoice1904)]:
    src = HERE / f"{name}.pdf"
    parsed = readanydoc.parse(src, citations=True)
    (HERE / f"{name}.md").write_text(parsed.markdown)
    out = readanydoc.extract(src, schema=schema)
    result = {
        "data": out.data.model_dump(),
        "citations": {k: [c.model_dump(exclude_none=True) for c in v] for k, v in out.citations.items()},
        "warnings": out.warnings + parsed.warnings,
        "cost_usd": (out.usage.cost_usd or 0) + (parsed.usage.cost_usd or 0),
        "seconds": round(out.usage.seconds + parsed.usage.seconds, 1),
        "models": sorted(set(out.usage.by_model) | set(parsed.usage.by_model)),
    }
    (HERE / f"{name}.json").write_text(json.dumps(result, indent=2))
    print(name, json.dumps(result["data"], indent=2)[:600], "\nwarnings:", result["warnings"])
