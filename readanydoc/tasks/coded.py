"""Extraction for a schema too complex for one structured-output call ("coded" answers).

The API refuses a structured-output schema whose compiled grammar is too large, and a different format in each
call stops the calls from sharing the cached document (changing ``output_config.format`` invalidates the prompt
cache). So a schema the API refuses is asked in one fixed format, the same in every call:

    {"values": [{"code": "f17", "item": "0.1", "value": "4200"}, ...]}

- ``code`` is one of the schema's own field codes (an enum of every code, so the API rejects invented fields);
- ``item`` holds the list item numbers, one per list on the field's path ("" outside lists);
- ``value`` is the value as text, converted to the user's types afterwards like every reply-schema answer.

Each call starts with the same system prompt and the same document (cached), then lists only its own fields.
``rebuild`` turns the entries back into an object in the reply schema's shape; the caller converts that to the
user's types and checks it against the user's schema, as for any other answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

LIST = "[]"

NOTE = ('Answer as {"values": [...]} with one entry per value: "code" is the field\'s code from the list above, '
        '"value" the value as text. For a field inside lists (a [] in its path), "item" gives the item numbers '
        'from 0, one per [], joined by dots ("0", "2.1"): every field of the same list item uses the same number. '
        'Outside lists "item" is "". For every list item you write, give every one of its fields that the '
        "document states or makes clear for that item. A value the document states once for a whole table, "
        "column, section or page (a unit, a scale such as \"in thousands\", a currency, a date or period in a "
        "heading) applies to every item it covers: write it on each of those items. Leave out only a field the "
        "document gives no value for and does not make clear; never write a guess. A field marked \"always answer\" is "
        "required in every list item or section you write: never leave it out there; write the choice or value "
        "the document\'s terms mean (it never makes you write an item or section the document does not have). A value the document gives in "
        "another form is not a guess: write it in the form the field asks for (a country name as its code). "
        'Write numbers as plain digits ("4200.50", "-1200") and dates in the format the field asks for. '
        'Copy names, titles and labels exactly as printed, every word of them, even when part of a name looks like '
        'a plan, size or detail.')


@dataclass
class Leaf:
    code: str
    path: tuple  # field names, with LIST for every list on the way
    desc: str
    unit: tuple  # the top-level object path or the outermost list this field belongs to (never split)

    @property
    def lists(self) -> int:
        return sum(1 for p in self.path if p == LIST)

    def text(self) -> str:
        return ".".join(p if p != LIST else "[]" for p in self.path).replace(".[]", "[]")


def _describe(node: dict) -> str:
    bits = []
    raw = node.get("enum")
    enum = [e for e in (raw or []) if e != ""]
    if enum:  # no "" choice: the user's schema requires this yes/no or enum (reply_schema.to_reply)
        bits.append(("always answer, " if "" not in raw else "") + "one of: " + ", ".join(str(e) for e in enum))
    if node.get("description"):
        bits.append(str(node["description"]))
    if "anyOf" in node:
        bits.append("any of these forms: " + "; ".join(_describe(b) or "text" for b in node["anyOf"]))
    return " | ".join(bits) or "text"


def build(reply: dict) -> list[Leaf]:
    """Every leaf field of a reply schema, in schema order, with its code (f1, f2...)."""
    out: list[Leaf] = []

    def go(node: Any, path: tuple, unit: Optional[tuple]) -> None:
        # unit: the outermost list on the way (its fields are asked together), else the field itself
        if not isinstance(node, dict):
            return
        if "anyOf" in node and all(isinstance(b, dict) and b.get("type") == "object" for b in node["anyOf"]):
            node = _merge_objects(node["anyOf"])  # "fixed {strategy, value} or metered {strategy}": both forms' fields
        if node.get("type") == "object" and node.get("properties"):
            for k, sub in node["properties"].items():
                go(sub, path + (k,), unit)
            return
        if node.get("type") == "array":
            go(node.get("items") or {"type": "string"}, path + (LIST,), unit if unit is not None else path + (LIST,))
            return
        out.append(Leaf(code=f"f{len(out) + 1}", path=path, desc=_describe(node),
                        unit=unit if unit is not None else path))

    go(reply, (), None)
    return out


def _merge_objects(branches: list[dict]) -> dict:
    """One object with every field of every branch; a field in several branches takes all their allowed values.
    The answer is matched back to the right branch when it is converted to the user's types."""
    props: dict = {}
    for b in branches:
        for k, sub in (b.get("properties") or {}).items():
            if k in props and isinstance(props[k].get("enum"), list) and isinstance(sub.get("enum"), list):
                props[k] = {**props[k], "enum": list(dict.fromkeys(props[k]["enum"] + sub["enum"]))}
            else:
                props.setdefault(k, sub)
    return {"type": "object", "properties": props}


