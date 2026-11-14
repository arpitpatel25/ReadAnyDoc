"""Per-page spending cap. Off unless a cap is set.

The page read is always made. Every model call after it (zoomed strips, the handwriting read, verify
re-reads, table-only reads, form reads, formula crops) is optional: before such a call the page's spend so
far plus an estimate of the call is compared with the cap, and when it would pass the cap the call is skipped.
The page then simply keeps what it has. What was skipped is recorded in ``page.properties["spend_cap"]`` and
summed into ``usage.model_usage.spend_cap`` of the response.

Setting it: Read stage option ``page_cap_usd`` (USD per page) and / or ``page_cap_tokens`` (input + output
tokens per page, all models), or per request ``settings.page_cap_usd`` / ``settings.page_cap_tokens``
(additive fields; the request can only lower a cap the profile sets). With a cap set nothing is started
before the page read (no prefetch): a prefetched call is paid before anyone knows what the page read cost.

Spend is counted from the tokens of ``doc.usage`` at the list prices of ``readers/usage.py``; a replayed
call counts with the tokens of the original call. The estimate of a call is a multiple of the page read's
own cost (the 90th percentile of that ratio over the 639 development-set pages): a call
that turns out dearer than its estimate can take a page over the cap by the difference, which is recorded
as ``over_usd``.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ...model import Document, Page
from ...readers.usage import PRICES, family

KEY = "page_cap"
# cost of one call of a step as a multiple of the page read's cost (90th percentile on the development set)
RATIO = {"zoom_strip": 0.9, "table": 1.5, "form": 1.1, "verify": 1.8, "formula": 0.8, "region": 1.5,
         "handwriting": 0.7}  # handwriting: times the price ratio of its model (Sonnet / Haiku = 20): 14 page reads
_LOCK = threading.Lock()


def configure(doc: Document, usd: Any = None, tokens: Any = None) -> Optional[dict]:
    """Called once by Read. Stores the cap on the document so that later stages see it."""
    cap = {}
    if usd not in (None, "", 0, 0.0, False):
        cap["usd"] = float(usd)
    if tokens not in (None, "", 0, False):
        cap["tokens"] = int(tokens)
    if cap:
        doc.meta[KEY] = cap
    return cap or None


def cap_of(doc: Document) -> Optional[dict]:
    return doc.meta.get(KEY) or None


def active(doc: Document) -> bool:
    return bool(doc.meta.get(KEY))


def entry_usd(e: dict) -> float:
    pi, po = PRICES.get(family(str(e.get("model") or e.get("reader") or "")), PRICES["haiku"])
    usd = ((e.get("input_tokens") or 0) * pi + (e.get("output_tokens") or 0) * po) / 1e6
    return usd * (0.5 if e.get("batch") else 1.0)


def spent(doc: Document, page_index: int) -> tuple[float, int]:
    """(USD, tokens) of every model call recorded for the page so far."""
    usd, tok = 0.0, 0
    for e in list(doc.usage):
        if e.get("page") != page_index or (e.get("input_tokens") is None and e.get("output_tokens") is None):
            continue
        usd += entry_usd(e)
        tok += int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
    return usd, tok


def page_read(doc: Document, page_index: int) -> tuple[float, int]:
    """(USD, tokens) of the page read itself: the first call of the page that is not an extra."""
    for e in list(doc.usage):
        if e.get("page") == page_index and not e.get("escalation") and e.get("input_tokens") is not None:
            return entry_usd(e), int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
    return 0.0, 0


def estimate(doc: Document, page_index: int, step: str, calls: int = 1, reader: str = "") -> tuple[float, int]:
    """Expected (USD, tokens) of ``calls`` calls of ``step`` on this page."""
    usd0, tok0 = page_read(doc, page_index)
    ratio = RATIO.get(step, RATIO["region"]) * max(calls, 1)
    usd = usd0 * ratio
    if reader:  # a call to another model family costs that family's price for about the same tokens
        base = PRICES["haiku"]
        other = PRICES.get(family(reader), base)
        usd *= other[1] / base[1]
    return usd, int(tok0 * ratio)


def allow(doc: Document, page: Page, step: str, calls: int = 1, reader: str = "", tag: str = "") -> bool:
    """True when there is no cap or the step still fits. A refused step is recorded on the page."""
    cap = cap_of(doc)
    if not cap:
        return True
    usd, tok = spent(doc, page.index)
    est_usd, est_tok = estimate(doc, page.index, step, calls, reader)
    ok = (("usd" not in cap or usd + est_usd <= cap["usd"] + 1e-12)
          and ("tokens" not in cap or tok + est_tok <= cap["tokens"]))
    with _LOCK:
        rec = page.properties.setdefault("spend_cap", {**{f"cap_{k}": v for k, v in cap.items()}, "skipped": []})
        if not ok:
            rec["skipped"].append({"step": step if not tag else f"{step}:{tag}", "calls": calls,
                                   "est_usd": round(est_usd, 6), "spent_usd": round(usd, 6)})
    return ok


def close(doc: Document) -> Optional[dict]:
    """Final figures per page, and the summary for the response. Called when the pipeline's model steps are over
    (GroundRegions) and again by the API when it builds the usage; safe to call more than once."""
    cap = cap_of(doc)
    if not cap:
        return None
    out = {**{f"cap_{k}": v for k, v in cap.items()}, "pages": len(doc.pages), "pages_with_skips": 0,
           "pages_over": 0, "skipped": []}
    for page in doc.pages:
        usd, tok = spent(doc, page.index)
        rec = page.properties.setdefault("spend_cap", {**{f"cap_{k}": v for k, v in cap.items()}, "skipped": []})
        rec["spent_usd"], rec["spent_tokens"] = round(usd, 6), tok
        over = max(0.0, usd - cap["usd"]) if "usd" in cap else 0.0
        over_tok = max(0, tok - cap["tokens"]) if "tokens" in cap else 0
        rec.pop("over_usd", None)
        rec.pop("over_tokens", None)
        if over > 1e-9:
            rec["over_usd"] = round(over, 6)
        if over_tok:
            rec["over_tokens"] = over_tok
        if over > 1e-9 or over_tok:
            out["pages_over"] += 1
            read_usd, read_tok = page_read(doc, page.index)
            rec["page_read_usd"] = round(read_usd, 6)
        if rec["skipped"]:
            out["pages_with_skips"] += 1
            out["skipped"] += [{"page": page.index + 1, **s} for s in rec["skipped"]]
    return out


def summary(docs: list) -> Optional[dict]:
    """One record for a response built from several documents."""
    parts = [p for p in (close(d) for d in docs) if p]
    if not parts:
        return None
    out = dict(parts[0])
    for p in parts[1:]:
        for k in ("pages", "pages_with_skips", "pages_over"):
            out[k] += p[k]
        out["skipped"] = out["skipped"] + p["skipped"]
    return out
