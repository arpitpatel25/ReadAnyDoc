"""10-Q filings scored against the company's own tagged numbers (SEC XBRL), so the answer key needs no hand labels.

Every 10-Q filed with the SEC carries machine-readable facts (XBRL) the company tagged itself: revenue, net income,
cash, total assets... for each period in the filing. ``data.sec.gov/api/xbrl/companyfacts`` serves them, public
domain. A filing's facts are picked by its accession number, and only facts without a segment (company totals).

Two scores, both by code:

- found (recall): of the tagged core values (the schema metrics in ``MAP``, every period in the filing), how many
  appear in that metric's extracted list (value times scale within 0.5%, sign ignored: filings print losses in
  brackets);
- right (precision): of the company-level money and per-share values extracted, how many equal some value tagged
  in the filing. A value the company did not tag (a sub-line, a non-GAAP figure) counts as wrong, so precision is
  a lower bound.
"""

from __future__ import annotations

from typing import Any, Optional

# schema metric -> XBRL concepts (us-gaap) that state it
MAP = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"], "basic_eps": ["EarningsPerShareBasic"], "diluted_eps": ["EarningsPerShareDiluted"],
    "gross_profit": ["GrossProfit"], "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"], "operating_income": ["OperatingIncomeLoss"],
    "income_tax_expense": ["IncomeTaxExpenseBenefit"], "operating_expenses": ["OperatingExpenses", "CostsAndExpenses"],
    "restructuring_charge": ["RestructuringCharges"],
    "stock_based_compensation": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"], "goodwill": ["Goodwill"], "inventories": ["InventoryNet"],
    "total_assets": ["Assets"], "current_assets": ["AssetsCurrent"], "long_term_debt": ["LongTermDebtNoncurrent"],
    "accounts_payable": ["AccountsPayableCurrent"], "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "total_liabilities": ["Liabilities"], "current_liabilities": ["LiabilitiesCurrent"],
    "accounts_receivable": ["AccountsReceivableNetCurrent"], "shareholders_equity": ["StockholdersEquity"],
    "additional_paid_in_capital": ["AdditionalPaidInCapital", "AdditionalPaidInCapitalCommonStock"],
    "property_plant_and_equipment": ["PropertyPlantAndEquipmentNet"], "short_term_investments": ["ShortTermInvestments"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "share_repurchases": ["PaymentsForRepurchaseOfCommonStock"],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
}
RATIOS = {"gross_margin", "operating_margin", "revenue_growth", "net_income_growth", "effective_tax_rate"}
SECTIONS = ("balance_sheet", "income_statement", "cash_flow_statement")


def facts_of(companyfacts: dict, accession: str) -> list[dict]:
    """The numeric facts tagged in one filing: [{"concept", "val", "end"}]."""
    out = []
    for tax, concepts in (companyfacts.get("facts") or {}).items():
        for concept, c in concepts.items():
            for rows in (c.get("units") or {}).values():
                for r in rows:
                    if r.get("accn") == accession and isinstance(r.get("val"), (int, float)):
                        out.append({"concept": f"{tax}:{concept}", "val": r["val"], "end": r.get("end")})
    return out


def _num(e: dict) -> Optional[float]:
    try:
        v = float(str(e.get("value")).replace(",", "").replace("$", "").replace("(", "-").replace(")", ""))
    except (TypeError, ValueError):
        return None
    try:
        s = float(e.get("scale") or 1)
    except (TypeError, ValueError):
        s = 1.0
    return v * s


def _eq(a: float, b: float) -> bool:
    a, b = abs(a), abs(b)
    return abs(a - b) <= max(0.005 * b, 0.0051 if b < 100 else 0.5)


def score(gold: dict, data: Any) -> dict:
    """Scores in the shape of ``grade.score`` (recall = found, precision = right), one row per tagged value."""
    facts = gold.get("facts") or []
    pool = [f["val"] for f in facts]
    metrics: dict[str, list] = {}
    for sec in SECTIONS:
        for k, v in ((data or {}).get(sec) or {}).items() if isinstance(data, dict) else []:
            if isinstance(v, list):
                metrics[k] = [e for e in v if isinstance(e, dict)]
    rows, right = [], 0
    for m, concepts in MAP.items():
        want = sorted({f["val"] for f in facts if f["concept"].split(":", 1)[-1] in concepts})
        got = [x for x in (_num(e) for e in metrics.get(m, [])) if x is not None]
        for gv in want:
            hit = next((x for x in got if _eq(x, gv)), None)
            right += hit is not None
            rows.append({"path": m, "expected": gv, "got": hit, "ok": hit is not None})
    written = correct = 0
    for m, entries in metrics.items():
        if m in RATIOS:
            continue
        for e in entries:
            x = _num(e)
            if x is None or (e.get("segment_type") or "company") != "company":
                continue
            written += 1
            correct += any(_eq(x, p) for p in pool)
    return {"recall": right / len(rows) if rows else 1.0, "right": right, "expected": len(rows),
            "precision": correct / written if written else (1.0 if not rows else 0.0), "written": written,
            "exact": right == len(rows), "fields": rows}
