"""readanydoc.parse / readanydoc.extract and model choice (readanydoc.modelspec). No model is called."""

from __future__ import annotations

from typing import Optional

import pydantic
import pytest

import readanydoc
from readanydoc import library
from readanydoc.modelspec import ModelSpecError, make_spec, split_options
from readanydoc.readers import make_reader
from readanydoc.tasks.llm import FakeLLM, get_llm

from .conftest import PAGES, needs_spike

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def test_model_names_become_reader_specs():
    assert make_spec("claude-haiku-5-5", "high") == "anthropic_api:claude-haiku-5-5#high"
    assert make_spec("gpt-5", "low") == "openai:gpt-5#low"
    assert make_spec("openai_compatible:glm@http://h:8000/v1") == "openai_compatible:glm@http://h:8000/v1"
    assert make_spec("cached:haiku", "high") == "cached:haiku"  # no effort for adapters that have none
    assert make_spec("claude-haiku-5-5", "high", {"effort": "low"}) == "anthropic_api:claude-haiku-5-5#low"
    with pytest.raises(ModelSpecError):
        make_spec("some-model")  # provider unknown: must be written as provider:model


def test_model_options_are_sent_but_reserved_fields_are_refused():
    spec = make_spec("claude-haiku-5-5", "high", {"max_tokens": 20000, "inference_geo": "us"})
    assert split_options(spec)[1] == {"inference_geo": "us", "max_tokens": 20000}
    body = make_reader(spec).build_request(PNG, "read", 100)
    assert body["inference_geo"] == "us" and body["output_config"] == {"effort": "high"}
    for field in ("stream", "tools", "messages"):
        with pytest.raises(ModelSpecError):
            make_spec("claude-haiku-5-5", None, {field: True})


def test_task_models_take_the_same_specs():
    llm = get_llm(make_spec("claude-sonnet-5-5", "medium", {"output_config": {"task_budget": 5000}}))
    assert llm.build_request("s", "u", None, None, 100)["output_config"] == {"effort": "medium", "task_budget": 5000}
    body = get_llm("openai:gpt-5#low").build_request("s", "u", None, None, 100)
    assert body["reasoning_effort"] == "low" and body["max_completion_tokens"] == 100


def test_extract_results_split_values_and_citations():
    cite = {"content": "2,000,000", "bbox": {"left": 0.8, "top": 0.4, "width": 0.05, "height": 0.01, "page": 1},
            "confidence": "high"}
    raw = {"total": {"value": 2000000, "citations": [cite]},
           "lines": [{"name": {"value": "a", "citations": []}}], "note": {"value": None, "citations": []}}
    data, cites = library._unwrap(raw, True)
    assert data == {"total": 2000000, "lines": [{"name": "a"}], "note": None}
    assert list(cites) == ["total"] and cites["total"][0].bbox.page == 1


def test_unknown_mode_is_an_error():
    with pytest.raises(ValueError):
        library._run_pipeline("x.pdf", mode="fast", model="cached:haiku", effort=None, model_options=None,
                              handwriting_model=None, handwriting_effort=None, handwriting_options=None, pages=None,
                              citations=False, options=None)


@needs_spike
def test_parse_and_extract_with_saved_model_outputs(monkeypatch):
    from readanydoc import paths
    pdf = paths.bench_pdf(PAGES["multi_column"])
    if pdf is None:
        pytest.skip("page not available")
    result = readanydoc.parse(str(pdf), mode="model-only", model="cached:haiku")
    assert result.blocks and result.pages == 1 and result.markdown

    class Page(pydantic.BaseModel):
        title: Optional[str] = None

    monkeypatch.setattr("readanydoc.tasks.llm.get_llm", lambda spec: FakeLLM(['{"title": "X"}']))
    out = readanydoc.extract(str(pdf), schema=Page, mode="model-only", model="cached:haiku", citations=False)
    assert isinstance(out.data, Page)
