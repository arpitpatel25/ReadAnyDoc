"""Systems a test run can compare. Each uses your own key from the environment and returns the same result shape:

    {"data": {...}, "citations": {"a.b[0].c": [{"content", "bbox": {"page", "left", "top", "width", "height"}}]},
     "cost_usd": float | None, "cost_note": str, "seconds": float, "error": str | None}

- ``readanydoc`` (two stages, the default), ``readanydoc-model-only`` and ``readanydoc-one-stage``: this library,
  in-process. Needs ``ANTHROPIC_API_KEY`` (or the key of the model you choose with ``model=``).
- ``plain:<model>``: the file and the schema sent to one model in one call, as a developer would do without any
  pipeline (default ``claude-haiku-5-5`` at high effort). No enforcement, no citations. Needs ``ANTHROPIC_API_KEY``.
- ``reducto``: Reducto's Extract API with citations on. Needs ``REDUCTO_API_KEY``. Its cost is estimated from its
  list price ($0.02 per page); your contract may differ.

Add another with ``register(name, factory)``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

REDUCTO_URL = os.environ.get("REDUCTO_BASE_URL", "https://platform.reducto.ai")
REDUCTO_PRICE_PER_PAGE = 0.02
_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def pages_of(path: str) -> int:
    if path.lower().endswith(".pdf"):
        try:
            import pypdfium2 as pdfium
            return len(pdfium.PdfDocument(path))
        except Exception:  # noqa: BLE001 - a count only
            return 1
    return 1


class System:
    name = "system"
    key_env: Optional[str] = None
    # rough cost per page, only to estimate a run before it starts (measured on the records in docs/research)
    est_per_page = 0.005

    def ready(self) -> Optional[str]:
        """None when the system can run, else why not."""
        if self.key_env and not os.environ.get(self.key_env):
            return f"{self.key_env} is not set"
        return None

    def run(self, case: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class ReadAnyDoc(System):
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, mode: str = "engine", parse: bool = True, model: Optional[str] = None):
        self.mode, self.parse, self.model = mode, parse, model
        self.name = "readanydoc" + ("" if mode == "engine" and parse else "-model-only" if parse else "-one-stage")
        self.est_per_page = 0.005 if parse and mode == "engine" else 0.003

    def run(self, case: dict) -> dict:
        import readanydoc
        t = time.time()
        kw: dict = {"citations": True, "mode": self.mode, "parse": self.parse}
        if self.model:
            kw["model"] = kw["extract_model"] = self.model
        try:
            r = readanydoc.extract(case["file"], case["schema"], instructions=case.get("instructions"), **kw)
        except Exception as e:  # noqa: BLE001 - recorded as a failed document
            return {"data": None, "citations": {}, "cost_usd": None, "seconds": round(time.time() - t, 1),
                    "error": f"{type(e).__name__}: {e}"[:500]}
        data = r.data.model_dump() if hasattr(r.data, "model_dump") else r.data
        cites = {k: [_box(c.model_dump()) for c in v] for k, v in (r.citations or {}).items()}
        return {"data": data, "citations": cites, "cost_usd": r.usage.cost_usd, "cost_note": "measured",
                "seconds": round(time.time() - t, 1), "error": None, "warnings": r.warnings[:20]}


class PlainModel(System):
    key_env = "ANTHROPIC_API_KEY"
    est_per_page = 0.002

    def __init__(self, model: str = "claude-haiku-5-5", effort: str = "high"):
        self.model, self.effort = model, effort
        self.name = f"plain:{model}"

    def run(self, case: dict) -> dict:
        from .. import model_limits as ML
        t = time.time()
        path = Path(case["file"])
        data = path.read_bytes()
        if path.suffix.lower() == ".pdf":
            doc = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                  "data": base64.standard_b64encode(data).decode()}}
        elif path.suffix.lower() in _IMAGE:
            doc = {"type": "image", "source": {"type": "base64", "media_type": _IMAGE[path.suffix.lower()],
                                               "data": base64.standard_b64encode(data).decode()}}
        else:
            return {"data": None, "citations": {}, "cost_usd": None, "seconds": 0.0,
                    "error": f"plain model: {path.suffix} files are not sent (PDF and images only)"}
        limits, _ = ML.lookup(self.model)
        text = ("Extract the data from this document into JSON matching this JSON Schema:\n" +
                json.dumps(case["schema"]) + "\n\nReturn only the JSON object.")
        body: dict = {"model": self.model, "max_tokens": int((limits or {}).get("max_output") or 32000),
                      "messages": [{"role": "user", "content": [doc, {"type": "text", "text": text}]}]}
        if case.get("instructions"):
            body["system"] = case["instructions"]
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        out, usage, err = "", {}, None
        try:
            with httpx.Client(timeout=1800) as c, c.stream(
                    "POST", "https://api.anthropic.com/v1/messages", json={**body, "stream": True},
                    headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"}) as r:
                if r.status_code != 200:
                    err = f"HTTP {r.status_code}: {r.read()[:300]!r}"
                else:
                    for line in r.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        e = json.loads(line[6:])
                        if e["type"] == "message_start":
                            usage.update(e["message"].get("usage", {}))
                        elif e["type"] == "content_block_delta" and e["delta"].get("type") == "text_delta":
                            out += e["delta"]["text"]
                        elif e["type"] == "message_delta":
                            usage.update(e.get("usage", {}))
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:500]
        parsed = None
        if not err:
            m = re.search(r"\{.*\}", out, re.S)
            try:
                parsed = json.loads(m.group(0)) if m else None
            except ValueError as e:
                err = f"the reply is not valid JSON: {e}"[:300]
            if parsed is None and not err:
                err = "no JSON in the reply"
        return {"data": parsed, "citations": {}, "cost_usd": ML.cost(limits, usage) if usage else None,
                "cost_note": "measured", "seconds": round(time.time() - t, 1), "error": err}


class Reducto(System):
    name = "reducto"
    key_env = "REDUCTO_API_KEY"
    est_per_page = REDUCTO_PRICE_PER_PAGE

    def run(self, case: dict) -> dict:
        t = time.time()
        hd = {"Authorization": "Bearer " + os.environ["REDUCTO_API_KEY"]}
        body: dict = {"instructions": {"schema": case["schema"]}, "settings": {"citations": {"enabled": True}}}
        if case.get("instructions"):
            body["instructions"]["system_prompt"] = case["instructions"]
        try:
            with httpx.Client(timeout=1800) as c:
                with open(case["file"], "rb") as fh:
                    up = c.post(REDUCTO_URL + "/upload", headers=hd, files={"file": (Path(case["file"]).name, fh)})
                up.raise_for_status()
                r = c.post(REDUCTO_URL + "/extract", headers=hd, json={"input": up.json()["file_id"], **body})
                d = r.json()
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {str(d)[:300]}")
                if isinstance(d.get("result"), dict) and d["result"].get("type") == "url":
                    d["result"] = c.get(d["result"]["url"]).json()
        except Exception as e:  # noqa: BLE001
            return {"data": None, "citations": {}, "cost_usd": None, "seconds": round(time.time() - t, 1),
                    "error": f"{type(e).__name__}: {e}"[:500]}
        res = d.get("result")
        res = res[0] if isinstance(res, list) and len(res) == 1 else res
        cites: dict = {}
        data = _unwrap(res, "", cites)
        pages = (d.get("usage") or {}).get("num_pages") or pages_of(case["file"])
        return {"data": data, "citations": cites, "cost_usd": round(pages * REDUCTO_PRICE_PER_PAGE, 4),
                "cost_note": "list price", "seconds": round(time.time() - t, 1), "error": None}


def _box(c: dict) -> dict:
    b = c.get("bbox") or {}
    return {"content": c.get("content"), "confidence": c.get("confidence"),
            "granular": c.get("granular_confidence"), "score": c.get("score"), "parent_type": (c.get("parentBlock") or {}).get("type"),
            "bbox": {k: b.get(k) for k in ("page", "left", "top", "width", "height")} if b else None}


def _unwrap(node: Any, path: str, cites: dict) -> Any:
    """Reducto's {"value", "citations"} leaves back to plain values; citations collected by path."""
    if isinstance(node, dict) and "value" in node and "citations" in node and set(node) <= {"value", "citations"}:
        if node["citations"]:
            cites[path] = [_box(c) for c in node["citations"]]
        return _unwrap(node["value"], path, cites)
    if isinstance(node, dict):
        return {k: _unwrap(v, f"{path}.{k}" if path else k, cites) for k, v in node.items()}
    if isinstance(node, list):
        return [_unwrap(v, f"{path}[{i}]", cites) for i, v in enumerate(node)]
    return node


_REGISTRY: dict[str, Callable[[str], System]] = {}


def register(name: str, factory: Callable[[str], System]) -> None:
    """``factory(arg)`` builds the system for ``name`` or ``name:arg``."""
    _REGISTRY[name] = factory


register("readanydoc", lambda a: ReadAnyDoc("engine", True, a or None))
register("readanydoc-model-only", lambda a: ReadAnyDoc("model-only", True, a or None))
register("readanydoc-one-stage", lambda a: ReadAnyDoc("engine", False, a or None))
register("plain", lambda a: PlainModel(*(a.split("#", 1) if "#" in a else (a or "claude-haiku-5-5", "high"))))
register("reducto", lambda a: Reducto())

NAMES = ["readanydoc", "readanydoc-one-stage", "readanydoc-model-only", "plain:<model>", "reducto"]


def make(spec: str) -> System:
    name, _, arg = spec.partition(":")
    if name not in _REGISTRY:
        raise KeyError(f"unknown system {spec!r}; known: {', '.join(NAMES)}")
    return _REGISTRY[name](arg)
