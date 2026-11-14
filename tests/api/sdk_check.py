"""Runs under the THROWAWAY venv that has the official `reductoai` SDK (not the project env).

    <sdk-venv>/bin/python tests/api/sdk_check.py <base_url> <pdf path> <api key>

Prints one JSON object: {"sdk_version", "steps": {name: "ok" | "error: ..."}}. Exit code 0 when the
steps that must work (upload, parse.run, parse.run_job, job.get) all worked.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import reducto
from reducto import Reducto

REQUIRED = ["upload", "parse.run", "parse.run(url result)", "parse.run_job", "job.get", "parse.run(jobid://)"]


def main() -> int:
    base_url, pdf, key = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    client = Reducto(api_key=key, base_url=base_url, max_retries=0, timeout=120)
    steps: dict[str, str] = {}
    ctx: dict = {}

    def step(name, fn):
        try:
            out = fn()
            steps[name] = "ok" if out is None else f"ok: {out}"
        except Exception as e:  # noqa: BLE001
            steps[name] = f"error: {e.__class__.__name__}: {str(e)[:200]}"

    def upload():
        ctx["upload"] = client.upload(file=pdf)
        assert ctx["upload"].file_id.startswith("reducto://")

    def parse_run():
        r = client.parse.run(input=ctx["upload"].file_id)
        assert r.result.type == "full" and r.usage.num_pages == 1 and r.result.chunks[0].blocks[0].bbox.page == 1
        ctx["parse_job"] = r.job_id
        return f"{len(r.result.chunks[0].content)} chars, {len(r.result.chunks[0].blocks)} blocks"

    def parse_upload_object():
        r = client.parse.run(input=ctx["upload"], retrieval={"chunking": {"chunk_mode": "block"}},
                             settings={"model": "r-1-mini"})
        assert len(r.result.chunks) > 1
        return f"{len(r.result.chunks)} chunks"

    def parse_url_result():
        r = client.parse.run(input=ctx["upload"].file_id, settings={"force_url_result": True})
        assert r.result.type == "url" and r.result.url.startswith(base_url)
        import httpx
        assert httpx.get(r.result.url).json()["chunks"]

    def parse_run_job():
        ctx["job"] = client.parse.run_job(input=ctx["upload"].file_id, async_={"metadata": {"from": "sdk"}})
        assert ctx["job"].job_id

    def job_get():
        for _ in range(200):
            j = client.job.get(ctx["job"].job_id)
            if j.status != "Pending":
                break
            time.sleep(0.1)
        assert j.status == "Completed", j.status
        assert j.result.result.chunks[0].content
        return j.status

    def parse_jobid():
        r = client.parse.run(input=f"jobid://{ctx['parse_job']}")
        assert r.result.chunks[0].content

    def job_get_all():
        page = client.job.get_all(limit=2)
        assert len(page.jobs) == 2 and page.next_cursor
        return f"{len(page.jobs)} jobs, cursor ok"

    def job_cancel_delete():
        client.job.cancel(client.parse.run_job(input=ctx["upload"].file_id).job_id)
        d = client.job.delete(ctx["job"].job_id)
        assert d.job_id == ctx["job"].job_id
        try:
            client.job.get(ctx["job"].job_id)
        except reducto.APIStatusError as e:
            assert e.status_code == 410
            return "deleted job -> 410"
        raise AssertionError("deleted job still readable")

    def errors():
        try:
            client.parse.run(input="reducto://missing.pdf")
        except reducto.NotFoundError as e:
            return f"NotFoundError body keys {sorted(e.body)}"
        raise AssertionError("no error")

    def auth():
        try:
            Reducto(api_key="wrong", base_url=base_url, max_retries=0).job.get_all()
        except reducto.AuthenticationError:
            return "AuthenticationError"
        raise AssertionError("no auth error")

    def task(name, **kw):
        def run():
            try:
                r = getattr(client, name).run(**kw)
            except reducto.APIStatusError as e:
                return f"HTTP {e.status_code} ({str(e.body)[:120]})"
            return f"200 {type(r).__name__}"
        return run

    step("upload", upload)
    step("parse.run", parse_run)
    step("parse.run(upload object, options)", parse_upload_object)
    step("parse.run(url result)", parse_url_result)
    step("parse.run_job", parse_run_job)
    step("job.get", job_get)
    step("parse.run(jobid://)", parse_jobid)
    step("job.get_all", job_get_all)
    step("job.cancel + job.delete", job_cancel_delete)
    step("error mapping", errors)
    step("auth", auth)
    step("api_version", lambda: str(client.api_version()))
    step("webhook.run", lambda: repr(client.webhook.run()))
    step("delete_upload", lambda: client.delete_upload(ctx["upload"].file_id).file_id and None)
    if "--tasks" in sys.argv:
        fid = client.upload(file=pdf).file_id
        step("extract.run", task("extract", input=fid, instructions={"schema": {
            "type": "object", "properties": {"title": {"type": "string"}}}}))
        step("split.run", task("split", input=fid, split_description=[{"name": "body", "description": "main text"}]))
        step("classify.run", task("classify", input=fid, classification_schema=[
            {"category": "paper", "criteria": ["academic text"]}, {"category": "invoice", "criteria": ["amounts"]}]))
        step("pipeline.run", task("pipeline", input=fid, pipeline_id="p"))
    print(json.dumps({"sdk_version": reducto.__version__, "steps": steps}, indent=1))
    return 0 if all(steps.get(s, "").startswith("ok") for s in REQUIRED) else 1


if __name__ == "__main__":
    sys.exit(main())
