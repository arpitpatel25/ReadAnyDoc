"""Job queue semantics: statuses, priority, cancel, delete (202/409/410), listing, recovery."""

from __future__ import annotations

from readanydoc.store import Database, JobStore

from .conftest import STEM, needs_spec, needs_spike, wait_job


def test_queue_priority_and_recovery(tmp_path):
    db = Database(tmp_path / "q.sqlite3")
    jobs = JobStore(db, tmp_path / "jobs")
    a = jobs.create("Parse", {"input": "a"}, sync=False)
    b = jobs.create("Parse", {"input": "b"}, sync=False, priority=True)
    c = jobs.create("Parse", {"input": "c"}, sync=False)
    s = jobs.create("Parse", {"input": "s"}, sync=True)
    assert (a.status, s.status) == ("Pending", "InProgress")
    assert [jobs.claim().id for _ in range(3)] == [b.id, a.id, c.id]  # priority first, then FIFO
    assert jobs.claim() is None
    assert jobs.get(b.id).status == "InProgress" and jobs.get(b.id).started_at
    jobs.update(a.id, status="Completing")
    db.close()

    db2 = Database(tmp_path / "q.sqlite3")  # "restart"
    jobs2 = JobStore(db2, tmp_path / "jobs")
    assert jobs2.recover() == 4
    assert {jobs2.get(x.id).status for x in (a, b, c)} == {"Pending"}
    assert jobs2.get(s.id).status == "Failed" and jobs2.get(s.id).error["name"] == "INTERNAL_ERROR"
    # a cancel that arrives while the job runs wins over the result
    j = jobs2.claim()
    assert jobs2.cancel(j.id) == "InProgress"
    assert jobs2.finish(j.id, "Completed") == "Cancelled"
    assert jobs2.get(j.id).status == "Cancelled"
    db2.close()


def test_cancel_and_delete_semantics(idle_client, app):
    c = idle_client
    job_id = c.post("/parse_async", json={"input": STEM, "async": {"metadata": {"batch": 7}}}).json()["job_id"]
    assert c.get(f"/job/{job_id}").json()["status"] == "Pending"
    conflict = c.delete(f"/job/{job_id}")
    assert conflict.status_code == 409 and conflict.json()["error"]["name"] == "JOB_NOT_COMPLETE"
    assert c.post(f"/cancel/{job_id}").json() == {}
    assert c.post(f"/cancel/{job_id}").status_code == 200  # idempotent
    # GET /job only has Pending/Completed/Failed/Idle in Reducto's schema: a cancelled job reads as Failed
    got = c.get(f"/job/{job_id}").json()
    assert got["status"] == "Failed" and "cancel" in got["reason"].lower() and got["result"] is None
    assert c.get("/jobs").json()["jobs"][0]["status"] == "Cancelled"
    app.state.settings.job_status_detail = True
    assert c.get(f"/job/{job_id}").json()["status"] == "Cancelled"

    deleted = c.delete(f"/job/{job_id}", params={"include_persisted": True})
    assert deleted.status_code == 202 and deleted.json() == {"job_id": job_id}
    gone = c.delete(f"/job/{job_id}")
    assert gone.status_code == 410 and gone.json()["error"]["name"] == "JOB_DELETED"
    assert c.get(f"/job/{job_id}").status_code == 410
    assert c.post(f"/cancel/{job_id}").status_code == 410
    assert job_id not in [j["job_id"] for j in c.get("/jobs").json()["jobs"]]


def test_in_progress_statuses_are_reported(idle_client, app):
    c = idle_client
    job_id = c.post("/parse_async", json={"input": STEM}).json()["job_id"]
    assert app.state.jobs.claim().id == job_id
    assert c.get(f"/job/{job_id}").json()["status"] == "Pending"
    assert c.get("/jobs").json()["jobs"][0]["status"] == "InProgress"
    app.state.settings.job_status_detail = True
    assert c.get(f"/job/{job_id}").json()["status"] == "InProgress"
    assert c.delete(f"/job/{job_id}").status_code == 409


@needs_spec
def test_jobs_listing_cursor_and_exclude_configs(idle_client, validate):
    c = idle_client
    ids = [c.post("/parse_async", json={"input": f"doc-{i}"}).json()["job_id"] for i in range(5)]
    seen, cursor = [], None
    for _ in range(3):
        page = c.get("/jobs", params={"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        validate(page, "JobsResponse")
        seen += [j["job_id"] for j in page["jobs"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == ids[::-1] and cursor is None
    full = c.get("/jobs").json()["jobs"][0]
    assert "doc-4" in full["raw_config"] and full["type"] == "Parse" and full["source"] == "doc-4"
    assert c.get("/jobs", params={"exclude_configs": True}).json()["jobs"][0]["raw_config"] == ""
    assert c.get("/jobs", params={"limit": 0}).status_code == 422
    assert c.get("/jobs", params={"limit": 501}).status_code == 422
    assert c.get("/jobs", params={"cursor": "garbage"}).status_code == 400


@needs_spike
@needs_spec
def test_failed_job_carries_error_detail_and_delete_removes_artifacts(client, app, validate):
    bad = wait_job(client, client.post("/parse_async", json={"input": "reducto://missing.pdf"}).json()["job_id"])
    assert bad["status"] == "Failed" and bad["result"] is None
    validate(bad, "EnhancedAsyncJobResponse")
    assert bad["error"]["code"] == 404 and bad["error"]["name"] == "NOT_FOUND" and bad["error"]["job_id"]

    job_id = client.post("/parse_async", json={"input": STEM, "settings": {"force_url_result": True}}).json()["job_id"]
    done = wait_job(client, job_id)
    assert done["status"] == "Completed"
    assert client.post(f"/cancel/{job_id}").status_code == 409  # finished jobs cannot be cancelled
    url = done["result"]["result"]["url"].replace("http://testserver", "")
    assert client.get(url).status_code == 200
    assert client.delete(f"/job/{job_id}").status_code == 202
    assert client.get(url).status_code == 404 and not (app.state.jobs.root / job_id).exists()
