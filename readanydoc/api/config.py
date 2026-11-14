"""Server settings. Everything comes from environment variables; nothing here is a secret.

The API key (``READANYDOC_API_KEY``) and the webhook signing secret (``READANYDOC_WEBHOOK_SECRET``) are read
from the environment at the moment they are needed and are never stored, logged or returned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

API_KEY_ENV = "READANYDOC_API_KEY"
WEBHOOK_SECRET_ENV = "READANYDOC_WEBHOOK_SECRET"


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _b(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _default_data_dir() -> Path:
    return Path(os.environ.get("READANYDOC_DATA_DIR") or (paths.cache_dir() / "server"))


@dataclass
class ServerSettings:
    data_dir: Path = field(default_factory=_default_data_dir)
    # Base URL used in links we hand out (file URLs, presigned uploads). Empty = taken from each request.
    public_url: str = field(default_factory=lambda: os.environ.get("READANYDOC_PUBLIC_URL", "").rstrip("/"))
    upload_ttl: float = field(default_factory=lambda: _f("READANYDOC_UPLOAD_TTL_HOURS", 24) * 3600)
    result_ttl: float = field(default_factory=lambda: _f("READANYDOC_RESULT_TTL_HOURS", 12) * 3600)
    max_inline_bytes: int = field(default_factory=lambda: int(_f("READANYDOC_MAX_INLINE_BYTES", 6_000_000)))
    max_upload_bytes: int = field(default_factory=lambda: int(_f("READANYDOC_MAX_UPLOAD_MB", 100) * 1024 * 1024))
    # async jobs processed at once; pages of one document are read in parallel within each job
    workers: int = field(default_factory=lambda: max(1, int(_f("READANYDOC_WORKERS", 4))))
    # longest time one document may take (0 = no limit); a request that runs over fails with a timeout error
    document_timeout: float = field(default_factory=lambda: _f("READANYDOC_DOCUMENT_TIMEOUT_SECONDS", 1800.0))
    webhook_retries: int = field(default_factory=lambda: int(_f("READANYDOC_WEBHOOK_RETRIES", 3)))
    webhook_backoff: float = field(default_factory=lambda: _f("READANYDOC_WEBHOOK_BACKOFF_SECONDS", 1.0))
    webhook_timeout: float = field(default_factory=lambda: _f("READANYDOC_WEBHOOK_TIMEOUT_SECONDS", 10.0))
    webhook_url: str = field(default_factory=lambda: os.environ.get("READANYDOC_WEBHOOK_URL", ""))
    download_timeout: float = field(default_factory=lambda: _f("READANYDOC_DOWNLOAD_TIMEOUT_SECONDS", 60.0))
    # Local file paths as `input` let a caller read any file the server can: off unless asked for.
    allow_local_paths: bool = field(default_factory=lambda: _b("READANYDOC_ALLOW_LOCAL_PATHS"))
    # document URLs that point at private or loopback addresses are refused (set to 0 to fetch from internal hosts)
    block_private_urls: bool = field(default_factory=lambda: _b("READANYDOC_BLOCK_PRIVATE_URLS", True))
    # GET /job reports only the statuses in Reducto's schema unless this is on (see docs/decisions.md).
    job_status_detail: bool = field(default_factory=lambda: _b("READANYDOC_JOB_STATUS_DETAIL"))
    janitor_interval: float = field(default_factory=lambda: _f("READANYDOC_JANITOR_SECONDS", 300.0))
    poll_interval: float = 0.25
    # ---- disk hygiene (store/cleanup.py); every limit is checked by the janitor, never against running jobs ----
    # working caches (page renders, decrypted copies, figure crops, word caches); None = leave them alone
    cache_dir: Path | None = field(default_factory=lambda: paths.cache_dir() if _b("READANYDOC_CLEAN_CACHE", True) else None)
    cache_ttl: float = field(default_factory=lambda: _f("READANYDOC_CACHE_TTL_HOURS", 6) * 3600)
    reused_cache_ttl: float = field(default_factory=lambda: _f("READANYDOC_REUSED_CACHE_TTL_DAYS", 14) * 86400)
    max_cache_bytes: int = field(default_factory=lambda: int(_f("READANYDOC_MAX_CACHE_GB", 5) * 1024 ** 3))
    max_data_bytes: int = field(default_factory=lambda: int(_f("READANYDOC_MAX_DATA_GB", 0) * 1024 ** 3))
    clean_min_age: float = field(default_factory=lambda: _f("READANYDOC_CLEAN_MIN_AGE_SECONDS", 900.0))


def api_key_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV))
