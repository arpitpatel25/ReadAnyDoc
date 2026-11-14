"""Bearer auth: on when READANYDOC_API_KEY is set, off otherwise."""

from __future__ import annotations

from .conftest import STEM, needs_spike

KEY = "test-key-not-a-real-credential"
GOOD = {"Authorization": f"Bearer {KEY}"}


def test_open_when_no_key_is_configured(client):
    assert client.get("/jobs").status_code == 200
    assert client.get("/jobs", headers={"Authorization": "Bearer anything"}).status_code == 200
    assert client.get("/health").json()["auth"] is False


def test_bearer_required_when_key_is_set(client, monkeypatch):
    monkeypatch.setenv("READANYDOC_API_KEY", KEY)
    for method, path in [("get", "/jobs"), ("get", "/job/x"), ("post", "/parse"), ("post", "/upload"),
                         ("post", "/extract"), ("post", "/cancel/x"), ("delete", "/job/x"),
                         ("delete", "/upload/x"), ("post", "/configure_webhook"), ("post", "/pipeline")]:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {KEY}"},
                        {"Authorization": KEY}):
            r = getattr(client, method)(path, headers=headers)
            assert r.status_code == 401, (method, path, headers, r.status_code)
            assert r.json()["error"]["code"] == 401 and r.json()["error"]["name"] == "AUTH_ERROR"
            assert KEY not in r.text
            assert r.headers["www-authenticate"] == "Bearer"
    assert client.get("/jobs", headers=GOOD).status_code == 200
    assert client.get("/jobs", headers={"authorization": f"bearer {KEY}"}).status_code == 200
    assert client.get("/version").status_code == 200  # version and health stay open
    assert client.get("/health").json() == {"status": "ok", "version": client.get("/version").json().split("-")[1],
                                            "auth": True}


@needs_spike
def test_file_links_work_without_the_api_key(client, monkeypatch):
    monkeypatch.setenv("READANYDOC_API_KEY", KEY)
    assert client.post("/parse", json={"input": STEM}).status_code == 401
    body = client.post("/parse", json={"input": STEM, "settings": {"force_url_result": True}}, headers=GOOD).json()
    url = body["result"]["url"].replace("http://testserver", "")
    assert client.get(url).status_code == 200  # the token in the URL is the credential, like a presigned URL
    assert client.get(url.split("?")[0]).status_code == 403
    up = client.post("/upload", headers=GOOD).json()
    assert client.put(up["presigned_url"].replace("http://testserver", ""), content=b"%PDF-1.4").status_code == 200
