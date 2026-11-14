"""JSON Schema enforcement for Extract (Reducto's documented guarantees).

Enforced: type, enum, properties, required (every declared field always present, null when missing),
items, minimum/maximum (and exclusive variants), minLength/maxLength, pattern, anyOf/oneOf,
local ``$ref``, closed objects. Best effort (described to the model, not enforced): format, const,
uniqueItems, allOf, if/then/else, not, contains, dependent*.

The pipeline is: ``check_schema`` (draft 2020-12, 400 on invalid) -> ``resolve_refs`` -> ``enforced``
(the strict schema) -> ``conform`` (deterministic shape repair) -> ``find_errors`` -> model repair
(caller) -> ``null_out`` (a value that still breaks the schema becomes null; it is never returned).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterator, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .common import TaskError

Path = tuple  # tuple of str keys and int indexes

BEST_EFFORT = ("format", "const", "uniqueItems", "allOf", "if", "then", "else", "not", "contains",
               "minContains", "maxContains", "dependentRequired", "dependentSchemas", "contentEncoding",
               "contentMediaType", "unevaluatedProperties", "unevaluatedItems", "propertyNames",
               "patternProperties", "prefixItems", "multipleOf")
_OPENING = ("allOf", "if", "then", "else", "patternProperties", "dependentSchemas")  # keep such objects open
_NULL = {"type": "null"}


def check_schema(schema: Any) -> None:
    """Validate the user's schema as draft 2020-12. Raises ``TaskError(400)`` when it is not valid."""
    if not isinstance(schema, dict):
        raise TaskError(400, "Invalid JSON schema: instructions.schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        where = "/".join(str(p) for p in e.absolute_path)
        raise TaskError(400, f"Invalid JSON schema: {e.message}" + (f" (at {where})" if where else "")) from e
    for node in _walk(schema):
        pat = node.get("pattern")
        if isinstance(pat, str):
            try:
                re.compile(pat)
            except re.error as e:
                raise TaskError(400, f"Invalid JSON schema: pattern {pat!r} is not a valid regular expression ({e})") from e


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _pointer(root: dict, ref: str) -> Any:
    if not ref.startswith("#"):
        raise TaskError(400, f"Invalid JSON schema: only local $ref is supported, got {ref!r}")
    node: Any = root
    for part in [p for p in ref[1:].split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise TaskError(400, f"Invalid JSON schema: $ref {ref!r} does not resolve")
    return node


def resolve_refs(schema: dict, max_recursion: int = 2) -> dict:
    """Inline every local ``$ref``. A recursive reference is expanded ``max_recursion`` times, then left open."""

    def go(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [go(v, stack) for v in node]
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if stack.count(ref) > max_recursion:
                return {}
            target = go(_pointer(schema, ref), stack + (ref,))
            rest = {k: go(v, stack) for k, v in node.items() if k != "$ref"}
            return {**target, **rest} if isinstance(target, dict) else target
        return {k: (v if k in ("enum", "const", "default", "examples") else go(v, stack))
                for k, v in node.items() if k not in ("$defs", "definitions")}

    return go(schema, ())


def types_of(schema: Any) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    t = schema.get("type")
    out = {t} if isinstance(t, str) else set(t) if isinstance(t, list) else set()
    if not out:
        if "properties" in schema:
            out.add("object")
        elif "items" in schema:
            out.add("array")
    return out


def is_object(schema: Any) -> bool:
    return isinstance(schema, dict) and "object" in types_of(schema) and isinstance(schema.get("properties"), dict)


def is_array(schema: Any) -> bool:
    return isinstance(schema, dict) and "array" in types_of(schema)


def _branches(schema: Any) -> Optional[list]:
    if isinstance(schema, dict):
        for k in ("anyOf", "oneOf"):
            if isinstance(schema.get(k), list):
                return schema[k]
    return None


def allows_null(schema: Any) -> bool:
    if schema is True or schema == {}:
        return True
    if not isinstance(schema, dict):
        return False
    if "null" in types_of(schema) or (isinstance(schema.get("enum"), list) and None in schema["enum"]):
        return True
    br = _branches(schema)
    return bool(br) and any(allows_null(b) for b in br)


def enforced(schema: Any) -> Any:
    """The strict schema the result must satisfy. Input must already have its refs resolved."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items() if k not in BEST_EFFORT and k not in ("$schema", "$id")}
    for k in ("anyOf", "oneOf"):
        if isinstance(out.get(k), list):
            out[k] = [enforced(b) for b in out[k]]
    if isinstance(out.get("properties"), dict):
        props = {}
        for name, sub in out["properties"].items():
            sub = enforced(sub)
            props[name] = sub if allows_null(sub) else {"anyOf": [sub, _NULL]}
        out["properties"] = props
        out["required"] = list(props)
        if not any(k in schema for k in _OPENING):
            out["additionalProperties"] = False
    if "items" in out:
        out["items"] = enforced(out["items"])
    return out


# ---------------------------------------------------------------- choose the branch a value belongs to

def branch_for(schema: Any, value: Any) -> Any:
    """For anyOf/oneOf pick the branch that matches the value's shape (object, array, scalar)."""
    br = _branches(schema)
    if not br:
        return schema
    live = [b for b in br if b != _NULL and types_of(b) != {"null"}]
    if value is None or not live:
        return schema
    if isinstance(value, dict):
        cand = [b for b in live if is_object(branch_for(b, value))]
    elif isinstance(value, list):
        cand = [b for b in live if is_array(branch_for(b, value))]
    else:
        cand = [b for b in live if not is_object(b) and not is_array(b)]
    cand = cand or live
    if len(cand) == 1:
        return branch_for(cand[0], value)
    for b in cand:
        b2 = branch_for(b, value)
        if not find_errors(value, b2):
            return b2
    return branch_for(cand[0], value)


# ---------------------------------------------------------------- deterministic conform

_NUM = re.compile(r"^[-+]?\(?[$€£¥₹]?\s*[-+]?\d[\d,]*(\.\d+)?\)?\s*%?$")


def _to_number(s: str) -> Optional[float]:
    t = s.strip()
    if not t or not _NUM.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"[^\d.\-+]", "", t)
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _coerce_scalar(value: Any, schema: dict) -> Any:
    ts = types_of(schema)
    if not ts or value is None:
        return value
    if isinstance(value, str):
        if "string" in ts:
            return value
        if not value.strip():
            return None
        if ts & {"number", "integer"}:
            n = _to_number(value)
            if n is not None:
                return int(n) if (n == int(n) and ("integer" in ts or "number" not in ts)) else n
        if "boolean" in ts and value.strip().lower() in ("true", "false", "yes", "no"):
            return value.strip().lower() in ("true", "yes")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if "integer" in ts and "number" not in ts and isinstance(value, float) and value == int(value):
            return int(value)
        if ts <= {"string", "null"}:
            return str(int(value)) if isinstance(value, float) and value == int(value) else str(value)
    return value


def conform(value: Any, schema: Any) -> Any:
    """Shape repair that needs no judgement: add missing fields as null, drop undeclared fields,
    convert unambiguous scalar spellings ("1,575.00" for a number field). Never invents a value."""
    if not isinstance(schema, dict):
        return value
    schema = branch_for(schema, value)
    if is_object(schema):
        if value is None:
            return None
        if not isinstance(value, dict):
            return value
        closed = schema.get("additionalProperties") is False
        out = {name: conform(value.get(name), sub) for name, sub in schema["properties"].items()}
        if not closed:
            out.update({k: v for k, v in value.items() if k not in out})
        return out
    if is_array(schema):
        if isinstance(value, list):
            items = schema.get("items", {})
            return [conform(v, items) for v in value]
        return value
    return _coerce_scalar(value, schema)


def empty_instance(schema: Any) -> Any:
    """What we return when nothing was extracted: top-level object with every field null."""
    return conform({}, schema) if is_object(schema) else None


# ---------------------------------------------------------------- validation

def find_errors(value: Any, schema: Any, path: Path = ()) -> list[tuple[Path, str]]:
    """Errors as (path, message), reported at the deepest node that is wrong (so one bad leaf does not
    condemn its whole parent object)."""
    if schema is True or schema == {} or not isinstance(schema, dict):
        return []
    br = _branches(schema)
    if br:
        if value is None and allows_null(schema):
            return []
        best: Optional[list] = None
        for b in br:
            if b == _NULL and value is not None:
                continue
            errs = find_errors(value, b, path)
            if not errs:
                best = []
                break
            if best is None or len(errs) < len(best):
                best = errs
        errs = [(path, "value is not allowed here")] if best is None else list(best)
        rest = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}
        if rest and not errs:
            errs = find_errors(value, rest, path)
        return errs
    if value is None:
        return [] if allows_null(schema) else [(path, "null is not allowed here")]
    if is_object(schema) and isinstance(value, dict):
        errs = []
        props = schema["properties"]
        for name in schema.get("required", []):
            if name not in value:
                errs.append((path + (name,), "required field is missing"))
        for name, v in value.items():
            if name in props:
                errs.extend(find_errors(v, props[name], path + (name,)))
            elif schema.get("additionalProperties") is False:
                errs.append((path + (name,), "field is not declared in the schema"))
        return errs
    if is_array(schema) and isinstance(value, list):
        errs = []
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errs.append((path, f"array has {len(value)} items, minItems is {schema['minItems']}"))
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errs.append((path, f"array has {len(value)} items, maxItems is {schema['maxItems']}"))
        items = schema.get("items", {})
        for i, v in enumerate(value):
            errs.extend(find_errors(v, items, path + (i,)))
        return errs
    try:
        return [(path, e.message) for e in Draft202012Validator(schema).iter_errors(value)][:1]
    except Exception as e:  # an unusable sub-schema is the user's schema problem, not the value's
        return [(path, f"could not be validated: {e}")]


def _get(value: Any, path: Path) -> Any:
    for p in path:
        value = value[p]
    return value


def null_out(value: Any, schema: Any, shadow: Any = None) -> tuple[Any, list[tuple[Path, str]]]:
    """Remove what still breaks the schema: bad object fields become null, bad array items are dropped,
    over-long arrays are cut. Returns (value, errors that could not be removed, e.g. minItems).

    ``shadow`` is an optional structure of the same shape (the caller's per-leaf annotations); every
    removal is mirrored on it in place so its paths stay aligned with the returned value.
    """
    value = copy.deepcopy(value)

    def sh(path: Path) -> Any:
        node = shadow
        try:
            for p in path:
                node = node[p]
        except (KeyError, IndexError, TypeError):
            return None
        return node

    for _ in range(12):
        errs = find_errors(value, schema)
        todo = [e for e in errs if "minItems" not in e[1]]
        if not todo:
            return value, errs
        drops: dict[Path, set[int]] = {}
        others = [e for e in todo if "maxItems" not in e[1] or not e[0]]
        for path, msg in (others or todo):  # drop bad items first, cut over-long arrays afterwards
            if not path:
                return empty_instance(schema), [(path, msg)]
            if "maxItems" in msg:
                cut = int(re.search(r"maxItems is (\d+)", msg).group(1))  # type: ignore[union-attr]
                try:
                    del _get(value, path)[cut:]
                except (KeyError, IndexError, TypeError):
                    continue  # an enclosing array was already cut in this round
                if isinstance(sh(path), list):
                    del sh(path)[cut:]
                continue
            try:
                parent, sparent = _get(value, path[:-1]), sh(path[:-1])
            except (KeyError, IndexError, TypeError):
                continue  # an ancestor was already removed in this round
            key = path[-1]
            if isinstance(parent, dict):
                if "not declared" in msg:
                    parent.pop(key, None)
                    if isinstance(sparent, dict):
                        sparent.pop(key, None)
                else:
                    parent[key] = None
                    if isinstance(sparent, dict):
                        sparent[key] = None
            elif isinstance(parent, list):
                drops.setdefault(path[:-1], set()).add(key)
        for ppath, idxs in sorted(drops.items(), key=lambda kv: -len(kv[0])):  # deepest first
            try:
                parent, sparent = _get(value, ppath), sh(ppath)
            except (KeyError, IndexError, TypeError):
                continue
            for i in sorted(idxs, reverse=True):
                if i < len(parent):
                    del parent[i]
                    if isinstance(sparent, list) and i < len(sparent):
                        del sparent[i]
    # last resort (deeply nested trouble): null the top-level field that still holds an error
    for path, msg in find_errors(value, schema):
        if path and "minItems" not in msg and isinstance(value, dict) and path[0] in value:
            value[path[0]] = None
            if isinstance(shadow, dict):
                shadow[path[0]] = None
    return value, find_errors(value, schema)


# ---------------------------------------------------------------- leaves and citation wrapping

def is_leaf_schema(schema: Any, value: Any = None) -> bool:
    s = branch_for(schema, value) if isinstance(schema, dict) else schema
    return not is_object(s) and not is_array(s)


def count_leaf_fields(schema: Any) -> int:
    """Number of declared leaf fields (array items counted once)."""
    if not isinstance(schema, dict):
        return 1
    br = _branches(schema)
    if br:
        live = [b for b in br if b != _NULL]
        return max((count_leaf_fields(b) for b in live), default=1)
    if is_object(schema):
        return sum(count_leaf_fields(s) for s in schema["properties"].values())
    if is_array(schema):
        return count_leaf_fields(schema.get("items", {}))
    return 1


def _is_wrapped(value: Any) -> bool:
    return isinstance(value, dict) and "v" in value and set(value) <= {"v", "r"}


def unwrap(value: Any, schema: Any, path: Path = (), sources: Optional[dict] = None) -> tuple[Any, dict[Path, list[str]]]:
    """Turn ``{"v": value, "r": [labels]}`` leaves back into plain values; collect labels per leaf path.

    Tolerant: a leaf the model did not wrap is kept as is with no sources.
    """
    sources = {} if sources is None else sources
    s0 = branch_for(schema, value) if isinstance(schema, dict) else schema
    if _is_wrapped(value) and not (is_object(s0) and "v" in s0["properties"]):
        inner = value["v"]
        s = branch_for(schema, inner) if isinstance(schema, dict) else schema
        if not isinstance(inner, (dict, list)) or is_leaf_schema(s, inner):
            r = value.get("r")
            labels = [str(x) for x in r] if isinstance(r, list) else [str(r)] if isinstance(r, str) else []
            if labels:
                sources[path] = labels
            return inner, sources
        value = inner
    s = branch_for(schema, value) if isinstance(schema, dict) else schema
    if isinstance(value, dict) and is_object(s):
        props = s["properties"]
        return {k: unwrap(v, props.get(k, {}), path + (k,), sources)[0] for k, v in value.items()}, sources
    if isinstance(value, list):
        items = s.get("items", {}) if isinstance(s, dict) else {}
        return [unwrap(v, items, path + (i,), sources)[0] for i, v in enumerate(value)], sources
    return value, sources


def iter_leaves(value: Any, schema: Any, path: Path = ()) -> Iterator[tuple[Path, Any, Any]]:
    """(path, value, leaf schema) for every leaf position of a conformed value."""
    s = branch_for(schema, value) if isinstance(schema, dict) else schema
    if isinstance(value, dict) and is_object(s):
        for k, v in value.items():
            yield from iter_leaves(v, s["properties"].get(k, {}), path + (k,))
    elif isinstance(value, list) and is_array(s):
        for i, v in enumerate(value):
            yield from iter_leaves(v, s.get("items", {}), path + (i,))
    else:
        yield path, value, s


def map_leaves(value: Any, schema: Any, fn: Any, path: Path = ()) -> Any:
    """Rebuild ``value`` with every leaf replaced by ``fn(path, leaf_value, leaf_schema)``."""
    s = branch_for(schema, value) if isinstance(schema, dict) else schema
    if isinstance(value, dict) and is_object(s):
        return {k: map_leaves(v, s["properties"].get(k, {}), fn, path + (k,)) for k, v in value.items()}
    if isinstance(value, list) and is_array(s):
        return [map_leaves(v, s.get("items", {}), fn, path + (i,)) for i, v in enumerate(value)]
    return fn(path, value, s)


def path_str(path: Path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "(root)"
