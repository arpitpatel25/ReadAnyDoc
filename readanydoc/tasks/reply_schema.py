"""The schema the model answers in, so that structured outputs work for schemas of any size.

The API compiles a structured-output schema into a grammar and limits it: 24 optional fields and 16 fields with
more than one type per request ("string or null" counts as two types). A typical extraction schema, every field
nullable, is over both after a dozen fields, so the model used to write free JSON, which can break.

``to_reply`` rewrites the user's schema (``$ref``-free) into one the API accepts, in code, before the call:

- every field is required, and "or null" is dropped: an empty string means "not in the document", an empty list
  an empty list; a number or text is never forced to hold a guess, because "" is always a valid answer for it;
- a yes/no or enum field that the user's schema requires and does not allow null has no "" choice: the model has
  to answer it (as the user's schema demands), so the output does not break the user's own validation;
- numbers, integers and booleans become strings (the value as printed: "$4,200.00", "yes"), so they need no
  null either; enums keep their values plus "".

``from_reply`` turns the answer back into the user's types ("" -> null, "4,200" -> 4200, "yes" -> true); the
caller then checks it against the user's own schema as before. ``plan`` splits a schema that is still too
large into groups of fields, decided before any call, never inside a list. The user sees only their schema.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

from . import schema as S

MAX_LEAVES = 40      # fields per call: larger schemas are split into groups (Reducto advises < 50 per schema). The
                     # API also caps the compiled grammar's size: a nested 45-field group with enums was refused
                     # live, so a group the API still refuses is split further (split_group)
MAX_UNIONS = 16      # the API's limit on fields with more than one type (anyOf branches left after the rewrite)

NAMES = ("Copy names, titles and labels exactly as printed, every word of them, even when part of a name looks "
         "like a plan, size or detail.")
NOTE = ('Every field in the JSON Schema is required. Write "" for a field (or [] for a list) when the document '
        'gives no value for it and does not make one clear; never fill a field with a guess. A value the document '
        'gives in another form is not a guess: write it in the form the field asks for (a country name as its '
        'code, a yes/no that the terms answer). Write numbers, dates and yes/no answers as text: numbers as plain '
        'digits ("4200.50", "-1200"), dates in the format the schema description asks for.' + " " + NAMES)

_KEEP = ("description", "title")


_RULES = (("format", "format: {}"), ("pattern", "pattern: {}"), ("minimum", "minimum {}"), ("maximum", "maximum {}"),
          ("exclusiveMinimum", "more than {}"), ("exclusiveMaximum", "less than {}"), ("minLength", "at least {} characters"),
          ("maxLength", "at most {} characters"), ("multipleOf", "a multiple of {}"))


def _desc(node: dict, extra: str = "") -> dict:
    """Title and description kept; constraints the reply schema cannot carry (a pattern must also allow "") are
    kept as words in the description, so the model still sees them. The answer is checked against them after."""
    out = {k: node[k] for k in _KEEP if k in node}
    rules = [t.format(node[k]) for k, t in _RULES if k in node]
    hint = "; ".join(x for x in [extra, *rules] if x)
    if hint:
        out["description"] = (out.get("description", "") + (" " if out.get("description") else "") + f"({hint})")
    return out


def _live(node: dict) -> list:
    """The non-null branches of a union, or [node]."""
    br = S._branches(node)
    if not br:
        return [node]
    return [b for b in br if S.types_of(b) != {"null"} and b != {"type": "null"}]


def to_reply(schema: Any, must: bool = True) -> Any:
    """The structured-output version of a resolved schema (see the module docstring).

    ``must``: the user's schema requires this value and does not allow null, and every object above it is always
    present (required, not nullable). A yes/no, enum or const field like that gets no "" choice: the API makes the
    model answer it, so the output never breaks the user's required type. Other required fields keep "" (no
    number or text is ever made up); one that comes back empty is reported (``required_warnings``)."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    must = must and not _allows_null(schema)
    live = _live(schema)
    if len(live) == 1 and live[0] is not schema:
        merged = {**{k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}, **live[0]}
        return to_reply(merged, must)
    if len(live) > 1:
        return {"anyOf": [to_reply(b, False) for b in live], **_desc(schema)}
    ts = S.types_of(schema) - {"null"}
    empty = [] if must else [""]
    if "const" in schema:
        return {"type": "string", "enum": [str(schema["const"])] + empty, **_desc(schema)}
    if S.is_object(schema) or ("object" in ts and "properties" in schema):
        req, forced = set(schema.get("required") or []), _forced(schema, must)
        props = {k: to_reply(v, k in forced or (must and k in req)) for k, v in (schema.get("properties") or {}).items()}
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False,
                **_desc(schema)}
    if "array" in ts or "items" in schema:
        return {"type": "array", "items": to_reply(schema.get("items") or {"type": "string"}), **_desc(schema)}
    enum = schema.get("enum")
    if isinstance(enum, list):
        vals = [str(v).lower() if isinstance(v, bool) else str(v) for v in enum if v is not None]
        return {"type": "string", "enum": list(dict.fromkeys(vals + empty)) or [""], **_desc(schema)}
    if ts == {"boolean"}:
        return {"type": "string", "enum": ["true", "false"] + empty, **_desc(schema)}
    if ts & {"number", "integer"} and "string" not in ts:
        return {"type": "string", **_desc(schema, "a number")}
    return {"type": "string", **_desc(schema)}


