"""Official Python SDK (`reductoai`) against a real readanydoc server process.

The SDK lives in a throwaway venv (product/.cache/sdkcheck/venv), never in the project env:

    uv venv --python 3.12 .cache/sdkcheck/venv && uv pip install --python .cache/sdkcheck/venv/bin/python reductoai

Skipped when that venv is missing. Tasks use the fake LLM, so no model is called.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from readanydoc import paths

from .conftest import needs_spike

SDK_PYTHON = paths.REPO_ROOT / ".cache" / "sdkcheck" / "venv" / "bin" / "python"
KEY = "sdk-test-key-not-a-real-credential"
pytestmark = [needs_spike, pytest.mark.skipif(not SDK_PYTHON.exists(), reason="throwaway SDK venv not installed")]


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "READANYDOC_DATA_DIR": str(tmp_path / "data"), "READANYDOC_API_KEY": KEY,
           "READANYDOC_TASK_LLM": "fake",
           # recorded model outputs instead of live calls (development profile from research/)
           "READANYDOC_PROFILE_MODULES": "readanydoc_research.profiles", "READANYDOC_PROFILE": "cached-haiku",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(paths.REPO_ROOT / "research"), os.environ.get("PYTHONPATH")]))}
    for k in ("READANYDOC_PUBLIC_URL", "READANYDOC_READER"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, "-m", "readanydoc.cli", "serve", "--port", str(port)], env=env,
                            cwd=str(paths.REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_official_python_sdk_end_to_end(server, bench_pdf):
    script = Path(__file__).with_name("sdk_check.py")
    out = subprocess.run([str(SDK_PYTHON), "-I", str(script), server, str(bench_pdf), KEY, "--tasks"],
                         capture_output=True, text=True, timeout=180)
    assert out.stdout.strip(), out.stderr[-2000:]
    report = json.loads(out.stdout)
    failed = {k: v for k, v in report["steps"].items() if not v.startswith("ok")}
    assert not failed, failed
    assert out.returncode == 0
    assert report["steps"]["pipeline.run"].startswith("ok: HTTP 501")
    for name in ("extract.run", "split.run", "classify.run"):
        assert "HTTP 501" not in report["steps"][name], report["steps"][name]  # the task modules are wired in
