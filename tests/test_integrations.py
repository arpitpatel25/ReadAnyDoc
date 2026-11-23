"""Framework adapters: chunking by page, block and document, metadata, no model calls."""
from pathlib import Path
from unittest.mock import Mock, call

import pytest

import readanydoc
from readanydoc.integrations import chunks
from readanydoc.library import Block, BoundingBox, ParseResult, Usage


def fake_parse(path, **kw):
    box = lambda p, t: BoundingBox(left=0.1, top=t, width=0.5, height=0.05, page=p)
    blocks = [Block(type="Title", content="# Report", bbox=box(1, 0.1), confidence="high"),
              Block(type="Text", content="Revenue grew.", bbox=box(1, 0.3), confidence="high"),
              Block(type="Table", content="<table><tr><td>1</td></tr></table>", bbox=box(2, 0.2), confidence="low")]
    return ParseResult(blocks=blocks, pages=2, usage=Usage(calls=0, input_tokens=0, output_tokens=0, seconds=0),
                       warnings=["page 2: check"])


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setattr(readanydoc, "parse", fake_parse)


def test_split_by_page_block_and_document():
    pages = list(chunks("r.pdf"))
    assert [m["page"] for _, m in pages] == [1, 2] and pages[0][0] == "# Report\n\nRevenue grew."
    assert pages[0][1]["warnings"] == ["page 2: check"] and "warnings" not in pages[1][1]
    blocks = list(chunks("r.pdf", "block"))
    assert len(blocks) == 3 and blocks[2][1]["block_type"] == "Table" and blocks[2][1]["confidence"] == "low"
    (doc, meta), = chunks("r.pdf", "document")
    assert meta["pages"] == 2 and "Revenue grew." in doc
    with pytest.raises(ValueError):
        list(chunks("r.pdf", "line"))


def test_langchain_loader():
    pytest.importorskip("langchain_core")
    from readanydoc.integrations.langchain import ReadAnyDocLoader
    docs = ReadAnyDocLoader("r.pdf").load()
    assert len(docs) == 2 and docs[1].metadata == {"source": "r.pdf", "page": 2}


def test_llamaindex_reader():
    pytest.importorskip("llama_index.core")
    from readanydoc.integrations.llamaindex import ReadAnyDocReader
    docs = ReadAnyDocReader(split="block").load_data("r.pdf", extra_info={"team": "finance"})
    assert len(docs) == 3 and docs[0].metadata["team"] == "finance" and docs[0].text == "# Report"


@pytest.fixture
def haystack(monkeypatch):
    monkeypatch.setenv("HAYSTACK_TELEMETRY_ENABLED", "false")
    return pytest.importorskip("haystack")


def test_haystack_converter_pipeline(haystack, monkeypatch):
    from readanydoc.integrations.haystack import ReadAnyDocConverter

    parse = Mock(side_effect=fake_parse)
    monkeypatch.setattr(readanydoc, "parse", parse)
    sources = ["r.pdf", Path("s.pdf")]
    pipeline = haystack.Pipeline()
    pipeline.add_component("converter", ReadAnyDocConverter(mode="model-only"))
    assert pipeline.to_dict()["components"]["converter"]["init_parameters"] == {
        "split": "page", "mode": "model-only",
    }
    results = pipeline.run({"converter": {"sources": sources}})
    docs = results["converter"]["documents"]

    assert parse.call_args_list == [call(source, mode="model-only") for source in sources]
    assert all(isinstance(doc, haystack.Document) for doc in docs)
    assert [doc.content for doc in docs] == ["# Report\n\nRevenue grew.", "<table><tr><td>1</td></tr></table>"] * 2
    assert [doc.meta for doc in docs] == [
        {"source": "r.pdf", "page": 1, "warnings": ["page 2: check"]},
        {"source": "r.pdf", "page": 2},
        {"source": "s.pdf", "page": 1, "warnings": ["page 2: check"]},
        {"source": "s.pdf", "page": 2},
    ]


@pytest.mark.parametrize("split", ["document", "block"])
def test_haystack_converter_split(haystack, split):
    from readanydoc.integrations.haystack import ReadAnyDocConverter

    docs = ReadAnyDocConverter(split=split).run(sources=["r.pdf"])["documents"]
    assert [(doc.content, doc.meta) for doc in docs] == list(chunks("r.pdf", split))


def test_haystack_converter_surfaces_parse_errors(haystack, monkeypatch):
    from readanydoc.integrations.haystack import ReadAnyDocConverter

    monkeypatch.setattr(readanydoc, "parse", Mock(side_effect=FileNotFoundError("missing.pdf")))
    with pytest.raises(FileNotFoundError, match="missing.pdf"):
        ReadAnyDocConverter().run(sources=["missing.pdf"])
