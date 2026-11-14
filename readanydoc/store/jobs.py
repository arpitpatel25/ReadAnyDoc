"""SQLite-backed job table and queue.

Statuses: Pending -> InProgress -> Completing -> Completed | Failed, and Cancelled.
The queue hands out Pending jobs, priority ones first, then oldest first.
"""

from __future__ import annotations

import base64
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .db import Database

ACTIVE = ("Pending", "InProgress", "Completing")
TERMINAL = ("Completed", "Failed", "Cancelled")
_JSON_COLS = ("request", "metadata", "webhook", "error", "source", "webhook_status")


@dataclass
class Job:
    id: str
    seq: int
    type: str  # Parse | Extract | Split | Edit | Pipeline | Classify
    status: str
    sync: bool
    priority: bool
    queue_priority: str
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]
    request: dict[str, Any]
    raw_config: str
    metadata: Any
    webhook: Optional[dict[str, Any]]
    base_url: str
    error: Optional[dict[str, Any]]
    reason: Optional[str]
    source: Any
    num_pages: Optional[int]
    total_pages: Optional[int]
    progress: Optional[float]
    duration: Optional[float]
    cancel_requested: bool
    deleted_at: Optional[float]
    expires_at: Optional[float]
    webhook_status: Optional[dict[str, Any]]


