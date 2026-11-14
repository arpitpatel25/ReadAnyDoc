"""Extract, Split, Classify and Edit on top of a parsed Document (never on raw model text).

CONTRACT WITH THE API LAYER
---------------------------
Each task module exposes one entry point::

    readanydoc.tasks.extract.run(document, config, *, llm, options=None) -> dict
    readanydoc.tasks.split.run(document, config, *, llm, options=None) -> dict
    readanydoc.tasks.classify.run(document, config, *, llm, options=None) -> dict
    readanydoc.tasks.edit.run(document, config, *, llm, source_path, output_path=None, options=None) -> dict

- ``document``: an already parsed ``readanydoc.model.Document``. Extract/Split/Classify also accept a
  list of Documents (multi-document input); pages are then numbered continuously. Edit works on the
  source file, so ``document`` may be ``None`` there.
- ``config``: the Reducto request body as a plain dict, minus ``input`` / ``parsing``
  (Edit: minus ``document_url``). Unknown keys are ignored. Missing keys take Reducto's defaults.
- ``llm``: a ``readanydoc.tasks.llm.LLM`` (``complete(system, user, *, images=None, json_schema=None,
  max_tokens=...) -> LLMResult``). Build one with ``readanydoc.tasks.llm.get_llm("anthropic_api:claude-haiku-5-5")``,
  ``"anthropic_api:haiku"`` or pass a ``FakeLLM`` in tests.
- ``options``: optional dict of internal knobs (segment size, retry limits); never needed by a client.
- Return value: the Reducto response body for that endpoint WITHOUT ``job_id`` and ``duration``
  (the API layer adds those):

    extract  -> {"response_type": "v3_extract", "result": [obj] | obj-with-citations, "usage": {num_pages,
                 num_fields (leaf values returned), credits, extract_mode: "extract" | "super_agent"},
                 "studio_link": None, "confidence", "confidence_reason"}
    split    -> {"response_type": "split", "result": {"section_mapping": None, "splits": [...]},
                 "usage": {num_pages, credits}}; sections with no pages are left out. With
                 ``settings.deep_split`` the result is Reducto's ``DeepSplitResult``: {"splits": [{name,
                 pages: [{page_number, evidence, confidence}], partitions: [{name, pages: [...]}] | None}]}
    classify -> {"response_type": "classify", "result": {"category"}, "response_confidence": {...},
                 "usage": {num_pages, num_categories, credits}, ["extra_metadata": {"grouping"}]}
    edit     -> {"response_type": "edit", "document_url": <local output file path>, "form_schema": [...] | None,
                 "usage": {num_pages, credits, model_usage?}}; each form_schema entry carries the ``value``
                 written and ``filled``. A flat or scanned PDF without a ``form_schema`` has its fields
                 detected on the page images first (``tasks/detect.py``).

  Edit returns a local file path in ``document_url``; the API layer turns it into a download URL.
- Errors: every user-facing failure is a ``TaskError`` with ``status_code`` (400 invalid schema / page
  range / request, 501 not implemented, 502 model failure) and ``detail``; ``to_response()`` gives the
  FastAPI-style body ``{"detail": ...}``.

The shapes above were checked against Reducto's real responses on 2026-11-12 (``e2e/cases_extract.jsonl``,
``cases_split.jsonl``, ``cases_classify.jsonl``, ``cases_edit.jsonl``; ``e2e/compare_tasks.py``).
The task model is set by ``READANYDOC_TASK_LLM`` (``anthropic_api:haiku`` runs Haiku 5.5 at high effort).
"""

from .common import TaskError
from .llm import LLM, FakeLLM, LLMError, LLMResult, get_llm

__all__ = ["TaskError", "LLM", "FakeLLM", "LLMError", "LLMResult", "get_llm"]