def from_reply(value: Any, schema: Any) -> Any:
    """The answer in the user's types: "" -> null, number and yes/no text converted, enum spelling matched."""
    if not isinstance(schema, dict):
        return value
    nullable = _allows_null(schema)
    schema = S.branch_for(schema, value)
    live = _live(schema)
    if len(live) == 1 and live[0] is not schema:
        schema = {**{k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}, **live[0]}
    if S.is_object(schema):
        if not isinstance(value, dict):
            return None if value in ("", None) else value
        out = {k: from_reply(value.get(k), sub) for k, sub in schema["properties"].items()} | \
            {k: v for k, v in value.items() if k not in schema["properties"]}
        # an optional object the document does not have comes back as null, as the user's schema allows; its
        # yes/no and enum fields that had to be answered (``_forced``) do not count as something found
        forced = _forced(schema, False)
        return None if nullable and _blank({k: v for k, v in out.items() if k not in forced}) else out
    if S.is_array(schema):
        if value in ("", None):
            return []
        if not isinstance(value, list):
            return value
        items = schema.get("items") or {}
        out = [from_reply(v, items) for v in value]
        live = S.branch_for(items, {}) if isinstance(items, dict) else None
        forced = _forced(live, False) if isinstance(live, dict) and S.is_object(live) else set()
        if forced:  # an item holding only answers it was made to give is not an item the document has
            out = [x for x in out if not (isinstance(x, dict) and _blank({k: v for k, v in x.items() if k not in forced}))]
        return out
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.lower() in ("null", "none") and nullable:
            return None  # "null" written as text where the reply schema asks for "": the user's schema allows null
        enum = schema.get("enum")
        if isinstance(enum, list):
            for e in enum:
                if e is not None and str(e).lower() == v.lower():
                    return e
                if isinstance(e, bool) and v.lower() == str(e).lower():
                    return e
        if "const" in schema and str(schema["const"]).lower() == v.lower():
            return schema["const"]
        ts = S.types_of(schema)
        if "boolean" in ts and "string" not in ts:
            low = v.lower()
            if low in ("true", "yes", "y", "x", "checked", "1"):
                return True
            if low in ("false", "no", "n", "unchecked", "0"):
                return False
            return None
        return value  # numbers: S.conform converts "4,200" / "(1,200)" against the user's schema
    return value


def _choice(node: Any) -> bool:
    """A yes/no, enum or const field: one whose answer can be forced without making up a number or text."""
    if not isinstance(node, dict):
        return False
    live = _live(node)
    n = live[0] if len(live) == 1 else node
    return isinstance(n, dict) and ("const" in n or isinstance(n.get("enum"), list)
                                    or S.types_of(n) - {"null"} == {"boolean"})


def _forced(obj: dict, must: bool) -> set:
    """Fields of an object answered without a "" choice: required, not nullable, yes/no or enum. In an object that
    may be absent and has nothing else to find, none are forced (forcing them would make the object up)."""
    req, props = set(obj.get("required") or []), obj.get("properties") or {}
    keys = {k for k, v in props.items() if k in req and isinstance(v, dict) and not _allows_null(v) and _choice(v)}
    return keys if must or len(keys) < len(props) else set()


