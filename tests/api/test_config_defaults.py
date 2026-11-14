"""Server defaults that matter for safety and throughput."""

from readanydoc.api import ServerSettings


def test_private_urls_are_refused_by_default(monkeypatch):
    monkeypatch.delenv("READANYDOC_BLOCK_PRIVATE_URLS", raising=False)
    assert ServerSettings().block_private_urls is True
    monkeypatch.setenv("READANYDOC_BLOCK_PRIVATE_URLS", "0")
    assert ServerSettings().block_private_urls is False


def test_documents_have_a_time_limit_and_jobs_run_in_parallel(monkeypatch):
    for k in ("READANYDOC_DOCUMENT_TIMEOUT_SECONDS", "READANYDOC_WORKERS"):
        monkeypatch.delenv(k, raising=False)
    s = ServerSettings()
    assert s.document_timeout == 1800 and s.workers == 4
