"""Shared fixtures for the API tests: an app on a temp data dir, the OpenAPI spec, fake tasks."""

from __future__ import annotations

import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonschema
import pytest

from readanydoc import paths
from readanydoc.api import ServerSettings, contracts, create_app

from ..conftest import HAVE_SPIKE, needs_spike  # noqa: F401  (re-exported)

SPEC_PATH = paths.reference_dir() / "openapi.json"
needs_spec = pytest.mark.skipif(not SPEC_PATH.exists(), reason="reference/openapi.json not available")
STEM = "0097571b436308f563a5808d45d0232255a2_page_6_pg1"
ENV_VARS = ["READANYDOC_API_KEY", "READANYDOC_WEBHOOK_SECRET", "READANYDOC_PROFILE", "READANYDOC_PROFILE_R_1",
            "READANYDOC_PROFILE_R_1_MINI", "READANYDOC_PROFILE_LEGACY", "READANYDOC_READER", "READANYDOC_READER_MINI",
            "READANYDOC_LAYOUT", "READANYDOC_WORDS", "READANYDOC_PUBLIC_URL", "READANYDOC_WEBHOOK_URL",
            "READANYDOC_JOB_STATUS_DETAIL", "READANYDOC_ALLOW_LOCAL_PATHS", "READANYDOC_BLOCK_PRIVATE_URLS"]


@pytest.fixture(scope="session")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


@pytest.fixture(scope="session")
def validate(spec):
    """validate(instance, "SchemaName" | inline schema dict) against the OpenAPI components."""
    def _validate(instance, schema):
        if isinstance(schema, str):
            schema = {"$ref": f"#/components/schemas/{schema}"}
        full = {**schema, "components": spec["components"]}
        errs = sorted(jsonschema.Draft202012Validator(full).iter_errors(instance), key=lambda e: list(e.path))
        assert not errs, "; ".join(f"{list(e.path)}: {e.message[:200]}" for e in errs[:5])
    return _validate


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    # the API tests replay saved model outputs instead of calling a model
    from readanydoc import profiles
    for model, name in {"r-1": "cached-haiku", "r-1-mini": "cached-glm", "legacy": "cached-glm"}.items():
        monkeypatch.setitem(profiles.MODEL_TO_PROFILE, model, name)


@pytest.fixture
def settings(tmp_path) -> ServerSettings:
    # the tests serve documents from a local HTTP server, which the default would refuse
    return ServerSettings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", webhook_backoff=0.01, webhook_timeout=2.0,
                          block_private_urls=False)


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    application.state.worker.stop(timeout=2)
    application.state.db.close()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
def idle_client(app):
    """A client whose worker never starts: async jobs stay Pending."""
    from fastapi.testclient import TestClient
    app.state.worker.ensure_started = lambda: None
    return TestClient(app)


def _fake_extract(document, config):
    docs = document if isinstance(document, list) else [document]
    return {"result": [{"pages": sum(len(d.pages) for d in docs), "documents": len(docs),
                        "schema_keys": sorted((config["instructions"]["schema"] or {}).keys())}]}


def _fake_split(document, config):
    return {"result": {"splits": [{"name": c["name"], "pages": [1], "conf": "high", "partitions": None}
                                  for c in config["split_description"]]}}


def _fake_classify(document, config):
    cats = [c["category"] for c in config["classification_schema"]] or ["other"]
    return {"result": {"category": cats[0]},
            "response_confidence": {"categories": [{"category": c, "confidence": 1.0 if i == 0 else 0.0,
                                                    "criteria_confidence": []} for i, c in enumerate(cats)]}}


def _fake_edit(input_path, config):
    out = Path(config["_readanydoc"]["output_dir"]) / "edited.pdf"
    shutil.copyfile(input_path, out)
    return {"document_path": str(out), "form_schema": config.get("form_schema")}


@pytest.fixture
def fake_tasks():
    contracts.OVERRIDES.update(extract=_fake_extract, split=_fake_split, classify=_fake_classify, edit=_fake_edit)
    yield contracts.OVERRIDES
    contracts.OVERRIDES.clear()


@pytest.fixture(scope="session")
def bench_pdf() -> Path:
    p = paths.bench_pdf(STEM)
    if p is None:
        pytest.skip("cached spike artifacts not available")
    return p


def upload(client, path: Path, name: str | None = None) -> str:
    r = client.post("/upload", files={"file": (name or path.name, path.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def wait_job(client, job_id: str, timeout: float = 30.0, headers: dict | None = None) -> dict:
    deadline = time.time() + timeout
    while True:
        r = client.get(f"/job/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] not in ("Pending", "InProgress", "Completing", "Idle") or time.time() > deadline:
            return body
        time.sleep(0.05)


class Receiver:
    """Local HTTP server: records POSTs (webhook receiver) and serves files (URL inputs)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.fail_first = 0  # answer 500 to this many POSTs before accepting
        self.files: dict[str, bytes] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                code = 500 if len(outer.requests) <= outer.fail_first else 200
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                data = outer.files.get(self.path.split("?")[0])
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait(self, n: int = 1, timeout: float = 10.0) -> list[dict]:
        deadline = time.time() + timeout
        while len(self.requests) < n and time.time() < deadline:
            time.sleep(0.02)
        return self.requests

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()