def _allows_null(schema: dict) -> bool:
    t = schema.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    return any(isinstance(b, dict) and _allows_null(b) for b in schema.get("anyOf") or schema.get("oneOf") or [])


def _blank(v: Any) -> bool:
    if isinstance(v, dict):
        return all(_blank(x) for x in v.values())
    if isinstance(v, list):
        return all(_blank(x) for x in v)
    return v is None or (isinstance(v, str) and not v.strip())


def _leaves(node: Any) -> int:
    """Leaf fields of a reply schema (a list's item fields counted once, as LlamaExtract counts them)."""
    if not isinstance(node, dict):
        return 1
    if "anyOf" in node:
        return sum(_leaves(b) for b in node["anyOf"])
    if node.get("type") == "object":
        # "_from" (citations only) is a free-text note, not a field to fill: not counted towards a group's size
        return sum(_leaves(v) for k, v in node.get("properties", {}).items() if k != "_from") or 1
    if node.get("type") == "array":
        return _leaves(node.get("items"))
    return 1


def _unions(node: Any) -> int:
    if not isinstance(node, dict):
        return 0
    n = 1 if "anyOf" in node else 0
    for b in node.get("anyOf", []):
        n += _unions(b)
    for v in (node.get("properties") or {}).values():
        n += _unions(v)
    if isinstance(node.get("items"), dict):
        n += _unions(node["items"])
    return n


def _fits(node: dict) -> bool:
    return _leaves(node) <= MAX_LEAVES and _unions(node) <= MAX_UNIONS


def plan(reply: dict) -> list[list[tuple]]:
    """Groups of field paths, each small enough for one structured call. One group (every top-level field) when
    the schema fits. Otherwise top-level fields are packed into groups; a field too big on its own is split into
    its own fields, recursively, but never inside a list (a list item is extracted whole)."""
    if reply.get("type") != "object":
        return [[()]]
    if _fits(reply):
        return [[(k,) for k in reply["properties"]]]
    units: list[tuple[tuple, int, int]] = []  # (path, leaves, unions)

    def split(node: dict, path: tuple) -> None:
        if _fits(node) or node.get("type") != "object" or not node.get("properties"):
            units.append((path, _leaves(node), _unions(node)))
            return
        for k, sub in node["properties"].items():
            split(sub, path + (k,))

    for k, sub in reply["properties"].items():
        split(sub, (k,))
    groups: list[list[tuple]] = []
    size = unions = 0
    for path, n, u in units:  # in schema order, so related fields stay together
        if groups and size + n <= MAX_LEAVES and unions + u <= MAX_UNIONS:
            groups[-1].append(path)
            size, unions = size + n, unions + u
        else:
            groups.append([path])
            size, unions = n, u
    return groups


def split_group(reply: dict, paths: list[tuple]) -> Optional[list[list[tuple]]]:
    """Two smaller groups for a group the API would not compile, or None when it cannot be split further (a single
    list, or a single field). An object split into its fields keeps its "_block" label in both parts."""
    if len(paths) > 1:
        half = (len(paths) + 1) // 2
        return [paths[:half], paths[half:]]
    node = reply
    for k in paths[0]:
        node = (node.get("properties") or {}).get(k, {})
    props = node.get("properties") or {}
    kids = [paths[0] + (k,) for k in props if k not in MARKS]
    if node.get("type") != "object" or len(kids) < 2:
        return None
    half = (len(kids) + 1) // 2
    label = [paths[0] + (m,) for m in MARKS if m in props]
    return [kids[:half] + label, kids[half:] + label]


def project(node: dict, paths: list[tuple]) -> dict:
    """The part of an object schema that holds ``paths`` (nesting kept)."""
    if not paths or paths == [()]:
        return node
    out = dict(node)  # same key order as the full schema
    props: dict = {}
    by_head: dict[str, list[tuple]] = {}
    for p in paths:
        by_head.setdefault(p[0], []).append(p[1:])
    for k, rest in by_head.items():
        sub = node["properties"][k]
        props[k] = sub if any(len(r) == 0 for r in rest) else project(sub, rest)
    out["properties"] = props
    if "required" in out:
        out["required"] = [r for r in out["required"] if r in props]
    return out