def format_schema(leaves: list[Leaf], alt: Optional[dict] = None, enum_codes: bool = True) -> dict:
    """The one structured-output format used by every call for this schema."""
    code: dict = {"type": "string"}
    if enum_codes:
        code["enum"] = [l.code for l in leaves]
    item = {"type": "object", "properties": {"code": code, "item": {"type": "string"}, "value": {"type": "string"}},
            "required": ["code", "item", "value"], "additionalProperties": False}
    props: dict = {"values": {"type": "array", "items": item}}
    if alt is not None:
        props["alternatives"] = alt
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def lines(leaves: list[Leaf]) -> str:
    return "\n".join(f"{l.code} | {l.text()} | {l.desc}" for l in leaves)


def groups(leaves: list[Leaf], max_leaves: int) -> list[list[Leaf]]:
    """Fields packed into groups of about ``max_leaves`` in schema order; a list's fields stay together, so the
    item numbers of one list are always written by one call."""
    units: dict[tuple, list[Leaf]] = {}
    for l in leaves:
        units.setdefault(l.unit, []).append(l)
    out: list[list[Leaf]] = []
    size = lambda ls: sum(1 for l in ls if l.path[-1:] != ("_from",))  # noqa: E731 - a citation note is not a field
    for u in units.values():
        if out and size(out[-1]) + size(u) <= max_leaves:
            out[-1].extend(u)
        else:
            out.append(list(u))
    return out


def split(group: list[Leaf]) -> Optional[list[list[Leaf]]]:
    """Two halves of a group whose reply was cut off, on unit boundaries; None for a single unit."""
    units: list[list[Leaf]] = []
    for l in group:
        if units and units[-1][0].unit == l.unit:
            units[-1].append(l)
        else:
            units.append([l])
    if len(units) < 2:
        return None
    half = (len(units) + 1) // 2
    return [[l for u in units[:half] for l in u], [l for u in units[half:] for l in u]]


def rebuild(data: Any, leaves: dict[str, Leaf]) -> tuple[dict, dict]:
    """(object in the reply schema's shape, counts for the trace) from a coded reply. Entries with an unknown
    code or the wrong number of item numbers are dropped and counted; a repeated entry keeps the first value."""
    stats = {"entries": 0, "kept": 0, "empty": 0, "unknown_code": 0, "bad_item": 0, "duplicate": 0}
    root: dict = {}
    seen: set = set()
    entries = data.get("values") if isinstance(data, dict) else None
    for e in entries if isinstance(entries, list) else []:
        stats["entries"] += 1
        if not isinstance(e, dict):
            stats["bad_item"] += 1
            continue
        leaf = leaves.get(str(e.get("code", "")).strip())
        if leaf is None:
            stats["unknown_code"] += 1
            continue
        value = e.get("value")
        value = "" if value is None else str(value)
        if not value.strip():
            stats["empty"] += 1
            continue
        raw = str(e.get("item", "") or "").strip()
        try:
            idx = [int(x) for x in raw.split(".")] if raw else []
        except ValueError:
            idx = None
        if idx is None or len(idx) != leaf.lists or any(i < 0 for i in idx):
            stats["bad_item"] += 1
            continue
        key = (leaf.code, tuple(idx))
        if key in seen:
            stats["duplicate"] += 1
            continue
        seen.add(key)
        _put(root, leaf.path, idx, value)
        stats["kept"] += 1
    return _lists(root), stats


def _put(root: dict, path: tuple, idx: list[int], value: str) -> None:
    node: Any = root
    k = 0
    for i, name in enumerate(path):
        last = i == len(path) - 1
        if name == LIST:
            continue
        nxt_list = i + 1 < len(path) and path[i + 1] == LIST
        if last:
            node[name] = value
            return
        if nxt_list:
            bucket = node.setdefault(name, {"__list__": {}})["__list__"]
            n = idx[k]
            k += 1
            if i + 2 == len(path):  # a list of plain values
                bucket[n] = value
                return
            node = bucket.setdefault(n, {})
        else:
            node = node.setdefault(name, {})


def _lists(node: Any) -> Any:
    """Item buckets ({"__list__": {0: ..., 2: ...}}) into lists in item order (gaps closed)."""
    if isinstance(node, dict):
        if set(node) == {"__list__"}:
            return [_lists(node["__list__"][i]) for i in sorted(node["__list__"])]
        return {k: _lists(v) for k, v in node.items()}
    return node
