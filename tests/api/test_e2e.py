"""End to end through the HTTP layer: upload -> parse, parse_async -> job, inputs, URL results, errors."""

from __future__ import annotations

import time

import pytest

from readanydoc.api import contracts

from .conftest import STEM, needs_spec, needs_spike, upload, wait_job

pytestmark = [needs_spike, needs_spec]


def text_of(parse_response: dict) -> str:
    return "\n".join(c["content"] for c in parse_response["result"]["chunks"])


def test_parse_cached_stem(client, validate):
    r = client.post("/parse", json={"input": STEM})
    assert r.status_code == 200, r.text
    body = r.json()
    validate(body, "ParseResponse")
    assert body["result"]["type"] == "full" and body["usage"]["num_pages"] == 1
    assert len(text_of(body)) > 500
    blocks = body["result"]["chunks"][0]["blocks"]
    assert blocks and all(0 <= b["bbox"]["left"] <= 1 and b["bbox"]["page"] == 1 for b in blocks)
    # the same job is retrievable afterwards, like on Reducto
    job = client.get(f"/job/{body['job_id']}").json()
    assert job["status"] == "Completed" and job["type"] == "Parse" and job["result"]["job_id"] == body["job_id"]


def test_upload_then_parse_real_pdf(client, validate, bench_pdf):
    file_id = upload(client, bench_pdf)
    r = client.post("/parse", json={"input": file_id, "retrieval": {"chunking": {"chunk_mode": "block"},
                                                                    "filter_blocks": ["Header", "Footer"]}})
    assert r.status_code == 200, r.text
    body = r.json()
    validate(body, "ParseResponse")
    assert len(body["result"]["chunks"]) > 3 and len(text_of(body)) > 500
    unfiltered = client.post("/parse", json={"input": file_id, "retrieval": {"chunking": {"chunk_mode": "block"}}})
    assert len(text_of(body)) <= len(text_of(unfiltered.json()))  # filter_blocks reached the Shape stage
    # the upload object itself is also a valid input
    again = client.post("/parse", json={"input": {"file_id": file_id, "presigned_url": None}})
    assert again.status_code == 200 and len(text_of(again.json())) > 500


def test_upload_then_parse_async_then_job(client, validate, bench_pdf):
    file_id = upload(client, bench_pdf)
    sync = client.post("/parse", json={"input": file_id}).json()
    r = client.post("/parse_async", json={"input": file_id, "async": {"metadata": {"k": "v"}, "priority": True}})
    assert r.status_code == 200 and set(r.json()) == {"job_id"}
    validate(r.json(), "AsyncParseResponse")
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "Completed", job
    validate(job, "EnhancedAsyncJobResponse")
    assert job["type"] == "Parse" and job["num_pages"] == 1 and job["total_pages"] == 1 and job["progress"] == 1.0
    assert job["result"]["job_id"] == r.json()["job_id"]
    assert text_of(job["result"]) == text_of(sync)
    assert '"input"' in job["raw_config"] and job["created_at"].startswith("20")


def test_parse_with_explicit_async_block_on_sync_route(client):
    r = client.post("/parse", json={"input": STEM, "async": {"priority": False}})
    assert r.status_code == 200 and set(r.json()) == {"job_id"}
    assert wait_job(client, r.json()["job_id"])["status"] == "Completed"


def test_unknown_pdf_parses_without_cached_output(client, validate, bench_pdf):
    """A file the cached readers have never seen still returns a valid (possibly empty) response."""
    file_id = upload(client, bench_pdf, name="never-seen-before.pdf")
    r = client.post("/parse", json={"input": file_id})
    assert r.status_code == 200, r.text
    validate(r.json(), "ParseResponse")
    assert r.json()["usage"]["num_pages"] == 1


def test_presigned_upload_flow(client, bench_pdf):
    up = client.post("/upload", params={"extension": ".pdf"}).json()
    assert client.post("/parse", json={"input": up["file_id"]}).status_code == 404  # nothing uploaded yet
    path = up["presigned_url"].replace("http://testserver", "")
    assert client.put(path.split("?")[0] + "?token=wrong", content=b"x").status_code == 403
    assert client.put(path, content=bench_pdf.read_bytes()).status_code == 200
    r = client.post("/parse", json={"input": up["file_id"]})
    assert r.status_code == 200 and r.json()["usage"]["num_pages"] == 1


