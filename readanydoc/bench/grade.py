"""Deterministic scoring of an extraction against an answer key. No model is called.

Every value of the answer key is a leaf on a path (``lines[0].amount``). A predicted leaf counts as right when it is
the same value:

- numbers: equal within 0.5% (or 0.01 for small values), whatever the printed form ("$1,575.00", "1.575,00",
  "60.000" as sixty thousand, "(200)" as -200);
- dates: the same day, whatever the format ("2026-03-09", "09/03/2026" read both ways, "March 9, 2026");
- booleans: equal;
- text: equal after case, spacing and punctuation are ignored, or, for text longer than 20 characters, a fuzzy
  match of 90 or more (rapidfuzz ``token_sort_ratio``).

List items are paired before comparing: each answer-key item is matched to the unused predicted item with the most
equal leaves, so a list in another order is not penalised. Scores:

- ``recall``: answer-key values found (the share of the key that is right in the output);
- ``precision``: output values that are right (the share of the output that is in the key);
- ``exact_documents``: documents with every answer-key value right;
- ``schema_valid`` (``schema_errors``): outputs that pass the task's JSON Schema, checked by the standard
  ``jsonschema`` validator (types, required fields, enums, patterns, ranges). A failed run counts as not valid;
- ``schema_valid_strict``: the same, except that ``null`` for a value the document does not give is also an error
  when the schema's type does not allow null (many schemas declare optional fields as plain ``string``; every system
  tested, Reducto included, returns ``null`` there). ``schema_valid`` accepts that ``null`` and nothing else.

``alternatives`` in an answer key (``{"path": [other accepted values]}``) are accepted too.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Optional

from rapidfuzz import fuzz

_MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def _blank(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def number(v: Any) -> Optional[float]:
    """A number from any common printed form, or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > 40:
        return None
    neg = bool(re.search(r"\(\s*[$€£¥]?\s*\d|-\s*[$€£¥]?\s*\d|\d\s*-$|(?i:\bcr\b)", s))
    s = re.sub(r"(?i)\b(cr|dr|usd|eur|gbp|zar|nad|zmw|idr|rp|inr|rs|r)\b", "", s)
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):                 # 60.000 / 1.250.000: dots group thousands
        s = s.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{1,2}", s):       # 1.575,00: European decimals
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", s):                     # 12,50
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def dates(v: Any) -> set:
    """Every calendar day a value can mean (both readings of 03/04/2026)."""
    if not isinstance(v, str):
        return set()
    s = v.strip().lower().replace(",", " ")
    out: set = set()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(t.*)?", s)
    if m:
        out.add((int(m[1]), int(m[2]), int(m[3])))
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        out |= {(y, a, b), (y, b, a)}
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})", s) or None
    if m and m[2] in _MONTHS:
        out.add((int(m[3]), _MONTHS[m[2]], int(m[1])))
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})\s+(\d{4})", s)
    if m and m[1] in _MONTHS:
        out.add((int(m[3]), _MONTHS[m[1]], int(m[2])))
    valid = set()
    for y, mo, d in out:
        try:
            _dt.date(y, mo, d)
            valid.add((y, mo, d))
        except ValueError:
            pass
    return valid


_NUMERIC = re.compile(r"(?i)\s*(usd|eur|gbp|zar|nad|zmw|idr|rp|inr|rs|r)?\s*[$€£¥]?\s*[(\-+]?\s*[$€£¥]?\s*\d[\d.,\s]*%?\)?"
                      r"\s*-?\s*(cr|dr|usd|eur|gbp|zar|idr)?\s*")


def _numeric(v: Any) -> bool:
    """A value that is a number, as a number or as printed digits (not an id such as "AC-87051")."""
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float)) or (isinstance(v, str) and bool(_NUMERIC.fullmatch(v)))


def _text(v: Any) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(v).lower()).split())


def same(gold: Any, pred: Any) -> bool:
    if _blank(gold) or _blank(pred):
        return _blank(gold) and _blank(pred)
    if isinstance(gold, bool) or isinstance(pred, bool):
        g = gold if isinstance(gold, bool) else str(gold).strip().lower() in ("true", "yes", "1")
        p = pred if isinstance(pred, bool) else str(pred).strip().lower() in ("true", "yes", "1")
        return g == p
    gd, pd = dates(gold), dates(pred)
    if gd and pd:
        return bool(gd & pd) if len(gd) == 1 or len(pd) == 1 else gd == pd
    gn, pn = number(gold), number(pred)
    if gn is not None and pn is not None and _numeric(gold) and _numeric(pred):
        return abs(gn - pn) <= max(0.01, 0.005 * abs(gn))
    g, p = _text(gold), _text(pred)
    if g == p or g.replace(" ", "") == p.replace(" ", ""):
        return True
    return len(g) > 20 and fuzz.token_sort_ratio(g, p) >= 90


