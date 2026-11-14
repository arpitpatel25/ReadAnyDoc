"""Direct webhooks against a local receiver: payload, HMAC signature, retries, svix-as-direct."""

from __future__ import annotations

import json
import time

from readanydoc.api import webhooks

from .conftest import STEM, needs_spike, wait_job

pytestmark = needs_spike
SECRET = "test-signing-secret"  # a test fixture value, not a credential


def test_direct_webhook_is_signed_and_carries_metadata(client, receiver, monkeypatch):
    monkeypatch.setenv("READANYDOC_WEBHOOK_SECRET", SECRET)
    job_id = client.post("/parse_async", json={"input": STEM, "async": {
        "metadata": {"user": "u1"}, "webhook": {"mode": "direct", "url": f"{receiver.url}/hook"}}}).json()["job_id"]
    got = receiver.wait(1)
    assert len(got) == 1 and got[0]["path"] == "/hook"
    assert json.loads(got[0]["body"]) == {"job_id": job_id, "status": "Completed", "metadata": {"user": "u1"}}
    header = got[0]["headers"][webhooks.SIGNATURE_HEADER]
    assert webhooks.verify(got[0]["body"], header, SECRET)
    assert not webhooks.verify(got[0]["body"] + b" ", header, SECRET)
    assert not webhooks.verify(got[0]["body"], header, "other-secret")
    assert wait_job(client, job_id)["status"] == "Completed"


def test_webhook_is_unsigned_without_secret_and_reports_failures(client, receiver):
    job_id = client.post("/parse_async", json={"input": "reducto://missing.pdf", "async": {
        "webhook": {"mode": "direct", "url": f"{receiver.url}/hook"}}}).json()["job_id"]
    got = receiver.wait(1)
    assert json.loads(got[0]["body"]) == {"job_id": job_id, "status": "Failed", "metadata": None}
    assert webhooks.SIGNATURE_HEADER not in got[0]["headers"]


def test_webhook_retries_until_accepted(client, app, receiver):
    receiver.fail_first = 2
    job_id = client.post("/parse_async", json={"input": STEM, "async": {
        "webhook": {"mode": "direct", "url": f"{receiver.url}/hook"}}}).json()["job_id"]
    assert len(receiver.wait(3)) == 3
    for _ in range(100):
        status = app.state.jobs.get(job_id).webhook_status
        if status:
            break
        time.sleep(0.02)
    assert status["delivered"] is True and status["attempts"] == 3


def test_webhook_gives_up_after_three_retries(client, app, receiver):
    receiver.fail_first = 99
    job_id = client.post("/parse_async", json={"input": STEM, "async": {
        "webhook": {"mode": "direct", "url": f"{receiver.url}/hook"}}}).json()["job_id"]
    assert len(receiver.wait(4)) == 4
    for _ in range(100):
        status = app.state.jobs.get(job_id).webhook_status
        if status:
            break
        time.sleep(0.02)
    assert status["delivered"] is False and status["attempts"] == 4  # 1 try + 3 retries
    assert wait_job(client, job_id)["status"] == "Completed"  # delivery failure does not fail the job


def test_svix_mode_is_delivered_directly_with_a_note(client, app, receiver):
    first = client.post("/parse_async", json={"input": STEM, "async": {"webhook": {"mode": "svix"}}}).json()["job_id"]
    wait_job(client, first)
    for _ in range(100):
        status = app.state.jobs.get(first).webhook_status
        if status:
            break
        time.sleep(0.02)
    assert status["delivered"] is False and "no default webhook URL" in status["note"]

    assert client.post("/configure_webhook", params={"url": f"{receiver.url}/svix"}).json() == f"{receiver.url}/svix"
    assert client.post("/configure_webhook").json() == f"{receiver.url}/svix"
    second = client.post("/parse_async", json={"input": STEM, "async": {
        "webhook": {"mode": "svix", "channels": ["a"]}}}).json()["job_id"]
    got = receiver.wait(1)
    assert got and got[0]["path"] == "/svix" and json.loads(got[0]["body"])["job_id"] == second
    assert client.post("/configure_webhook", params={"url": "not-a-url"}).status_code == 400


def test_edit_async_uses_top_level_webhook(client, fake_tasks, receiver, bench_pdf):
    from .conftest import upload
    job_id = client.post("/edit_async", json={
        "document_url": upload(client, bench_pdf), "edit_instructions": "x",
        "webhook": {"mode": "direct", "url": f"{receiver.url}/edit", "metadata": {"n": 1}}}).json()["job_id"]
    got = receiver.wait(1)
    assert json.loads(got[0]["body"]) == {"job_id": job_id, "status": "Completed", "metadata": {"n": 1}}