def test_url_input_is_downloaded(client, receiver, bench_pdf):
    receiver.files[f"/docs/{bench_pdf.name}"] = bench_pdf.read_bytes()
    r = client.post("/parse", json={"input": f"{receiver.url}/docs/{bench_pdf.name}?sig=abc"})
    assert r.status_code == 200, r.text
    assert len(text_of(r.json())) > 500
    bad = client.post("/parse", json={"input": f"{receiver.url}/missing.pdf"})
    assert bad.status_code == 400 and bad.json()["error"]["type"] == "file_download_failure"
    assert "Failed to download file from url" in bad.json()["detail"]


def test_jobid_reuse_and_list_inputs(client, fake_tasks, validate, bench_pdf):
    first = client.post("/parse", json={"input": STEM}).json()
    reuse = client.post("/parse", json={"input": f"jobid://{first['job_id']}"})
    assert reuse.status_code == 200, reuse.text
    assert reuse.json()["job_id"] != first["job_id"] and text_of(reuse.json()) == text_of(first)

    seen = {}
    contracts.OVERRIDES["extract"] = lambda doc, cfg: seen.update(doc=doc, cfg=cfg) or {"result": [{"ok": True}]}
    ex = client.post("/extract", json={"input": f"jobid://{first['job_id']}",
                                       "instructions": {"schema": {"type": "object", "properties": {"ok": {}}}}})
    assert ex.status_code == 200, ex.text
    validate(ex.json(), "V3ExtractResponse")
    assert seen["doc"].__class__.__name__ == "Document" and len(seen["doc"].pages) == 1
    assert seen["cfg"]["instructions"]["schema"]["type"] == "object"
    assert seen["cfg"]["settings"]["citations"] == {"enabled": False, "numerical_confidence": True,
                                                    "parent_block": "full"}
    assert ex.json()["usage"] == {"num_pages": 1, "credits": None, "num_fields": 1}

    both = client.post("/parse", json={"input": [STEM, upload(client, bench_pdf)]})
    assert both.status_code == 200, both.text
    validate(both.json(), "ParseResponse")
    assert both.json()["usage"]["num_pages"] == 2
    assert {b["bbox"]["page"] for c in both.json()["result"]["chunks"] for b in c["blocks"]} == {1, 2}

    multi = client.post("/extract", json={"input": [STEM, f"jobid://{first['job_id']}"]})
    assert multi.status_code == 200 and isinstance(seen["doc"], list) and len(seen["doc"]) == 2

    assert client.post("/parse", json={"input": "jobid://nope"}).status_code == 404
    assert client.post("/parse", json={"input": f"jobid://{ex.json()['job_id']}"}).status_code == 400


def test_force_url_result_and_size_threshold(client, app, validate):
    r = client.post("/parse", json={"input": STEM, "settings": {"force_url_result": True}})
    body = r.json()
    validate(body, "ParseResponse")
    assert body["result"]["type"] == "url" and body["result"]["result_id"]
    fetched = client.get(body["result"]["url"].replace("http://testserver", ""))
    assert fetched.status_code == 200 and len(fetched.json()["chunks"][0]["content"]) > 500
    assert client.get(body["result"]["url"].split("?")[0].replace("http://testserver", "")).status_code == 403
    # a parse that reuses a URL-result job gets the full content back
    reuse = client.post("/parse", json={"input": f"jobid://{body['job_id']}"}).json()
    assert reuse["result"]["type"] == "full"

    app.state.settings.max_inline_bytes = 1000  # stands in for the ~6 MB limit
    big = client.post("/parse", json={"input": STEM}).json()
    assert big["result"]["type"] == "url"
    app.state.settings.max_inline_bytes = 6_000_000
    assert client.post("/parse", json={"input": STEM}).json()["result"]["type"] == "full"