class JobStore:
    def __init__(self, db: Database, root: str | Path):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # called with the job id just before a job's folder is removed (the service drops page renders there)
        self.on_remove = None
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
            status TEXT NOT NULL, sync INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
            queue_priority TEXT NOT NULL DEFAULT 'auto', created_at REAL NOT NULL, started_at REAL,
            finished_at REAL, request TEXT NOT NULL, raw_config TEXT NOT NULL, metadata TEXT, webhook TEXT,
            base_url TEXT NOT NULL DEFAULT '', error TEXT, reason TEXT, source TEXT, num_pages INTEGER,
            total_pages INTEGER, progress REAL, duration REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
            deleted_at REAL, expires_at REAL, webhook_status TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, sync, priority, seq)")

    # ---- rows ----
    @staticmethod
    def _job(row) -> Job:
        d = dict(row)
        for k in _JSON_COLS:
            d[k] = json.loads(d[k]) if d[k] is not None else None
        for k in ("sync", "priority", "cancel_requested"):
            d[k] = bool(d[k])
        d["request"] = d["request"] or {}
        return Job(**d)

    def create(self, type: str, request: dict[str, Any], *, sync: bool, priority: bool = False,
               queue_priority: str = "auto", metadata: Any = None, webhook: dict | None = None,
               base_url: str = "", source: Any = None, raw_config: str | None = None) -> Job:
        jid = str(uuid.uuid4())
        now = time.time()
        self.db.execute(
            "INSERT INTO jobs(id, type, status, sync, priority, queue_priority, created_at, started_at, request, "
            "raw_config, metadata, webhook, base_url, source, progress) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, type, "InProgress" if sync else "Pending", int(sync), int(priority), queue_priority, now,
             now if sync else None, json.dumps(request), raw_config if raw_config is not None else json.dumps(request),
             json.dumps(metadata) if metadata is not None else None,
             json.dumps(webhook) if webhook is not None else None, base_url,
             json.dumps(source) if source is not None else None, 0.0))
        return self.get(jid)  # type: ignore[return-value]

    def get(self, job_id: str) -> Optional[Job]:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return self._job(row) if row else None

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        for k in _JSON_COLS:
            if k in fields and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    # ---- queue ----
    def claim(self) -> Optional[Job]:
        """Atomically take the next Pending async job (priority first, then FIFO)."""
        with self.db.tx() as c:
            row = c.execute("SELECT id FROM jobs WHERE status='Pending' AND sync=0 AND deleted_at IS NULL "
                            "ORDER BY priority DESC, seq ASC LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute("UPDATE jobs SET status='InProgress', started_at=? WHERE id=?", (time.time(), row["id"]))
        return self.get(row["id"])

    def recover(self) -> int:
        """After a restart: re-queue interrupted async jobs, fail interrupted sync ones."""
        n = len(self.db.all("SELECT id FROM jobs WHERE status IN ('InProgress','Completing')"))
        self.db.execute("UPDATE jobs SET status='Pending', started_at=NULL, progress=0 "
                        "WHERE status IN ('InProgress','Completing') AND sync=0")
        err = json.dumps({"code": 500, "name": "INTERNAL_ERROR", "message": "server restarted during the request"})
        self.db.execute("UPDATE jobs SET status='Failed', finished_at=?, error=?, reason='server restarted' "
                        "WHERE status IN ('InProgress','Completing') AND sync=1", (time.time(), err))
        return n

    def cancel(self, job_id: str) -> Optional[str]:
        """Returns the status after the call, or None if the job does not exist."""
        with self.db.tx() as c:
            row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "Pending":
                c.execute("UPDATE jobs SET status='Cancelled', finished_at=?, reason='Job was cancelled', "
                          "cancel_requested=1 WHERE id=?", (time.time(), job_id))
                return "Cancelled"
            if row["status"] in ("InProgress", "Completing"):
                c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            return row["status"]

    def finish(self, job_id: str, status: str, *, respect_cancel: bool = True, **fields: Any) -> str:
        """Set a terminal status unless a cancel request won the race. Returns the final status."""
        for k in _JSON_COLS:
            if k in fields and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        with self.db.tx() as c:
            row = c.execute("SELECT cancel_requested, started_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            now = time.time()
            if row is not None and row["cancel_requested"] and respect_cancel:
                c.execute("UPDATE jobs SET status='Cancelled', finished_at=?, reason='Job was cancelled' WHERE id=?",
                          (now, job_id))
                return "Cancelled"
            fields = {"status": status, "finished_at": now, **fields}
            if "duration" not in fields and row is not None and row["started_at"]:
                fields["duration"] = round(now - row["started_at"], 4)
            cols = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        return status

    # ---- artifacts (result JSON, internal Documents) ----
    def dir(self, job_id: str) -> Path:
        p = self.root / job_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_result(self, job_id: str, result: dict[str, Any]) -> None:
        (self.dir(job_id) / "result.json").write_text(json.dumps(result, ensure_ascii=False))

    def read_result(self, job_id: str) -> Optional[dict[str, Any]]:
        p = self.root / job_id / "result.json"
        return json.loads(p.read_text()) if p.exists() else None

    def write_documents(self, job_id: str, docs_json: list[str]) -> None:
        for i, d in enumerate(docs_json):
            (self.dir(job_id) / f"document-{i:03d}.json").write_text(d)

    def read_documents(self, job_id: str) -> list[str]:
        return [p.read_text() for p in sorted((self.root / job_id).glob("document-*.json"))]

    def remove_artifacts(self, job_id: str) -> None:
        if self.on_remove is not None:
            try:
                self.on_remove(job_id)
            except Exception:  # clean-up extras never block a delete
                pass
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def mark_deleted(self, job_id: str, reason: str = "Job was deleted") -> None:
        self.remove_artifacts(job_id)
        self.update(job_id, deleted_at=time.time(), reason=reason)

    def expired(self, now: float | None = None) -> list[str]:
        rows = self.db.all("SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at < ? AND deleted_at IS NULL "
                           "AND status IN ('Completed','Failed','Cancelled')", (now or time.time(),))
        return [r["id"] for r in rows]

    # ---- listing ----
    @staticmethod
    def encode_cursor(seq: int) -> str:
        return base64.urlsafe_b64encode(json.dumps({"seq": seq}).encode()).decode().rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str) -> int:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        return int(json.loads(raw)["seq"])

    def list(self, limit: int = 100, cursor: str | None = None) -> tuple[list[Job], Optional[str]]:
        """Newest first. ``cursor`` is the opaque value from the previous page."""
        before = self.decode_cursor(cursor) if cursor else None
        sql = "SELECT * FROM jobs WHERE deleted_at IS NULL"
        params: list[Any] = []
        if before is not None:
            sql += " AND seq < ?"
            params.append(before)
        rows = self.db.all(sql + " ORDER BY seq DESC LIMIT ?", (*params, limit + 1))
        jobs = [self._job(r) for r in rows[:limit]]
        nxt = self.encode_cursor(jobs[-1].seq) if len(rows) > limit and jobs else None
        return jobs, nxt
