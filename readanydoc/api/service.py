"""Runs one job: resolve inputs, run the parse pipeline or a task, shape Reducto's response."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..store import FileStore, Job, JobStore
from . import contracts, errors
from .config import ServerSettings
from .inputs import InputResolver, Resolved, as_list

PIPELINE_GONE = ("Pipelines are deprecated by Reducto and are not implemented here. "
                 "Chain the endpoints instead: /parse, then /split or /extract with input 'jobid://<job_id>'.")


def _b_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def cheaper_tier(model: Optional[str]) -> bool:
    """True when settings.model = r-1-mini / legacy is served by the cheaper sibling of the configured profile."""
    if model not in ("r-1-mini", "legacy") or _b_env("READANYDOC_MINI_SAME_PROFILE"):
        return False
    if os.environ.get("READANYDOC_PROFILE_" + model.upper().replace("-", "_")):
        return False
    from .. import profiles
    profiles.load_plugins()
    return os.environ.get("READANYDOC_PROFILE", "") in profiles.CHEAPER


def profile_for(model: Optional[str]) -> str:
    """settings.model -> pipeline profile. Env overrides: READANYDOC_PROFILE (all), READANYDOC_PROFILE_R_1,
    READANYDOC_PROFILE_R_1_MINI, READANYDOC_PROFILE_LEGACY."""
    from .. import profiles
    profiles.load_plugins()
    model = model or "r-1"
    own = os.environ.get("READANYDOC_PROFILE_" + model.upper().replace("-", "_"))
    env = own or os.environ.get("READANYDOC_PROFILE")
    if not own and env and model in ("r-1-mini", "legacy") and not _b_env("READANYDOC_MINI_SAME_PROFILE"):
        env = profiles.CHEAPER.get(env, env)  # the cheaper sibling of the configured profile
    return env or profiles.MODEL_TO_PROFILE.get(model, profiles.MODEL_TO_PROFILE["r-1"])


def pipeline_config(parsing: dict[str, Any], page_range: Any = None):
    """Reducto parse options (enhance/retrieval/formatting/spreadsheet/settings) -> PipelineConfig."""
    from ..profiles import get_profile
    st = parsing.get("settings") or {}
    model = st.get("model")
    try:
        cfg = get_profile(profile_for(model))
    except KeyError as e:
        raise errors.internal(f"server misconfigured: {e.args[0]}") from e
    # adapter overrides, e.g. READANYDOC_READER=claude_cli:haiku or openai_compatible:<model>@<url>
    mini = model in ("r-1-mini", "legacy")
    reader = (os.environ.get("READANYDOC_READER_MINI") if mini else None) or os.environ.get("READANYDOC_READER")
    if reader:
        cfg.reader = reader
    elif mini and cheaper_tier(model) and cfg.reader.endswith("#high"):
        # the cheaper tier reads at medium effort: about 35% cheaper and twice as fast as high effort,
        # same text accuracy, slightly worse tables
        cfg.reader = cfg.reader[:-len("#high")] + "#medium"
    if os.environ.get("READANYDOC_LAYOUT"):
        cfg.layout = os.environ["READANYDOC_LAYOUT"]
    if os.environ.get("READANYDOC_WORDS"):
        cfg.words = os.environ["READANYDOC_WORDS"]
    if os.environ.get("READANYDOC_STAGE_OPTIONS"):
        # switches for experiments and deployments, e.g. {"read": {"zoom_prefetch": true}, "triage": {"prepare": true}}
        import json
        for stage_name, opts in json.loads(os.environ["READANYDOC_STAGE_OPTIONS"]).items():
            cfg = cfg.with_stage(stage_name, **opts)
    from ..pipeline.request import PageRangeError, apply_parse_request
    try:
        return apply_parse_request(cfg, parsing, page_range)
    except PageRangeError as e:
        raise errors.bad_request(str(e), type="invalid_page_range") from e


def merge_parse_responses(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Several inputs in one parse call: one response, chunks concatenated, pages renumbered."""
    if len(parts) == 1:
        return parts[0]
    out = json.loads(json.dumps(parts[0]))
    offset = out["usage"]["num_pages"]
    for part in parts[1:]:
        part = json.loads(json.dumps(part))
        for chunk in part["result"]["chunks"]:
            for block in chunk.get("blocks") or []:
                if isinstance(block.get("bbox"), dict) and isinstance(block["bbox"].get("page"), int):
                    block["bbox"]["page"] += offset
            out["result"]["chunks"].append(chunk)
        n = part["usage"]["num_pages"]
        offset += n
        out["usage"]["num_pages"] += n
        ub, pb = out["usage"].get("usage_breakdown"), part["usage"].get("usage_breakdown")
        if isinstance(ub, dict) and isinstance(pb, dict):
            for k in ("parse_pages", "parse_native_pages", "ocr_pages", "charts", "prompted_blocks"):
                ub[k] = (ub.get(k) or 0) + (pb.get(k) or 0)
    return out