def test_public_url_setting_is_used_in_links(client, app):
    app.state.settings.public_url = "https://docs.example.test"
    body = client.post("/parse", json={"input": STEM, "settings": {"force_url_result": True}}).json()
    assert body["result"]["url"].startswith("https://docs.example.test/files/")
    assert client.post("/upload").json()["presigned_url"].startswith("https://docs.example.test/upload/presigned/")


def test_tasks_run_through_all_four_routes(client, fake_tasks, validate, bench_pdf):
    file_id = upload(client, bench_pdf)
    sp = client.post("/split", json={"input": file_id, "split_description": [{"name": "A", "description": "a"}]})
    validate(sp.json(), "SplitResponse")
    assert sp.json()["result"] == {"splits": [{"name": "A", "pages": [1], "conf": "high", "partitions": None}],
                                   "section_mapping": None}
    cl = client.post("/classify", json={"input": file_id, "classification_schema": [
        {"category": "paper", "criteria": ["has columns"]}, {"category": "invoice", "criteria": []}]})
    validate(cl.json(), "ClassifyResponse")
    assert cl.json()["result"] == {"category": "paper"} and cl.json()["usage"]["num_categories"] == 2
    ed = client.post("/edit", json={"document_url": file_id, "edit_instructions": "fill it"})
    validate(ed.json(), "EditResponse")
    out = client.get(ed.json()["document_url"].replace("http://testserver", ""))
    assert out.status_code == 200 and out.content == bench_pdf.read_bytes()
    # an edited document's URL is accepted as the next input
    assert client.post("/parse", json={"input": ed.json()["document_url"]}).status_code == 200
    job = wait_job(client, client.post("/edit_async", json={"document_url": file_id,
                                                           "edit_instructions": "x"}).json()["job_id"])
    assert job["status"] == "Completed" and job["type"] == "Edit" and job["result"]["document_url"]


def test_missing_task_module_answers_501(client, monkeypatch):
    def boom(name):
        raise ModuleNotFoundError(f"No module named {name!r}")
    monkeypatch.setattr(contracts.importlib, "import_module", boom)
    for path, body in [("/extract", {"input": STEM}),
                       ("/split", {"input": STEM, "split_description": []}),
                       ("/classify", {"input": STEM}),
                       ("/edit", {"document_url": STEM, "edit_instructions": "x"})]:
        r = client.post(path, json=body)
        assert r.status_code == 501, (path, r.text)
        assert r.json()["error"]["type"] == "not_implemented" and r.json()["error"]["name"] == "NOT_APPLICABLE"
    j = client.post("/extract_async", json={"input": STEM}).json()
    job = wait_job(client, j["job_id"])
    assert job["status"] == "Failed" and job["error"]["code"] == 501


