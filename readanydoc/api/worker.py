"""In-process worker: threads that drain the SQLite queue, plus the expiry janitor."""

from __future__ import annotations

import logging
import threading
import time

from ..store import Database, FileStore, JobStore
from . import webhooks
from .config import ServerSettings
from .service import Service

log = logging.getLogger("readanydoc.api.worker")


class Worker:
    def __init__(self, settings: ServerSettings, db: Database, files: FileStore, jobs: JobStore, service: Service):
        self.settings, self.db, self.files, self.jobs, self.service = settings, db, files, jobs, service
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._last_purge = 0.0

    def ensure_started(self) -> None:
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return
            self._stop.clear()
            self._threads = [threading.Thread(target=self._loop, name=f"readanydoc-worker-{i}", daemon=True)
                             for i in range(self.settings.workers)]
            for t in self._threads:
                t.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout)

    def notify(self) -> None:
        self.ensure_started()
        self._wake.set()

    def purge(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_purge < self.settings.janitor_interval:
            return None
        self._last_purge = now
        from ..store import cleanup  # expiry, orphans, storage limit, working caches (never touches active jobs)
        s = self.settings
        rep = cleanup.clean(self.files, self.jobs, data_dir=s.data_dir, cache_root=s.cache_dir,
                            scratch_ttl=s.cache_ttl, reused_ttl=s.reused_cache_ttl, max_data_bytes=s.max_data_bytes,
                            max_cache_bytes=s.max_cache_bytes, min_age=s.clean_min_age, now=now)
        self.last_clean = rep
        if rep.bytes_freed:
            log.info("janitor freed %.1f MB (%s)", rep.bytes_freed / 1e6,
                     {k: v for k, v in rep.as_dict().items() if v and k != "notes"})
        return rep

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.jobs.claim()
                if job is None:
                    self.purge()
                    self._wake.wait(self.settings.poll_interval)
                    self._wake.clear()
                    continue
                status, _, err = self.service.process(job)
                if err is not None:
                    log.warning("job %s failed: %s %s", job.id, err.name, err.message)
                if status in ("Completed", "Failed") and (job.webhook or {}).get("mode", "disabled") != "disabled":
                    threading.Thread(target=webhooks.deliver, name=f"readanydoc-webhook-{job.id[:8]}", daemon=True,
                                     args=(job.id, self.jobs, self.db, self.settings)).start()
            except Exception:  # the loop must survive anything
                log.exception("worker loop error")
                time.sleep(self.settings.poll_interval)
