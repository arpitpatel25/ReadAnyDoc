"""Disk hygiene: expiry and size limits for the data folder and the working caches.

Three things fill a disk on a long-running server:

1. uploads, downloaded inputs, results and artifacts in the data folder (``files/``, ``jobs/``);
2. page renders and other per-document working files in the cache folder (``renders/``, ``decrypted/``,
   ``figures/``), which are named by a random document id and are never used again after the job;
3. content-keyed caches that are reused (``pdfwords/``, ``tesseract/``, ``rapidocr/``, ``prepared/``) and only
   ever grow.

Rows with an expiry time are removed by ``FileStore.purge_expired`` and ``JobStore.expired`` (as before).
This module adds what was missing: clean-up of (2) when a job ends and by age, an age and size limit for
(3), a size limit for the data folder, and removal of folders that no database row points to.

Nothing here touches a job that is Pending, InProgress or Completing, a file that belongs to such a job or
is its input, or a cache file younger than ``min_age`` seconds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .files import SCHEME, FileStore
from .jobs import ACTIVE, JobStore

# per-document working files: useless once the job that made them has ended
SCRATCH_DIRS = ("renders", "decrypted", "figures")
# reused, keyed by content: kept longer, limited by size
REUSED_DIRS = ("pdfwords", "tesseract", "rapidocr", "prepared")
_ID = re.compile(r"[0-9a-f]{32}(\.[A-Za-z0-9]{1,12})?|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass
class Report:
    jobs_expired: int = 0
    files_expired: int = 0
    orphans_removed: int = 0
    jobs_evicted: int = 0
    files_evicted: int = 0
    cache_files_removed: int = 0
    bytes_freed: int = 0
    data_bytes: int = 0
    cache_bytes: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def _remove(path: Path) -> int:
    """Delete a file or folder; returns the bytes freed (0 if it was already gone)."""
    try:
        if path.is_dir() and not path.is_symlink():
            size = tree_size(path)
            shutil.rmtree(path, ignore_errors=True)
        else:
            size = path.lstat().st_size
            path.unlink()
        return size
    except OSError:
        return 0


# ---------------------------------------------------------------- working caches
def drop_document_scratch(cache_root: str | Path, doc_ids: Iterable[str]) -> int:
    """Remove the renders, decrypted copy and figure crops of documents whose job has ended."""
    root = Path(cache_root)
    freed = 0
    for doc_id in {d for d in doc_ids if d and _ID.fullmatch(str(d))}:
        for p in (root / "renders").glob(f"{doc_id}_*"):
            freed += _remove(p)
        freed += _remove(root / "decrypted" / f"{doc_id}.pdf")
        freed += _remove(root / "figures" / doc_id)
    return freed


def _entries(folder: Path) -> list[tuple[float, int, Path]]:
    """(last use, size, path) of every direct child of a cache folder."""
    out = []
    if not folder.is_dir():
        return out
    for p in folder.iterdir():
        try:
            st = p.lstat()
            if p.is_dir() and not p.is_symlink():
                newest, size = st.st_mtime, 0
                for r, _d, names in os.walk(p):
                    for n in names:
                        s = os.lstat(os.path.join(r, n))
                        newest, size = max(newest, s.st_mtime), size + s.st_size
                out.append((newest, size, p))
            else:
                out.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    return out


def sweep_cache(cache_root: str | Path, *, scratch_ttl: float, reused_ttl: float, max_bytes: int = 0,
                min_age: float = 900.0, now: Optional[float] = None, report: Optional[Report] = None) -> Report:
    """Age out the working caches, then enforce ``max_bytes`` over all of them (oldest first).

    ``scratch_ttl`` / ``reused_ttl`` <= 0 turn the age rule off for that group; ``max_bytes`` <= 0 = no limit.
    Files younger than ``min_age`` are never removed: a running job may be reading them.
    """
    rep = report or Report()
    root = Path(cache_root)
    now = now or time.time()
    kept: list[tuple[float, int, Path]] = []
    for names, ttl in ((SCRATCH_DIRS, scratch_ttl), (REUSED_DIRS, reused_ttl)):
        for name in names:
            for used, size, p in _entries(root / name):
                age = now - used
                if ttl > 0 and age > max(ttl, min_age):
                    freed = _remove(p)
                    rep.cache_files_removed += 1
                    rep.bytes_freed += freed
                else:
                    kept.append((used, size, p))
    total = sum(s for _, s, _ in kept)
    if max_bytes > 0 and total > max_bytes:
        for used, size, p in sorted(kept, key=lambda e: e[0]):
            if total <= max_bytes:
                break
            if now - used < min_age:
                continue
            freed = _remove(p)
            rep.cache_files_removed += 1
            rep.bytes_freed += freed
            total -= size
        if total > max_bytes:
            rep.notes.append("cache is above its limit but the rest is in use (younger than min_age)")
    rep.cache_bytes = total
    return rep


# ---------------------------------------------------------------- data folder
def _input_ids(source: Any) -> set[str]:
    """File ids named by a job's input (``reducto://<id>``, upload objects, lists of them)."""
    out: set[str] = set()
    if isinstance(source, str):
        if source.startswith(SCHEME):
            out.add(source[len(SCHEME):])
    elif isinstance(source, dict):
        for v in source.values():
            out |= _input_ids(v)
    elif isinstance(source, (list, tuple)):
        for v in source:
            out |= _input_ids(v)
    return out


