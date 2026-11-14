"""Turn a request ``input`` into something the pipeline can read.

Accepted: ``reducto://<id>`` uploads, ``{"file_id": ...}`` upload objects, http(s) URLs (downloaded into
the file store; our own ``/files/...`` links are read straight from disk), ``jobid://<id>`` (reuse a
finished parse), cached bench stems, and local paths when ``READANYDOC_ALLOW_LOCAL_PATHS`` is on.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx

from .. import paths
from ..store import FileStore, JobStore
from . import errors
from .config import ServerSettings

JOB_SCHEME = "jobid://"
_FILES_RE = re.compile(r"/files/([A-Za-z0-9._-]+)")


@dataclass
class Resolved:
    label: str  # what the caller sent
    path: Optional[str] = None  # local file path or cached stem, for the pipeline
    job_id: Optional[str] = None  # set for jobid:// inputs


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


class InputResolver:
    def __init__(self, settings: ServerSettings, files: FileStore, jobs: JobStore, base_url: str = ""):
        self.settings = settings
        self.files = files
        self.jobs = jobs
        self.base_url = base_url.rstrip("/")

    def resolve(self, value: Any) -> Resolved:
        if isinstance(value, dict):
            value = value.get("file_id")
        if not isinstance(value, str) or not value.strip():
            raise errors.bad_request("input must be a URL, a reducto:// file id or a jobid:// reference",
                                     type="invalid_input")
        value = value.strip()
        if value.startswith("reducto://"):
            rec = self.files.get(value)
            if rec is None or not rec.path.exists():
                raise errors.not_found(f"File not found: {value} (uploads expire after "
                                       f"{self.settings.upload_ttl / 3600:g} h)", "file_not_found")
            return Resolved(label=value, path=str(rec.path))
        if value.startswith(JOB_SCHEME):
            return self._job(value)
        if value.startswith(("http://", "https://")):
            return Resolved(label=value, path=str(self._url(value)))
        if "://" in value:
            raise errors.bad_request(f"Invalid URL format: unsupported scheme in {value!r}", type="invalid_url")
        stem = Path(value).stem if value.lower().endswith((".pdf", ".png")) else value
        if "/" not in value and (paths.bench_pdf(stem) or paths.bench_image(stem)):
            return Resolved(label=value, path=value)  # cached bench page; intake resolves the stem
        if self.settings.allow_local_paths and Path(value).is_file():
            return Resolved(label=value, path=str(Path(value).resolve()))
        raise errors.bad_request(f"Invalid URL format: {value!r} is not a URL, reducto:// id or jobid:// reference",
                                 type="invalid_url")

    def _job(self, value: str) -> Resolved:
        jid = value[len(JOB_SCHEME):].strip("/")
        job = self.jobs.get(jid)
        if job is None:
            raise errors.not_found(f"Job not found: {jid}", "job_not_found")
        if job.deleted_at is not None:
            raise ApiGone(jid)
        if job.type != "Parse":
            raise errors.bad_request(f"jobid:// must reference a parse job; {jid} is a {job.type} job",
                                     type="invalid_input")
        if job.status != "Completed":
            raise errors.ApiError(400, "JOB_NOT_COMPLETE", f"Job {jid} is not complete (status {job.status})",
                                  "job_not_complete")
        return Resolved(label=value, job_id=jid)

    def _url(self, url: str) -> Path:
        parsed = urlparse(url)
        m = _FILES_RE.search(parsed.path)
        if m and self.base_url and url.startswith(self.base_url + "/files/"):
            rec = self.files.get(m.group(1))
            if rec is None or not rec.path.exists():
                raise errors.not_found(f"File not found: {url}", "file_not_found")
            return rec.path
        if not parsed.hostname:
            raise errors.bad_request(f"Invalid URL format: {url!r}", type="invalid_url")
        if self.settings.block_private_urls and _is_private(parsed.hostname):
            raise errors.ApiError(400, "URL_NOT_ALLOWED", "URLs that resolve to private addresses are not allowed",
                                  "url_not_allowed")
        name = unquote(Path(parsed.path).name) or "download"
        rec = self.files.create("download", name, ttl=self.settings.upload_ttl)
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=self.settings.download_timeout) as r:
                if r.status_code == 403:
                    raise errors.ApiError(403, "PROCESSING_FAILED", "Permission denied when downloading the URL",
                                          "permission_error")
                if r.status_code >= 400:
                    raise errors.download_failed(f"HTTP {r.status_code}")
                size = 0
                with open(rec.path, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        size += len(chunk)
                        if size > self.settings.max_upload_bytes:
                            raise errors.ApiError(413, "DOCUMENT_TOO_LARGE", "Downloaded file is too large",
                                                  "file_too_large")
                        fh.write(chunk)
            self.files.finish(rec, size)
        except errors.ApiError:
            self.files.delete(rec.id)
            raise
        except (httpx.HTTPError, OSError) as e:
            self.files.delete(rec.id)
            raise errors.download_failed(e.__class__.__name__) from e
        return rec.path


def ApiGone(job_id: str) -> errors.ApiError:
    return errors.ApiError(410, "JOB_DELETED", f"Job {job_id} was deleted or has expired", "job_deleted", job_id)