def count_fields(value: Any) -> int:
    if isinstance(value, dict):
        if set(value) >= {"value", "citations"}:
            return 1
        return sum(count_fields(v) for v in value.values())
    if isinstance(value, list):
        return sum(count_fields(v) for v in value)
    return 1


class Service:
    def __init__(self, settings: ServerSettings, files: FileStore, jobs: JobStore):
        self.settings = settings
        self.files = files
        self.jobs = jobs
        self._scratch = threading.local()  # ids of the documents the current job parsed (their page renders)
        jobs.on_remove = self._drop_scratch_of

    # ---- working files (page renders, decrypted copies, figure crops): see store/cleanup.py ----
    def _drop_scratch_of(self, job_id: str) -> None:
        """A job's folder is going away: remove the renders of the documents it parsed."""
        if self.settings.cache_dir is None:
            return
        p = self.jobs.root / job_id / "scratch.json"
        if p.is_file():
            from ..store import cleanup
            cleanup.drop_document_scratch(self.settings.cache_dir, json.loads(p.read_text()))

    def _settle_scratch(self, job: Job, keep: bool) -> None:
        """End of a job. A completed Parse job keeps its renders while its result lives (``jobid://`` reuse by
        Extract with images); every other job's renders are removed at once."""
        ids = list(getattr(self._scratch, "ids", []) or [])
        self._scratch.ids = []
        if not ids or self.settings.cache_dir is None:
            return
        try:
            if keep and (self.jobs.root / job.id).is_dir():
                (self.jobs.root / job.id / "scratch.json").write_text(json.dumps(ids))
            else:
                from ..store import cleanup
                cleanup.drop_document_scratch(self.settings.cache_dir, ids)
        except Exception:
            pass

    # ---- links ----
    def file_url(self, base_url: str, rec) -> str:
        return f"{base_url.rstrip('/')}/files/{rec.id}?token={rec.token}"

    # ---- one job, start to finish ----
    def process(self, job: Job) -> tuple[str, Optional[dict[str, Any]], Optional[errors.ApiError]]:
        """Run the job and record the outcome. Returns (status, response, error)."""
        self._scratch.ids = []
        try:
            return self._process(job)
        finally:
            j = self.jobs.get(job.id)
            self._settle_scratch(job, keep=bool(j and j.type == "Parse" and j.status == "Completed"
                                                and j.deleted_at is None))

    def _process(self, job: Job) -> tuple[str, Optional[dict[str, Any]], Optional[errors.ApiError]]:
        try:
            response = self.execute(job)
            self.jobs.update(job.id, status="Completing", progress=1.0)
            self.jobs.write_result(job.id, response)
            usage = response.get("usage") or {}
            persist = bool(((job.request.get("settings") or {}).get("persist_results")))
            status = self.jobs.finish(
                job.id, "Completed", respect_cancel=not job.sync, num_pages=usage.get("num_pages"),
                expires_at=None if persist else time.time() + self.settings.result_ttl)
            if status == "Cancelled":
                self._drop(job.id)
                return status, None, None
            return status, response, None
        except BaseException as exc:  # noqa: BLE001 - every failure becomes a Failed job
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            err = errors.from_exception(exc)
            err.job_id = job.id
            status = self.jobs.finish(job.id, "Failed", respect_cancel=not job.sync, error=err.detail(job.id),
                                      reason=err.message, expires_at=time.time() + self.settings.result_ttl)
            if status == "Cancelled":
                self._drop(job.id)
            return status, None, err

    def _drop(self, job_id: str) -> None:
        self.jobs.remove_artifacts(job_id)
        self.files.delete_for_job(job_id)

    def execute(self, job: Job) -> dict[str, Any]:
        t0 = time.time()
        resolver = InputResolver(self.settings, self.files, self.jobs, job.base_url)
        req = job.request
        if job.type == "Parse":
            response = self._parse(job, req, resolver)
            force = bool((req.get("settings") or {}).get("force_url_result"))
        elif job.type == "Extract":
            response = self._task(job, "extract", req, resolver, (req.get("settings") or {}).get("page_range"))
            force = bool((req.get("settings") or {}).get("force_url_result"))
        elif job.type == "Split":
            response = self._task(job, "split", req, resolver, None)
            force = bool((req.get("settings") or {}).get("force_url_result"))
        elif job.type == "Classify":
            # additive: Classify reads the first 5 pages unless told otherwise, so parse only those
            response = self._task(job, "classify", req, resolver, req.get("page_range") or {"start": 1, "end": 5})
            force = bool(req.get("force_url_result"))
        elif job.type == "Edit":
            response = self._edit(job, req, resolver)
            force = False
        else:
            raise errors.not_implemented(PIPELINE_GONE)
        response["job_id"] = job.id
        response["duration"] = round(time.time() - t0, 4)
        if job.type == "Edit":
            response.pop("duration", None)
        else:
            self._maybe_url(job, response, force)
        return response

    def _maybe_url(self, job: Job, response: dict[str, Any], force: bool) -> None:
        payload = json.dumps(response.get("result"), ensure_ascii=False).encode()
        if not force and len(payload) <= self.settings.max_inline_bytes:
            return
        rec = self.files.put("result", payload, "result.json", ttl=self.settings.result_ttl,
                             content_type="application/json", job_id=job.id)
        response["result"] = {"type": "url", "url": self.file_url(job.base_url, rec), "result_id": rec.id}

    # ---- parse ----
    def _documents_of(self, job_id: str):
        from ..model import Document
        docs = [Document.from_json(d) for d in self.jobs.read_documents(job_id)]
        if not docs:
            raise errors.ApiError(410, "JOB_DELETED", f"Job {job_id} has no stored result (deleted or expired)",
                                  "job_deleted")
        return docs

    def _full_result(self, job_id: str) -> dict[str, Any]:
        """Stored parse response of a previous job with a URL result expanded again."""
        prev = self.jobs.read_result(job_id)
        if prev is None:
            raise errors.ApiError(410, "JOB_DELETED", f"Job {job_id} has no stored result (deleted or expired)",
                                  "job_deleted")
        res = prev.get("result") or {}
        if res.get("type") == "url":
            rec = self.files.get(res.get("result_id", ""))
            if rec is None:
                raise errors.ApiError(410, "JOB_DELETED", f"The result of job {job_id} has expired", "job_deleted")
            prev["result"] = json.loads(rec.path.read_text())
        return prev

    def _run_pipeline(self, item: Resolved, parsing: dict[str, Any], page_range: Any = None, batch: bool = False):
        from ..pipeline import Pipeline
        cfg = pipeline_config(parsing, page_range)
        if batch:  # async jobs through the Message Batches API when READANYDOC_BATCH_ASYNC=1
            from ..readers import batch_mode
            cfg = batch_mode.apply(cfg)
        from ..pipeline import PipelineTimeout
        try:
            doc = Pipeline(cfg).run(item.path, timeout=self.settings.document_timeout or None)
        except PipelineTimeout as e:
            raise errors.ApiError(504, "TIMEOUT", f"{e}; raise READANYDOC_DOCUMENT_TIMEOUT_SECONDS for very long "
                                                  "documents", "timeout") from e
        if getattr(self._scratch, "ids", None) is not None:
            self._scratch.ids.append(doc.id)
        return doc

    def _parse(self, job: Job, req: dict[str, Any], resolver: InputResolver) -> dict[str, Any]:
        from ..serialize import to_parse_response
        inputs = as_list(req.get("input"))
        if not inputs:
            raise errors.bad_request("input is empty", type="invalid_input")
        parts, docs, total = [], [], 0
        fresh = []  # documents parsed by this request (their model calls are this request's token usage)
        for i, raw in enumerate(inputs):
            item = resolver.resolve(raw)
            if item.job_id:
                prev_docs = self._documents_of(item.job_id)
                parts.append(self._full_result(item.job_id))
                docs.extend(prev_docs)
            else:
                from ..readers import batch_mode
                doc = self._run_pipeline(item, req, batch=batch_mode.wanted(job))
                parts.append(to_parse_response(doc, job_id=job.id))
                try:  # additive: page-marker blocks, image / chart-data links, pdf_url
                    from ..pipeline.shaping.response import finish
                    finish(parts[-1], doc, lambda data, name, ctype: self.file_url(job.base_url, self.files.put(
                        "artifact", data, name, ttl=self.settings.result_ttl, content_type=ctype, job_id=job.id)),
                        source=item.path)
                except Exception:  # extras never fail a parse
                    pass
                docs.append(doc)
                fresh.append(doc)
            total = sum(int(d.meta.get("total_pages") or len(d.pages)) for d in docs)
            self.jobs.update(job.id, progress=round((i + 1) / len(inputs) * 0.9, 3), total_pages=total,
                             num_pages=sum(len(d.pages) for d in docs))
        self.jobs.write_documents(job.id, [d.to_json() for d in docs])
        response = merge_parse_responses(parts)
        response["response_type"] = "parse"
        warnings = [f"{n.stage}: {n.message}" for d in fresh for n in d.notes if n.level in ("warning", "error")]
        if warnings:  # not in Reducto's schema; clients that do not know the field ignore it
            response["warnings"] = warnings
        if isinstance(response.get("usage"), dict):  # additive: tokens per model, for cost
            from ..readers.usage import summarize
            response["usage"]["model_usage"] = summarize(e for d in fresh for e in d.usage)
            if fresh:  # which engine profile served settings.model (r-1-mini / legacy may be a cheaper one)
                model = (req.get("settings") or {}).get("model")
                response["usage"]["model_usage"]["requested_model"] = model or "r-1"
                response["usage"]["model_usage"]["profile"] = profile_for(model)
                try:  # what a per-page spending cap skipped (absent when no cap is set)
                    from ..pipeline.reading import budget as _budget
                    cap = _budget.summary(fresh)
                    if cap:
                        response["usage"]["model_usage"]["spend_cap"] = cap
                except Exception:
                    pass
                try:
                    response["usage"]["model_usage"]["reader"] = pipeline_config(req).reader
                except Exception:
                    pass
        return response

    # ---- extract / split / classify ----
    def _task(self, job: Job, name: str, req: dict[str, Any], resolver: InputResolver,
              page_range: Any) -> dict[str, Any]:
        contracts.load_task(name)  # fail with 501 before spending time on parsing
        inputs = as_list(req.get("input"))
        if not inputs:
            raise errors.bad_request("input is empty", type="invalid_input")
        parsing = req.get("parsing") or {}
        docs = []
        fresh_ids = set()  # documents parsed by this request (token usage)
        for i, raw in enumerate(inputs):
            item = resolver.resolve(raw)
            docs.extend(self._documents_of(item.job_id) if item.job_id
                        else [self._run_pipeline(item, parsing, page_range)])
            if not item.job_id:
                fresh_ids.add(id(docs[-1]))
            self.jobs.update(job.id, progress=round((i + 1) / len(inputs) * 0.5, 3),
                             num_pages=sum(len(d.pages) for d in docs),
                             total_pages=sum(int(d.meta.get("total_pages") or len(d.pages)) for d in docs))
        num_pages = sum(len(d.pages) for d in docs)
        target = docs if isinstance(req.get("input"), list) else (docs[0] if len(docs) == 1 else docs)
        out = contracts.call_task(name, target, self._task_config(job, req, parsing))
        if "result" not in out:
            raise errors.internal(f"readanydoc.tasks.{name}.run returned no 'result'")
        usage = dict(out.pop("usage", None) or {})
        usage.setdefault("num_pages", num_pages)
        usage.setdefault("credits", None)
        try:  # tokens per model of this request's parse and of the task's own calls;
            # a document reused through jobid:// was paid for by the request that parsed it. Absent = no calls.
            from ..readers.usage import summarize
            mu = summarize(e for d in docs for e in (getattr(d, "usage", None) or [])
                           if id(d) in fresh_ids or e.get("task"))
            if mu["calls"]:
                usage.setdefault("model_usage", mu)
        except Exception:
            pass
        if name == "extract":
            usage.setdefault("num_fields", count_fields(out["result"]))
            base = {"response_type": "v3_extract", "usage": usage, "studio_link": None}
        elif name == "split":
            if isinstance(out["result"], dict) and "splits" in out["result"] \
                    and not (req.get("settings") or {}).get("deep_split"):
                out["result"].setdefault("section_mapping", None)
            base = {"response_type": "split", "usage": usage}
        else:
            usage.setdefault("num_categories", len(req.get("classification_schema") or []))
            base = {"response_type": "classify", "usage": usage, "response_confidence": None, "extra_metadata": {}}
        return {**base, **out, "usage": usage}

    def _task_config(self, job: Job, req: dict[str, Any], parsing: dict[str, Any]) -> dict[str, Any]:
        out_dir = self.jobs.dir(job.id) / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        model = (parsing.get("settings") or {}).get("model")
        return {**req, "_readanydoc": {"job_id": job.id, "profile": profile_for(model), "output_dir": str(out_dir)}}

    # ---- edit ----
    def _edit(self, job: Job, req: dict[str, Any], resolver: InputResolver) -> dict[str, Any]:
        contracts.load_task("edit")
        item = resolver.resolve(req.get("document_url"))
        if item.job_id:
            raise errors.bad_request("document_url must be a file, not a jobid:// reference", type="invalid_input")
        out = contracts.call_task("edit", item.path, self._task_config(job, req, {}))
        url = out.pop("document_url", None)
        path, data = out.pop("document_path", None), out.pop("document_bytes", None)
        if isinstance(url, str) and not url.startswith(("http://", "https://")):
            path, url = url, None  # the edit task returns the local output path in document_url
        name = out.pop("filename", None)
        if url is None:
            if path is not None and Path(path).is_file():
                with open(path, "rb") as fh:
                    rec = self.files.put("artifact", fh, name or Path(path).name, ttl=self.settings.upload_ttl,
                                         job_id=job.id)
            elif isinstance(data, (bytes, bytearray)):
                rec = self.files.put("artifact", bytes(data), name or f"edited{Path(item.path or '').suffix or '.pdf'}",
                                     ttl=self.settings.upload_ttl, job_id=job.id)
            else:
                raise errors.internal("readanydoc.tasks.edit.run returned no document_path, document_bytes or document_url")
            url = self.file_url(job.base_url, rec)
        usage = dict(out.pop("usage", None) or {})
        usage.setdefault("num_pages", _pdf_pages(item.path))
        usage.setdefault("credits", None)
        return {"response_type": "edit", "form_schema": None, **out, "document_url": url, "usage": usage}


def _pdf_pages(path: Optional[str]) -> int:
    try:
        import pypdfium2 as pdfium
        from ..readers.sizing import _PDFIUM_LOCK
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                return len(pdf)
            finally:
                pdf.close()
        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()
    except Exception:
        return 1