def protected_file_ids(files: FileStore, jobs: JobStore) -> set[str]:
    """Files a waiting or running job still needs: its inputs and whatever it has written so far."""
    marks = ",".join("?" for _ in ACTIVE)
    rows = jobs.db.all(f"SELECT id, source, request FROM jobs WHERE status IN ({marks}) AND deleted_at IS NULL", ACTIVE)
    keep: set[str] = set()
    for r in rows:
        for col in ("source", "request"):
            try:
                keep |= _input_ids(json.loads(r[col])) if r[col] else set()
            except ValueError:
                pass
        keep |= {f["id"] for f in files.db.all("SELECT id FROM files WHERE job_id=?", (r["id"],))}
    return keep


def purge_expired(files: FileStore, jobs: JobStore, now: Optional[float] = None,
                  report: Optional[Report] = None) -> Report:
    """Expired jobs (finished ones only) and expired files, except files an active job needs."""
    rep = report or Report()
    now = now or time.time()
    for jid in jobs.expired(now):
        rep.bytes_freed += tree_size(jobs.root / jid)
        rep.bytes_freed += sum(int(f["size"] or 0) for f in files.db.all("SELECT size FROM files WHERE job_id=?", (jid,)))
        jobs.mark_deleted(jid, reason="Job result expired")
        files.delete_for_job(jid)
        rep.jobs_expired += 1
    keep = protected_file_ids(files, jobs)
    for r in files.db.all("SELECT id, size FROM files WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)):
        if r["id"] in keep:
            continue
        files.delete(r["id"])
        rep.files_expired += 1
        rep.bytes_freed += int(r["size"] or 0)
    return rep


def remove_orphans(files: FileStore, jobs: JobStore, *, min_age: float = 900.0, now: Optional[float] = None,
                   report: Optional[Report] = None) -> Report:
    """Folders under ``files/`` and ``jobs/`` that no row points to (left by a crash or an older version)."""
    rep = report or Report()
    now = now or time.time()
    known_files = {r["id"] for r in files.db.all("SELECT id FROM files")}
    live_jobs = {r["id"] for r in jobs.db.all("SELECT id FROM jobs WHERE deleted_at IS NULL")}
    for root, known in ((files.root, known_files), (jobs.root, live_jobs)):
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if p.name in known or not _ID.fullmatch(p.name):
                continue
            try:
                if now - p.lstat().st_mtime < min_age:
                    continue  # being created right now
            except OSError:
                continue
            rep.bytes_freed += _remove(p)
            rep.orphans_removed += 1
    return rep


def enforce_data_limit(files: FileStore, jobs: JobStore, data_dir: str | Path, max_bytes: int, *,
                       now: Optional[float] = None, report: Optional[Report] = None) -> Report:
    """Above ``max_bytes`` (uploads, results and artifacts under ``data_dir``): drop finished jobs oldest first (results marked persistent last), then loose
    uploads and downloads oldest first. Active jobs and their files are never dropped."""
    rep = report or Report()
    total = tree_size(files.root) + tree_size(jobs.root)  # what can be freed; the database itself is small
    rep.data_bytes = total
    if max_bytes <= 0 or total <= max_bytes:
        return rep
    keep = protected_file_ids(files, jobs)
    rows = jobs.db.all("SELECT id FROM jobs WHERE deleted_at IS NULL AND status IN ('Completed','Failed','Cancelled') "
                       "ORDER BY (expires_at IS NULL) ASC, COALESCE(finished_at, created_at) ASC")
    for r in rows:
        if total <= max_bytes:
            break
        freed = tree_size(jobs.root / r["id"])
        freed += sum(int(f["size"] or 0) for f in files.db.all("SELECT size FROM files WHERE job_id=?", (r["id"],)))
        jobs.mark_deleted(r["id"], reason="Job result removed: the server's storage limit was reached")
        files.delete_for_job(r["id"])
        rep.jobs_evicted += 1
        rep.bytes_freed += freed
        total -= freed
    if total > max_bytes:
        for r in files.db.all("SELECT id, size FROM files WHERE job_id IS NULL ORDER BY created_at ASC"):
            if total <= max_bytes:
                break
            if r["id"] in keep:
                continue
            files.delete(r["id"])
            rep.files_evicted += 1
            rep.bytes_freed += int(r["size"] or 0)
            total -= int(r["size"] or 0)
    if total > max_bytes:
        rep.notes.append("data folder is above its limit but the rest belongs to waiting or running jobs")
    rep.data_bytes = max(total, 0)
    return rep


def clean(files: FileStore, jobs: JobStore, *, data_dir: str | Path, cache_root: str | Path | None,
          scratch_ttl: float = 6 * 3600, reused_ttl: float = 14 * 86400, max_data_bytes: int = 0,
          max_cache_bytes: int = 0, min_age: float = 900.0, now: Optional[float] = None) -> Report:
    """One full pass: expiry, orphans, data limit, caches. Safe to call while jobs run."""
    rep = Report()
    purge_expired(files, jobs, now, rep)
    remove_orphans(files, jobs, min_age=min_age, now=now, report=rep)
    enforce_data_limit(files, jobs, data_dir, max_data_bytes, now=now, report=rep)
    if cache_root is not None:
        sweep_cache(cache_root, scratch_ttl=scratch_ttl, reused_ttl=reused_ttl, max_bytes=max_cache_bytes,
                    min_age=min_age, now=now, report=rep)
    return rep
