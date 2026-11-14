"""Direct webhooks: one POST per finished job, retried, optionally HMAC-signed.

Payload (same keys as Reducto's direct webhook): ``{"job_id", "status", "metadata"}``.
When ``READANYDOC_WEBHOOK_SECRET`` is set, every request carries

    X-Readanydoc-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>

``svix`` mode has no URL of its own; it is delivered directly to the default URL (set with
``POST /configure_webhook?url=...`` or ``READANYDOC_WEBHOOK_URL``) and the job records a note saying so.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Optional

import httpx

from ..store import Database, Job, JobStore
from .config import WEBHOOK_SECRET_ENV, ServerSettings

SIGNATURE_HEADER = "X-Readanydoc-Signature"
DEFAULT_URL_KEY = "webhook_url"


def sign(body: bytes, timestamp: int, secret: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(body: bytes, header: str, secret: str, tolerance: Optional[float] = 300) -> bool:
    """Helper for receivers (and our tests)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if tolerance is not None and abs(time.time() - ts) > tolerance:
        return False
    return hmac.compare_digest(sign(body, ts, secret), header)


def target_url(job: Job, db: Database, settings: ServerSettings) -> tuple[Optional[str], Optional[str]]:
    """(url, note). url is None when nothing should be sent."""
    wh = job.webhook or {}
    mode = wh.get("mode", "disabled")
    if mode == "direct":
        return (wh.get("url") or None), (None if wh.get("url") else "direct webhook without a url")
    if mode == "svix":
        url = db.get_setting(DEFAULT_URL_KEY) or settings.webhook_url or None
        note = "svix mode is not supported; delivered as a direct webhook to the default URL"
        if not url:
            note = ("svix mode is not supported and no default webhook URL is configured "
                    "(POST /configure_webhook?url=... or READANYDOC_WEBHOOK_URL); nothing sent")
        return url, note
    return None, None


def deliver(job_id: str, jobs: JobStore, db: Database, settings: ServerSettings) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        return {"delivered": False, "note": "job not found"}
    url, note = target_url(job, db, settings)
    outcome: dict[str, Any] = {"delivered": False, "attempts": 0, "note": note, "url": url}
    if url:
        body = json.dumps({"job_id": job.id, "status": job.status, "metadata": job.metadata}).encode()
        attempts = 1 + max(0, settings.webhook_retries)
        for i in range(attempts):
            headers = {"Content-Type": "application/json", "User-Agent": "readanydoc-webhook"}
            secret = os.environ.get(WEBHOOK_SECRET_ENV)
            if secret:
                headers[SIGNATURE_HEADER] = sign(body, int(time.time()), secret)
            outcome["attempts"] = i + 1
            try:
                r = httpx.post(url, content=body, headers=headers, timeout=settings.webhook_timeout)
                outcome["status_code"] = r.status_code
                if 200 <= r.status_code < 300:
                    outcome["delivered"] = True
                    break
            except httpx.HTTPError as e:
                outcome["error"] = e.__class__.__name__
            if i + 1 < attempts:
                time.sleep(settings.webhook_backoff * (2 ** i))
    if url or note:
        jobs.update(job_id, webhook_status=outcome)
    return outcome
