"""Contract tests driven by ../reference/openapi.json (Reducto's spec: 20 routes)."""

from __future__ import annotations

import pytest

from readanydoc.api import schemas

from .conftest import STEM, needs_spec, needs_spike, upload, wait_job

pytestmark = needs_spec
JSON_POST_ROUTES = ["/parse", "/parse_async", "/extract", "/extract_async", "/split", "/split_async", "/edit",
                    "/edit_async", "/classify", "/classify_async", "/pipeline", "/pipeline_async"]
JOB_SCHEMA = {"anyOf": [{"$ref": "#/components/schemas/AsyncJobResponse"},
                        {"$ref": "#/components/schemas/EnhancedAsyncJobResponse"}]}


def deref(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def request_schema_names(spec: dict, path: str) -> list[str]:
    schema = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
    options = schema.get("oneOf") or [schema]
    return [o["$ref"].rsplit("/", 1)[1] for o in options]


def response_schema(spec: dict, path: str, method: str = "post", code: str = "200") -> dict:
    schema = spec["paths"][path][method]["responses"][code]["content"]["application/json"]["schema"]
    if "oneOf" in schema:
        # /extract is "oneOf [V3ExtractResponse, AsyncExtractResponse]", but any sync response carries a
        # job_id and so also matches the async shape ({job_id}); Reducto's own responses fail a strict
        # oneOf here. Read it as anyOf.
        schema = {**{k: v for k, v in schema.items() if k != "oneOf"}, "anyOf": schema["oneOf"]}
    return schema


def example(spec: dict, schema: dict, full: bool, key: str = "") -> object:
    """A request value for a schema: required fields only, or every documented field."""
    schema = deref(spec, schema)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema.get("default", schema["enum"][0])
    for comb in ("anyOf", "oneOf"):
        if comb in schema:
            options = [o for o in schema[comb] if o.get("type") != "null"]
            return example(spec, options[0], full, key)
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        keys = props if full else schema.get("required", [])
        return {k: example(spec, props[k], full, k) for k in keys}
    if t == "array":
        return [example(spec, schema.get("items", {}), full, key)] if full else []
    if "default" in schema:
        return schema["default"]
    if t == "string":
        return "x"
    if t == "integer":
        return int(schema.get("minimum", 1))
    if t == "number":
        return float(schema.get("minimum", schema.get("exclusiveMinimum", 0) + 0.5))
    if t == "boolean":
        return True
    return {}


def body_for(spec: dict, name: str, full: bool, file_id: str, with_async: bool) -> dict:
    body = example(spec, {"$ref": f"#/components/schemas/{name}"}, full)
    assert isinstance(body, dict)
    for key in ("input", "document_url"):
        if key in body:
            body[key] = file_id
    if not with_async:
        body.pop("async", None)
    return body


# ---------------------------------------------------------------- routes
def test_spec_has_twenty_routes(spec):
    assert sum(len(m) for m in spec["paths"].values()) == 20


def test_every_route_exists(spec, app):
    ours = {(m.lower(), r.path.replace(":path", "")) for r in app.routes for m in getattr(r, "methods", [])}
    missing = [(m, p) for p, methods in spec["paths"].items() for m in methods if (m, p) not in ours]
    assert not missing, missing


# ---------------------------------------------------------------- request models
def test_request_models_cover_every_request_schema(spec):
    """Every schema reachable from a request body has a model in SCHEMA_MODELS."""
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                name = ref.rsplit("/", 1)[1]
                if name not in seen:
                    seen.add(name)
                    walk(spec["components"]["schemas"][name])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for methods in spec["paths"].values():
        for op in methods.values():
            walk(op.get("requestBody", {}).get("content", {}).get("application/json", {}))
    seen -= {"Body_upload_upload_post", "BoundingBox", "QueuePriority", "CitationParentBlockMode"}
    assert seen <= set(schemas.SCHEMA_MODELS), sorted(seen - set(schemas.SCHEMA_MODELS))


def _subset(spec_default, ours) -> bool:
    """Spec defaults for nested objects list only some keys; ours may add keys that are null/empty."""
    if isinstance(spec_default, dict) and isinstance(ours, dict):
        return all(k in ours and _subset(v, ours[k]) for k, v in spec_default.items()) and \
            all(ours[k] in (None, [], {}, "") for k in ours if k not in spec_default)
    return spec_default == ours


@pytest.mark.parametrize("name", sorted(schemas.SCHEMA_MODELS))
def test_request_model_matches_spec(spec, name):
    model = schemas.SCHEMA_MODELS[name]
    sch = spec["components"]["schemas"][name]
    fields = {(f.alias or n): f for n, f in model.model_fields.items()}
    assert set(fields) == set(sch["properties"]), set(fields) ^ set(sch["properties"])
    assert {a for a, f in fields.items() if f.is_required()} == set(sch.get("required", []))
    for prop, ps in sch["properties"].items():
        if "default" in ps:
            ours = fields[prop].get_default(call_default_factory=True)
            ours = ours.dump() if hasattr(ours, "dump") else ours
            assert _subset(ps["default"], ours), (prop, ps["default"], ours)


def test_unknown_and_legacy_fields_are_ignored():
    m = schemas.SyncParseConfig.model_validate({
        "document_url": "reducto://a.pdf", "options": {"ocr_mode": "standard"}, "advanced_options": {"x": 1},
        "experimental_options": {}, "priority": True, "no_such_field": 1, "settings": {"unknown": True}})
    assert m.input == "reducto://a.pdf"
    assert "options" not in m.dump() and "no_such_field" not in m.dump()


# ---------------------------------------------------------------- documented bodies in, schema-valid bodies out
@needs_spike
@pytest.mark.parametrize("path", JSON_POST_ROUTES)
@pytest.mark.parametrize("full", [False, True], ids=["required-only", "every-field"])
def test_documented_bodies_are_accepted(spec, client, fake_tasks, validate, bench_pdf, path, full):
    file_id = upload(client, bench_pdf)
    for name in request_schema_names(spec, path):
        is_async_schema = name.startswith(("Async", "V3Async", "config__v3__Async"))
        body = body_for(spec, name, full, file_id, with_async=is_async_schema or path.endswith("_async"))
        if full and "split_description" in body:
            assert body["split_description"], "generator should produce one category"
        r = client.post(path, json=body)
        if path.startswith("/pipeline"):
            assert r.status_code == 501, r.text
            assert r.json()["error"]["code"] == 501 and "deprecated" in r.json()["detail"]
            continue
        assert r.status_code == 200, (name, r.text[:500])
        validate(r.json(), response_schema(spec, path))
        if set(r.json()) == {"job_id"}:
            job = wait_job(client, r.json()["job_id"])
            assert job["status"] == "Completed", job
            validate(job, JOB_SCHEMA)


@needs_spike
@pytest.mark.parametrize("field,values", [
    ("chunk_mode", ["variable", "section", "page", "disabled", "block", "page_sections"]),
    ("table_output_format", ["html", "json", "md", "jsonbbox", "dynamic", "csv"]),
    ("model", ["r-1", "r-1-mini", "legacy", None]),
])
def test_parse_enum_values_are_accepted(client, validate, field, values):
    for v in values:
        body = {"input": STEM}
        if field == "chunk_mode":
            body["retrieval"] = {"chunking": {"chunk_mode": v}}
        elif field == "table_output_format":
            body["formatting"] = {"table_output_format": v}
        else:
            body["settings"] = {"model": v}
        r = client.post("/parse", json=body)
        assert r.status_code == 200, (v, r.text[:300])
        validate(r.json(), "ParseResponse")
    if field == "model":
        mini = client.post("/parse", json={"input": STEM, "settings": {"model": "r-1-mini"}}).json()
        assert mini["usage"]["usage_breakdown"]["parse_model"] == "R-1 Mini"


def test_validation_error_shape(client, validate):
    for path, body in [("/parse", {}), ("/parse", {"input": STEM, "settings": {"model": "r-9"}}),
                       ("/split", {"input": "x"}), ("/edit", {"document_url": "x"}),
                       ("/parse", {"input": STEM, "retrieval": {"filter_blocks": ["Nope"]}})]:
        r = client.post(path, json=body)
        assert r.status_code == 422, (path, body, r.text)
        validate(r.json(), "HTTPValidationError")
        assert r.json()["detail"]


# ---------------------------------------------------------------- the non-JSON routes
def test_upload_delete_version_webhook_jobs_shapes(spec, client, validate, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4\n%%EOF\n")
    r = client.post("/upload", files={"file": ("a.pdf", f.read_bytes(), "application/pdf")})
    validate(r.json(), response_schema(spec, "/upload"))
    file_id = r.json()["file_id"]
    assert file_id.startswith("reducto://") and file_id.endswith(".pdf")
    d = client.delete(f"/upload/{file_id}")
    assert d.status_code == 200, d.text
    validate(d.json(), response_schema(spec, "/upload/{file_id}", "delete"))
    assert client.delete(f"/upload/{file_id}").status_code == 404

    p = client.post("/upload", params={"extension": "pdf"})
    validate(p.json(), response_schema(spec, "/upload"))
    assert p.json()["presigned_url"] and p.json()["file_id"].endswith(".pdf")

    v = client.get("/version")
    assert v.status_code == 200 and isinstance(v.json(), str)
    validate(v.json(), response_schema(spec, "/version", "get"))
    w = client.post("/configure_webhook")
    assert w.status_code == 200
    validate(w.json(), response_schema(spec, "/configure_webhook"))
    j = client.get("/jobs")
    validate(j.json(), response_schema(spec, "/jobs", "get"))


def test_error_shape_for_unknown_route_and_job(client):
    for r in (client.get("/nope"), client.get("/job/does-not-exist"), client.post("/cancel/does-not-exist"),
              client.delete("/job/does-not-exist")):
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == 404 and body["error"]["message"] == body["detail"]
        assert {"message", "type", "code"} <= set(body["error"])