def merge(a: Any, b: Any) -> Any:
    """Answers of two groups put back together (objects merged key by key)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if k in out else v
        return out
    return b if a in (None, "", [], {}) else a


def add_blocks(reply: dict, blocks: bool = True) -> dict:
    """The reply schema with a "_block" field on every object below the root: the label of the block the
    object's values were read from. Used only for citations, to tell apart repeated values."""
    reply = copy.deepcopy(reply)

    def go(n: Any, top: bool) -> None:
        if not isinstance(n, dict):
            return
        for b in n.get("anyOf", []):
            go(b, top)
        if n.get("type") == "object":
            for v in n.get("properties", {}).values():
                go(v, False)
            if not top:
                if blocks:
                    n["properties"]["_block"] = {"type": "string",
                                                 "description": "label of the block these values were read from, e.g. r12"}
                n["properties"][FROM] = {"type": "string", "description": FROM_DESC}
                n["required"] = list(n.get("required", [])) + (["_block"] if blocks else []) + [FROM]
        if isinstance(n.get("items"), dict):
            go(n["items"], False)

    go(reply, True)
    return reply


FROM = "_from"
FROM_DESC = 'field: printed words, for values not printed as written'
MARKS = ("_block", FROM)


def parse_from(text: Any) -> dict[str, str]:
    """{field: printed words} from a "_from" answer ("data_period: Three Months Ended December 31, 2024")."""
    out: dict[str, str] = {}
    if not isinstance(text, str):
        return out
    for line in re.split(r"[\n;]+", text):
        if ":" not in line:
            continue
        k, w = line.split(":", 1)
        k, w = k.strip().strip('"\'` '), w.strip().strip('"\'` ')
        k = k.split(".")[-1].replace("[]", "").strip()  # "income_statement.revenue[].data_period" -> "data_period"
        if k and w and k not in out:
            out[k] = w
    return out


def take_marks(value: Any, path: tuple = (), out: Optional[dict] = None) -> dict:
    """Remove the "_block" and "_from" fields from an answer; {object path: {"block": label, "from": {...}}}."""
    out = {} if out is None else out
    if isinstance(value, dict):
        b = value.pop("_block", None)
        f = parse_from(value.pop(FROM, None))
        b = b.strip() if isinstance(b, str) and b.strip() else None
        if b or f:
            out[path] = {"block": b, "from": f}
        for k, x in value.items():
            take_marks(x, path + (k,), out)
    elif isinstance(value, list):
        for i, x in enumerate(value):
            take_marks(x, path + (i,), out)
    return out


def take_blocks(value: Any, path: tuple = (), out: Optional[dict] = None) -> dict:
    """Remove the "_block" (and "_from") fields from an answer; {object path: block label}."""
    out = {} if out is None else out
    if isinstance(value, dict):
        value.pop(FROM, None)
        b = value.pop("_block", None)
        if isinstance(b, str) and b.strip():
            out[path] = b.strip()
        for k, x in value.items():
            take_blocks(x, path + (k,), out)
    elif isinstance(value, list):
        for i, x in enumerate(value):
            take_blocks(x, path + (i,), out)
    return out


ALT_FIELD = "_alternatives"


def add_alternatives(reply: dict, fields: Optional[list[str]]) -> dict:
    """A root list where the model writes other values the document gives for a field (a special term, an
    amendment, a second total). Only when the request asks for it; ``fields`` limits it to those paths."""
    reply = copy.deepcopy(reply)
    which = (" Only for these fields: " + ", ".join(fields) + ".") if fields else ""
    reply["properties"][ALT_FIELD] = {
        "type": "array",
        "description": ("Other values the document itself gives for a field, besides the one in the answer (a "
                        "special term, an amendment, a crossed-out or second value). Leave empty when there are "
                        "none." + which),
        "items": {"type": "object", "properties": {
            "field": {"type": "string", "description": "path of the field, e.g. contract.price or items[0].amount"},
            "value": {"type": "string", "description": "the other value, written the way the field asks for its "
                      "answer (same format as the answer: a date in the field's date format, a number as digits)"},
            "printed": {"type": "string", "description": "the same value as printed in the document"},
            "note": {"type": "string", "description": "why it differs, in a few words"}},
            "required": ["field", "value", "printed", "note"], "additionalProperties": False}}
    reply["required"] = list(reply.get("required", [])) + [ALT_FIELD]
    return reply
