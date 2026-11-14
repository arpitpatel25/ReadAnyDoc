"""The call contract between the API layer and ``readanydoc.tasks``.

The API resolves inputs, runs the parse pipeline and hands each task a ``Document``; the task returns
a plain dict that the API wraps into Reducto's response (job id, duration, usage defaults, URL result).

Minimal form (what a replacement task or a test double needs):

    readanydoc.tasks.extract.run(document, config)  -> dict
    readanydoc.tasks.split.run(document, config)    -> dict
    readanydoc.tasks.classify.run(document, config) -> dict
    readanydoc.tasks.edit.run(input_path, config)   -> dict

The tasks' published form adds keyword-only arguments; the adapter here inspects the signature
and supplies whichever of them ``run`` declares:

    run(document, config, *, llm, options=None)                              extract / split / classify
    run(document, config, *, llm, source_path, output_path=None, options=None)   edit (document is None)

``llm`` is ``readanydoc.tasks.llm.get_llm(os.environ.get("READANYDOC_TASK_LLM") or DEFAULT_LLM)``;
``source_path`` is the local file to edit; ``output_path`` a path inside the job's output directory.

``document``
    ``readanydoc.model.Document`` (already parsed with the request's ``parsing`` options), or a
    ``list[Document]`` when the request's ``input`` is a list (multi-document).
``input_path`` (edit, minimal form only)
    ``str`` path of the local file to edit (PDF or DOCX), already downloaded.
``config``
    The validated request body as a dict with every documented default filled in, keys exactly as in
    Reducto's API (``instructions.schema``, ``settings.citations.enabled``, ``split_description``,
    ``classification_schema``, ``edit_instructions``, ``form_schema`` ...). It still contains ``input``
    / ``parsing`` / ``async`` (ignore them) and one extra key, ``"_readanydoc"``:
    ``{"job_id": str, "profile": str, "output_dir": str}``; ``output_dir`` is a directory the task may
    write files into.

Return value: a dict (or a pydantic model, which is dumped). Keys the API reads:

    extract   {"result": <object | list>,                      # required; with citations the task puts
               "usage": {"num_fields": int, ...}?,             #   {"value", "citations"} leaves in result
               "confidence": "high"|"low"?, "confidence_reason": str?}
    split     {"result": {"splits": [{"name", "pages", "conf", "partitions"?}], "section_mapping": {...}|None},
               "usage": {...}?}
    classify  {"result": {"category": str}, "response_confidence": {"categories": [...]}?,
               "extra_metadata": {str: str}?, "usage": {...}?}
    edit      {"document_url": <local output file path, or a ready http(s) URL>
               (or "document_path": str, or "document_bytes": bytes), "filename": str?,
               "form_schema": [EditWidget...]?, "usage": {...}?}

``job_id``, ``duration`` and ``response_type`` are set by the API; missing ``usage`` fields get
defaults (``num_pages`` from the Document, ``num_fields`` by counting result leaves, ``credits`` null).
Anything else in the dict is passed through into the response untouched.

Errors: ``readanydoc.tasks.TaskError(status_code, detail)`` -> that status; ``NotImplementedError`` -> 501;
``jsonschema`` SchemaError -> 422 INVALID_SCHEMA; ``ValueError`` -> 400 INVALID_CONFIG;
``readanydoc.api.errors.ApiError`` -> as given; anything else -> 500. All in Reducto's error shape.
If ``readanydoc.tasks.<name>`` cannot be imported, or has no ``run``, the route answers 501.
"""

from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Any, Callable

from . import errors

TASK_LLM_ENV = "READANYDOC_TASK_LLM"
TASKS = ("extract", "split", "classify", "edit")
TaskFn = Callable[[Any, dict[str, Any]], Any]

# tests (and embedders) can register a replacement here: OVERRIDES["extract"] = fn
OVERRIDES: dict[str, TaskFn] = {}


def load_task(name: str) -> TaskFn:
    if name not in TASKS:
        raise errors.not_implemented(f"unknown task {name!r}")
    if name in OVERRIDES:
        return OVERRIDES[name]
    try:
        mod = importlib.import_module(f"readanydoc.tasks.{name}")
    except Exception as e:  # ImportError, or the module failing at import while it is being written
        raise errors.not_implemented(
            f"/{name} is not available in this build: readanydoc.tasks.{name} could not be imported "
            f"({e.__class__.__name__}: {e})") from e
    fn = getattr(mod, "run", None)
    if not callable(fn):
        raise errors.not_implemented(f"/{name} is not available in this build: readanydoc.tasks.{name}.run is missing")
    return fn


_STATUS_NAMES = {400: "INVALID_CONFIG", 401: "AUTH_ERROR", 404: "NOT_FOUND", 413: "DOCUMENT_TOO_LARGE",
                 415: "DOCUMENT_UNSUPPORTED", 422: "INVALID_SCHEMA", 429: "RATE_LIMIT",
                 442: "DOCUMENT_PASSWORD_PROTECTED", 501: "NOT_APPLICABLE", 502: "LLM_PROVIDER_ERROR",
                 503: "SERVICE_UNAVAILABLE", 504: "TIMEOUT"}


def _keyword_args(fn: TaskFn, name: str, target: Any, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Supply the keyword-only arguments of the task signature, if ``fn`` declares them."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return target, {}
    kwargs: dict[str, Any] = {}
    if "llm" in params:
        from ..tasks.llm import DEFAULT_LLM, get_llm  # only reached when readanydoc.tasks exists
        kwargs["llm"] = get_llm(os.environ.get(TASK_LLM_ENV) or DEFAULT_LLM)
    if name == "edit" and "source_path" in params:
        kwargs["source_path"] = target
        if "output_path" in params:
            out_dir = Path((config.get("_readanydoc") or {}).get("output_dir") or ".")
            kwargs["output_path"] = str(out_dir / f"edited{Path(str(target)).suffix or '.pdf'}")
        target = None
    return target, kwargs


def call_task(name: str, target: Any, config: dict[str, Any]) -> dict[str, Any]:
    fn = load_task(name)
    try:
        target, kwargs = _keyword_args(fn, name, target, config)
        out = fn(target, config, **kwargs)
    except errors.ApiError:
        raise
    except NotImplementedError as e:
        raise errors.not_implemented(str(e) or f"/{name} is not implemented yet") from e
    except Exception as e:
        status = getattr(e, "status_code", None)
        if isinstance(status, int) and hasattr(e, "detail"):  # readanydoc.tasks.TaskError
            raise errors.ApiError(status, _STATUS_NAMES.get(status, "PROCESSING_FAILED" if status < 500
                                                            else "INTERNAL_ERROR"), str(e.detail)) from e
        if isinstance(e, ValueError) and not e.__class__.__module__.startswith("jsonschema"):
            raise errors.bad_request(str(e)) from e
        raise errors.from_exception(e) from e
    if hasattr(out, "model_dump"):
        out = out.model_dump(mode="json", by_alias=True)
    if not isinstance(out, dict):
        raise errors.internal(f"readanydoc.tasks.{name}.run returned {type(out).__name__}, expected a dict")
    return out
