"""Batch mode for async jobs (off by default): ``READANYDOC_BATCH_ASYNC=1`` sends the model reads of async,
non-priority parse jobs through the Message Batches API (``anthropic_batch`` reader, half price). Sync
requests and priority jobs are never batched: a batch may take minutes, at most 24 hours."""

from __future__ import annotations

import os

API, BATCH = "anthropic_api:", "anthropic_batch:"


def wanted(job) -> bool:
    return (os.environ.get("READANYDOC_BATCH_ASYNC") == "1" and not getattr(job, "sync", True)
            and not getattr(job, "priority", False))


def apply(cfg):
    """The same pipeline with every ``anthropic_api`` reader replaced by its batch twin, and enough page
    workers that all pages of a document wait in the same batch."""
    if cfg.reader.startswith(API):
        cfg.reader = BATCH + cfg.reader[len(API):]
    read = cfg.stage("read")
    opts = dict(read.options)
    for k, v in list(opts.items()):
        if isinstance(v, str) and v.startswith(API):
            opts[k] = BATCH + v[len(API):]
    opts["page_workers"] = max(int(opts.get("page_workers", 1) or 1), int(os.environ.get("READANYDOC_BATCH_PAGE_WORKERS", "64")))
    return cfg.with_stage("read", **opts)