def leaves(v: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(v, dict):
        return [x for k, sub in v.items() for x in leaves(sub, f"{path}.{k}" if path else str(k))]
    if isinstance(v, list):
        if v and not isinstance(v[0], (dict, list)):
            return [(f"{path}[{i}]", x) for i, x in enumerate(v)]
        return [x for i, sub in enumerate(v) for x in leaves(sub, f"{path}[{i}]")]
    return [(path, v)]


def _align(gold: Any, pred: Any) -> Any:
    """``pred`` with its list items reordered to face the answer key's items (unmatched ones at the end)."""
    if isinstance(gold, dict) and isinstance(pred, dict):
        return {k: _align(gold.get(k), v) for k, v in pred.items()}
    if isinstance(gold, list) and isinstance(pred, list):
        if not gold or not pred:
            return pred
        scalar = not isinstance(gold[0], (dict, list))
        free = list(range(len(pred)))
        out: list = [None] * len(gold)
        for gi, g in enumerate(gold):
            best, best_n = None, 0
            for pi in free:
                p = pred[pi]
                n = (1 if same(g, p) else 0) if scalar else sum(
                    1 for (gp, gv) in leaves(g) for (pp, pv) in leaves(_align(g, p)) if gp == pp and not _blank(gv)
                    and same(gv, pv))
                if n > best_n:
                    best, best_n = pi, n
            if best is not None:
                out[gi] = _align(g, pred[best])
                free.remove(best)
        return out + [pred[i] for i in free]
    return pred


def score(gold: Any, pred: Any, alternatives: Optional[dict] = None, only: Optional[list[str]] = None) -> dict:
    """Per-document scores, with every answer-key value marked right or wrong (for the report)."""
    pred = _align(gold, pred if pred is not None else {})
    want = [(p, v) for p, v in leaves(gold) if not _blank(v)]
    if only:
        want = [(p, v) for p, v in want if any(p == o or p.startswith(o + ".") or p.startswith(o + "[") for o in only)]
    got = dict((p, v) for p, v in leaves(pred))
    rows, right = [], 0
    for p, v in want:
        ok = same(v, got.get(p)) or any(same(a, got.get(p)) for a in (alternatives or {}).get(p, []))
        right += ok
        rows.append({"path": p, "expected": v, "got": got.get(p), "ok": ok})
    keys = {p for p, _ in want}
    out_vals = [(p, v) for p, v in got.items() if not _blank(v)]
    if only:
        out_vals = [(p, v) for p, v in out_vals if any(p == o or p.startswith(o + ".") or p.startswith(o + "[")
                                                       for o in only)]
    correct_out = sum(1 for p, v in out_vals if p in keys and any(r["ok"] for r in rows if r["path"] == p))
    return {"recall": right / len(want) if want else 1.0, "right": right, "expected": len(want),
            "precision": correct_out / len(out_vals) if out_vals else (1.0 if not want else 0.0),
            "written": len(out_vals), "exact": right == len(want), "fields": rows}


def summarize(per_doc: list[dict]) -> dict:
    """Totals over documents. A document that failed is scored as an empty answer, never left out."""
    if not per_doc:
        return {"documents": 0}
    right = sum(d["score"]["right"] for d in per_doc)
    expected = sum(d["score"]["expected"] for d in per_doc)
    written = sum(d["score"]["written"] for d in per_doc)
    correct_out = sum(round(d["score"]["precision"] * d["score"]["written"]) for d in per_doc)
    return {"documents": len(per_doc), "failed": sum(1 for d in per_doc if d.get("error")),
            "recall": right / expected if expected else None,
            "precision": correct_out / written if written else None,
            "exact_documents": sum(d["score"]["exact"] for d in per_doc) / len(per_doc)}


def _nullable(schema: Any) -> Any:
    """The schema with null allowed wherever a type or enum is given (for ``schema_valid``)."""
    if isinstance(schema, list):
        return [_nullable(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in ("properties", "patternProperties", "$defs", "definitions") and isinstance(v, dict):
            out[k] = {kk: _nullable(vv) for kk, vv in v.items()}
        elif k in ("items", "additionalProperties", "anyOf", "oneOf", "allOf", "not", "prefixItems"):
            out[k] = _nullable(v)
        else:
            out[k] = v
    t = out.get("type")
    if isinstance(t, str) and t != "null":
        out["type"] = [t, "null"]
    elif isinstance(t, list) and "null" not in t:
        out["type"] = t + ["null"]
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out["enum"] = out["enum"] + [None]
    return out


def schema_errors(data: Any, schema: Any, nulls_ok: bool = False) -> list[str]:
    """What breaks the JSON Schema in an output, by the standard validator (``jsonschema``), as "path: message".
    Not ReadAnyDoc's own checker, so the score does not depend on the code under test."""
    import jsonschema
    if not isinstance(schema, dict) or data is None:
        return ["no output"] if data is None else []
    if nulls_ok:
        schema = _nullable(schema)
    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    try:
        cls.check_schema(schema)
    except jsonschema.SchemaError:
        cls = jsonschema.Draft202012Validator  # a schema with a non-standard keyword is still checked for the rest
    out = []
    for e in cls(schema).iter_errors(data):
        out.append(("/".join(str(p) for p in e.absolute_path) or "(root)") + ": " + e.message[:160])
    return out


def score_case(case: dict, pred: Any) -> dict:
    """A case's own scorer when it names one (``"scorer": "sec"``), else ``score``."""
    if case.get("scorer") == "sec":
        from . import sec
        return sec.score(case["gold"], pred)
    return score(case["gold"], pred, case.get("alternatives"), case.get("only"))
