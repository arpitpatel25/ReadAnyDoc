"""Local-disk object store: uploads (``reducto://`` ids), downloaded inputs, results and edited files.

Every file gets an unguessable token; ``/files/<id>?token=...`` is our equivalent of a presigned URL.
Rows carry an expiry (uploads 24 h, results 12 h by default) and ``purge_expired`` removes them.
"""

from __future__ import annotations

import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

from .db import Database

SCHEME = "reducto://"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class StoredFile:
    id: str
    kind: str  # upload | download | result | artifact
    path: Path
    filename: str
    content_type: Optional[str]
    token: str
    size: int
    pending: bool
    created_at: float
    expires_at: Optional[float]
    job_id: Optional[str]

    @property
    def file_id(self) -> str:
        return SCHEME + self.id


def safe_name(name: str | None, default: str = "file") -> str:
    name = _SAFE.sub("_", Path(name or "").name).strip("._")
    return name[:150] or default


def normalize_extension(ext: str | None) -> str:
    ext = _SAFE.sub("", (ext or "").strip().lstrip("."))[:12]
    return f".{ext.lower()}" if ext else ""


class FileStore:
    def __init__(self, db: Database, root: str | Path):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        db.execute("""CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, filename TEXT NOT NULL,
            content_type TEXT, token TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
            pending INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, expires_at REAL, job_id TEXT)""")

    @staticmethod
    def strip_scheme(file_id: str) -> str:
        return file_id[len(SCHEME):] if file_id.startswith(SCHEME) else file_id

    def create(self, kind: str, filename: str | None = None, *, extension: str = "", ttl: float | None = None,
               content_type: str | None = None, job_id: str | None = None, pending: bool = False) -> StoredFile:
        """Reserve an id and a path. Write the bytes with ``write`` (or hand ``path`` to a producer)."""
        ext = normalize_extension(extension) or normalize_extension(Path(filename or "").suffix)
        fid = uuid.uuid4().hex + ext
        name = safe_name(filename, default="file" + ext)
        if ext and not name.lower().endswith(ext):
            name += ext
        folder = self.root / fid
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rec = StoredFile(id=fid, kind=kind, path=folder / name, filename=name, content_type=content_type,
                         token=secrets.token_urlsafe(24), size=0, pending=pending, created_at=now,
                         expires_at=(now + ttl) if ttl else None, job_id=job_id)
        self.db.execute("INSERT INTO files(id, kind, path, filename, content_type, token, size, pending, "
                        "created_at, expires_at, job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (rec.id, kind, str(rec.path), name, content_type, rec.token, 0, int(pending), now,
                         rec.expires_at, job_id))
        return rec

    def write(self, rec: StoredFile, data: bytes | BinaryIO, max_bytes: int | None = None) -> StoredFile:
        size = 0
        with open(rec.path, "wb") as fh:
            if isinstance(data, (bytes, bytearray)):
                size = len(data)
                if max_bytes is not None and size > max_bytes:
                    raise OverflowError(size)
                fh.write(data)
            else:
                while chunk := data.read(1 << 20):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise OverflowError(size)
                    fh.write(chunk)
        return self.finish(rec, size)

    def finish(self, rec: StoredFile, size: int | None = None) -> StoredFile:
        """Mark a reserved file as written (after ``write`` or after a producer filled ``rec.path``)."""
        rec.size = rec.path.stat().st_size if size is None else size
        rec.pending = False
        self.db.execute("UPDATE files SET size=?, pending=0 WHERE id=?", (rec.size, rec.id))
        return rec

    def put(self, kind: str, data: bytes | BinaryIO, filename: str | None = None, **kw) -> StoredFile:
        max_bytes = kw.pop("max_bytes", None)
        rec = self.create(kind, filename, **kw)
        try:
            return self.write(rec, data, max_bytes=max_bytes)
        except BaseException:
            self.delete(rec.id)
            raise

    def get(self, file_id: str, include_pending: bool = False) -> Optional[StoredFile]:
        """None when the id is unknown, expired, or (unless asked) still waiting for its bytes."""
        row = self.db.one("SELECT * FROM files WHERE id=?", (self.strip_scheme(file_id),))
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            return None
        if row["pending"] and not include_pending:
            return None
        return StoredFile(id=row["id"], kind=row["kind"], path=Path(row["path"]), filename=row["filename"],
                          content_type=row["content_type"], token=row["token"], size=row["size"],
                          pending=bool(row["pending"]), created_at=row["created_at"],
                          expires_at=row["expires_at"], job_id=row["job_id"])

    def delete(self, file_id: str) -> bool:
        fid = self.strip_scheme(file_id)
        row = self.db.one("SELECT id FROM files WHERE id=?", (fid,))
        if row is None:
            return False
        self.db.execute("DELETE FROM files WHERE id=?", (fid,))
        shutil.rmtree(self.root / fid, ignore_errors=True)
        return True

    def delete_for_job(self, job_id: str) -> int:
        rows = self.db.all("SELECT id FROM files WHERE job_id=?", (job_id,))
        for r in rows:
            self.delete(r["id"])
        return len(rows)

    def purge_expired(self, now: float | None = None) -> int:
        rows = self.db.all("SELECT id FROM files WHERE expires_at IS NOT NULL AND expires_at < ?",
                           (now or time.time(),))
        for r in rows:
            self.delete(r["id"])
        return len(rows)