def test_task_signature_adapter_and_task_errors(client, fake_tasks, bench_pdf):
    calls = {}

    def edit_run(document, config, *, source_path, output_path=None, options=None):
        calls.update(document=document, source_path=source_path, output_path=output_path)
        with open(output_path, "wb") as fh:
            fh.write(b"%PDF-edited")
        return {"response_type": "edit", "document_url": output_path, "form_schema": None,
                "usage": {"num_pages": 3, "credits": None}}

    contracts.OVERRIDES["edit"] = edit_run
    r = client.post("/edit", json={"document_url": upload(client, bench_pdf), "edit_instructions": "x"})
    assert r.status_code == 200, r.text
    assert calls["document"] is None and calls["source_path"].endswith(".pdf")
    assert r.json()["usage"]["num_pages"] == 3 and r.json()["document_url"].startswith("http://testserver/files/")
    assert client.get(r.json()["document_url"].replace("http://testserver", "")).content == b"%PDF-edited"

    class TaskError(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    def failing(document, config):
        raise TaskError(400, "Invalid schema: 'type' is missing")

    contracts.OVERRIDES["extract"] = failing
    bad = client.post("/extract", json={"input": STEM})
    assert bad.status_code == 400 and bad.json()["detail"] == "Invalid schema: 'type' is missing"
    contracts.OVERRIDES["extract"] = lambda d, c: (_ for _ in ()).throw(ValueError("bad config"))
    assert client.post("/extract", json={"input": STEM}).status_code == 400
    contracts.OVERRIDES["extract"] = lambda d, c: (_ for _ in ()).throw(RuntimeError("boom"))
    crash = client.post("/extract", json={"input": STEM})
    assert crash.status_code == 500 and crash.json()["error"]["name"] == "INTERNAL_ERROR"


def test_input_and_document_errors(client, tmp_path):
    assert client.post("/parse", json={"input": "reducto://unknown.pdf"}).status_code == 404
    assert client.post("/parse", json={"input": "ftp://host/file.pdf"}).status_code == 400
    assert client.post("/parse", json={"input": "/etc/hosts"}).status_code == 400  # local paths are off
    assert client.post("/parse", json={"input": []}).status_code == 400
    for page_range in ({"start": 5, "end": 2}, {"start": 0, "end": 1}, [0]):
        r = client.post("/parse", json={"input": STEM, "settings": {"page_range": page_range}})
        assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_page_range", r.text[:300]
    ok = client.post("/parse", json={"input": STEM, "settings": {"page_range": {"start": 1}}})
    assert ok.status_code == 200 and ok.json()["usage"]["num_pages"] == 1
    junk = client.post("/upload", files={"file": ("blob.zzz", b"\x00\x01not a document", "application/octet-stream")})
    r = client.post("/parse", json={"input": junk.json()["file_id"]})
    assert r.status_code == 415 and r.json()["error"]["code"] == 415, r.text[:300]


def test_password_protected_pdf_is_442(client, bench_pdf, tmp_path):
    pikepdf = pytest.importorskip("pikepdf")
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(bench_pdf) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(user="pw", owner="pw"))
    file_id = upload(client, locked)
    r = client.post("/parse", json={"input": file_id})
    assert r.status_code == 442 and r.json()["error"]["name"] == "DOCUMENT_PASSWORD_PROTECTED", r.text
    ok = client.post("/parse", json={"input": file_id, "settings": {"document_password": "pw"}})
    assert ok.status_code == 200 and ok.json()["usage"]["num_pages"] == 1


def test_uploads_and_results_expire(client, app, bench_pdf):
    file_id = upload(client, bench_pdf)
    job_id = client.post("/parse", json={"input": file_id, "settings": {"force_url_result": True}}).json()["job_id"]
    kept = client.post("/parse", json={"input": file_id, "settings": {"persist_results": True}}).json()["job_id"]
    stored = app.state.files.root / file_id.removeprefix("reducto://")
    assert stored.exists()
    # let every expiry time pass (set directly: sleeping through a 1 s TTL failed on a loaded machine)
    app.state.db.execute("UPDATE files SET expires_at = ? WHERE expires_at IS NOT NULL", (time.time() - 1,))
    app.state.db.execute("UPDATE jobs SET expires_at = ? WHERE expires_at IS NOT NULL", (time.time() - 1,))
    assert client.post("/parse", json={"input": file_id}).status_code == 404  # expired even before the janitor runs
    app.state.worker.purge(force=True)
    assert not stored.exists()
    assert client.get(f"/job/{job_id}").status_code == 410
    assert not (app.state.jobs.root / job_id).exists()
    assert client.get(f"/job/{kept}").json()["status"] == "Completed"  # persist_results skips the expiry
    assert client.post("/parse", json={"input": f"jobid://{job_id}"}).status_code == 410


def test_real_task_modules_are_wired_with_a_fake_llm(client, monkeypatch, validate):
    """readanydoc.tasks (workstream E) through the API, with its FakeLLM: no model is called."""
    pytest.importorskip("readanydoc.tasks.classify")
    monkeypatch.setenv("READANYDOC_TASK_LLM", "fake")
    assert not contracts.OVERRIDES
    r = client.post("/classify", json={"input": STEM, "classification_schema": [
        {"category": "paper", "criteria": ["academic text"]}, {"category": "invoice", "criteria": ["amounts"]}]})
    assert r.status_code != 501, r.text[:300]
    if r.status_code == 200:
        validate(r.json(), "ClassifyResponse")
    else:  # a task-level failure must still be in Reducto's error shape
        assert r.json()["error"]["code"] == r.status_code
    bad = client.post("/classify", json={"input": STEM})  # empty classification_schema -> the task's own 400
    assert bad.status_code == 400 and "classification_schema" in bad.json()["detail"]
