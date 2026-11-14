import json

import jsonschema
import pytest

from readanydoc import paths
from readanydoc.model import Document
from readanydoc.serialize import to_parse_response

from .conftest import needs_spike
from .test_model import build_doc

SPEC = paths.reference_dir() / "openapi.json"
GOLDEN = paths.spike_dir() / "reducto_out" / "r1" / "part02.parse.json"
needs_spec = pytest.mark.skipif(not SPEC.exists(), reason="reference/openapi.json not available")


def validator() -> jsonschema.Draft202012Validator:
    spec = json.loads(SPEC.read_text())
    schema = {"$ref": "#/components/schemas/ParseResponse", "components": spec["components"]}
    return jsonschema.Draft202012Validator(schema)


def key_shape(obj, path=""):
    """Set of key paths; list items are merged under '[]'. Values under 'extra' are free-form."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            out.add(p)
            if k != "extra":
                out |= key_shape(v, p)
    elif isinstance(obj, list):
        for v in obj:
            out |= key_shape(v, path + "[]")
    return out


@needs_spec
def test_handmade_document_validates():
    doc = build_doc()
    assert to_parse_response(doc)["document_properties"] is None  # only on request, like Reducto
    doc.meta["parse_options"] = {"extract_document_properties": True}
    resp = to_parse_response(doc)
    validator().validate(resp)
    assert resp["result"]["chunks"][0]["blocks"][1]["bbox"]["original_page"] == 3
    assert resp["document_properties"]["title"] == "Report"


@needs_spec
def test_empty_document_validates():
    validator().validate(to_parse_response(Document()))


@needs_spec
@needs_spike
def test_pipeline_output_validates_against_openapi(haiku_docs):
    v = validator()
    for doc in haiku_docs.values():
        resp = to_parse_response(doc)
        v.validate(resp)
        blocks = resp["result"]["chunks"][0]["blocks"]
        assert blocks and all(0 <= b["bbox"]["left"] <= 1 and b["bbox"]["page"] == 1 for b in blocks)


@needs_spec
@needs_spike
def test_real_reducto_response_validates_too():
    """Sanity check of the test itself: the golden file must pass the same validator."""
    validator().validate(json.loads(GOLDEN.read_text()))


@needs_spike
def test_same_key_structure_as_real_reducto(haiku_docs):
    golden = key_shape(json.loads(GOLDEN.read_text()))
    for doc in haiku_docs.values():
        ours = key_shape(to_parse_response(doc))
        assert ours == golden, (sorted(ours - golden), sorted(golden - ours))


@needs_spike
def test_serializer_reads_only_the_document(haiku_docs):
    doc = next(iter(haiku_docs.values()))
    clone = Document.from_json(doc.to_json())
    assert to_parse_response(clone) == to_parse_response(doc)
