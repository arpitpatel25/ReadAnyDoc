"""Disk hygiene (readanydoc/store/cleanup.py): expiry, orphans, storage limit, working caches, running jobs."""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from readanydoc.store import Database, FileStore, JobStore, cleanup

from .conftest import needs_spike


@pytest.fixture
def stores(tmp_path):
    db = Database(tmp_path / "data" / "readanydoc.sqlite3")
    files, jobs = FileStore(db, tmp_path / "data" / "files"), JobStore(db, tmp_path / "data" / "jobs")
    yield files, jobs, tmp_path
    db.close()


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def _cache_file(root, folder, name, size=1000, age=0.0):
    p = root / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    _age(p, age)
    return p


def _finished_job(jobs, payload=b"{}", **kw):
    job = jobs.create("Parse", {"input": "x"}, sync=True)
    jobs.write_result(job.id, {"result": payload.decode()})
    jobs.finish(job.id, "Completed", **kw)
    return job


# ---------------------------------------------------------------- working caches
def test_scratch_older_than_ttl_goes_and_fresh_and_reused_stay(tmp_path):
    old = _cache_file(tmp_path, "renders", "a_p1_native.png", age=7 * 3600)
    fresh = _cache_file(tmp_path, "renders", "b_p1_native.png", age=60)
    words = _cache_file(tmp_path, "pdfwords", "k.json", age=7 * 3600)  # reused cache: 14 days
    stale_words = _cache_file(tmp_path, "tesseract", "k.json", age=15 * 86400)
    other = _cache_file(tmp_path, "judge", "verdict.json", age=90 * 86400)  # not ours to clean
    rep = cleanup.sweep_cache(tmp_path, scratch_ttl=6 * 3600, reused_ttl=14 * 86400)
    assert not old.exists() and not stale_words.exists()
    assert fresh.exists() and words.exists() and other.exists()
    assert rep.cache_files_removed == 2 and rep.bytes_freed == 2000 and rep.cache_bytes == 2000


def test_cache_limit_removes_oldest_first_but_never_files_in_use(tmp_path):
    a = _cache_file(tmp_path, "renders", "a.png", size=4000, age=3000)
    b = _cache_file(tmp_path, "pdfwords", "b.json", size=4000, age=2000)
    c = _cache_file(tmp_path, "renders", "c.png", size=4000, age=10)  # a running job is reading it
    rep = cleanup.sweep_cache(tmp_path, scratch_ttl=0, reused_ttl=0, max_bytes=3000, min_age=900)
    assert not a.exists() and not b.exists() and c.exists()
    assert rep.cache_bytes == 4000 and rep.notes  # still above the limit, and it says why


def test_a_ttl_shorter_than_min_age_does_not_delete_files_in_use(tmp_path):
    p = _cache_file(tmp_path, "renders", "a.png", age=120)
    cleanup.sweep_cache(tmp_path, scratch_ttl=1, reused_ttl=1, min_age=900)
    assert p.exists()


def test_drop_document_scratch_takes_only_that_document(tmp_path):
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    gone = [_cache_file(tmp_path, "renders", f"{mine}_p1_native.png"),
            _cache_file(tmp_path, "renders", f"{mine}_p2_150.png"),
            _cache_file(tmp_path, "decrypted", f"{mine}.pdf"),
            _cache_file(tmp_path, "figures", f"{mine}/fig1.png")]
    kept = [_cache_file(tmp_path, "renders", f"{other}_p1_native.png"), _cache_file(tmp_path, "pdfwords", "k.json")]
    freed = cleanup.drop_document_scratch(tmp_path, [mine, "../../etc", "*"])
    assert freed == 4000 and not any(p.exists() for p in gone) and all(p.exists() for p in kept)
    assert not (tmp_path / "figures" / mine).exists()


# ---------------------------------------------------------------- data folder
def test_expired_upload_that_a_waiting_job_needs_is_kept(stores):
    files, jobs, _ = stores
    needed = files.put("upload", b"pdf", "a.pdf", ttl=0.01)
    loose = files.put("upload", b"pdf", "b.pdf", ttl=0.01)
    job = jobs.create("Parse", {"input": needed.file_id}, sync=False, source=needed.file_id)
    time.sleep(0.05)
    rep = cleanup.purge_expired(files, jobs)
    assert needed.path.exists() and not loose.path.exists() and rep.files_expired == 1
    jobs.finish(job.id, "Completed")
    cleanup.purge_expired(files, jobs)
    assert not needed.path.exists()


def test_expired_running_job_is_not_touched(stores):
    files, jobs, _ = stores
    job = jobs.create("Parse", {"input": "x"}, sync=False)
    jobs.claim()
    jobs.update(job.id, expires_at=time.time() - 10)
    art = files.put("artifact", b"img", "crop.png", ttl=0.01, job_id=job.id)
    jobs.write_documents(job.id, ["{}"])
    time.sleep(0.05)
    rep = cleanup.clean(files, jobs, data_dir=files.root.parent, cache_root=None, max_data_bytes=1)
    assert art.path.exists() and (jobs.root / job.id / "document-000.json").exists()
    assert rep.jobs_expired == 0 and rep.jobs_evicted == 0 and rep.notes
    assert jobs.get(job.id).deleted_at is None


def test_orphan_folders_are_removed_but_not_new_ones(stores):
    files, jobs, _ = stores
    known = files.put("upload", b"pdf", "a.pdf")
    job = _finished_job(jobs)
    old_file, old_job, new_file = files.root / (uuid.uuid4().hex + ".pdf"), jobs.root / str(uuid.uuid4()), \
        files.root / uuid.uuid4().hex
    for p in (old_file, old_job, new_file):
        p.mkdir()
        (p / "x").write_bytes(b"12345")
    stranger = files.root / "README"
    stranger.mkdir()
    _age(old_file, 3600)
    _age(old_job, 3600)
    rep = cleanup.remove_orphans(files, jobs, min_age=900)
    assert not old_file.exists() and not old_job.exists() and rep.orphans_removed == 2 and rep.bytes_freed == 10
    assert new_file.exists() and stranger.exists() and known.path.exists() and (jobs.root / job.id).exists()


def test_deleted_jobs_leave_no_folder_behind(stores):
    files, jobs, _ = stores
    job = _finished_job(jobs)
    jobs.mark_deleted(job.id)
    (jobs.root / job.id).mkdir()  # e.g. recreated by a late writer
    _age(jobs.root / job.id, 3600)
    cleanup.remove_orphans(files, jobs)
    assert not (jobs.root / job.id).exists()


def test_storage_limit_drops_oldest_finished_jobs_first_and_persistent_ones_last(stores):
    files, jobs, _ = stores
    big = b"x" * 50_000
    persistent = _finished_job(jobs, big)  # oldest, but persist_results (no expiry)
    time.sleep(0.01)
    old = _finished_job(jobs, big, expires_at=time.time() + 3600)
    files.put("artifact", big, "crop.png", job_id=old.id)
    time.sleep(0.01)
    new = _finished_job(jobs, big, expires_at=time.time() + 3600)
    running = jobs.create("Parse", {"input": "x"}, sync=True)
    jobs.write_documents(running.id, [big.decode()])
    rep = cleanup.enforce_data_limit(files, jobs, files.root.parent, max_bytes=170_000)
    assert rep.jobs_evicted == 1 and jobs.get(old.id).deleted_at is not None
    assert not (jobs.root / old.id).exists() and not files.db.all("SELECT id FROM files WHERE job_id=?", (old.id,))
    assert all(jobs.get(j.id).deleted_at is None for j in (persistent, new, running))
    rep = cleanup.enforce_data_limit(files, jobs, files.root.parent, max_bytes=1)
    assert jobs.get(persistent.id).deleted_at is not None and jobs.get(new.id).deleted_at is not None
    assert (jobs.root / running.id / "document-000.json").exists() and rep.notes


def test_storage_limit_then_takes_loose_uploads_oldest_first(stores):
    files, jobs, _ = stores
    a = files.put("upload", b"x" * 40_000, "a.pdf")
    time.sleep(0.01)
    b = files.put("upload", b"x" * 40_000, "b.pdf")
    cleanup.enforce_data_limit(files, jobs, files.root.parent, max_bytes=60_000)
    assert not a.path.exists() and b.path.exists()


# ---------------------------------------------------------------- through the server
@pytest.fixture
def own_cache(app, tmp_path, monkeypatch):
    """Renders of this test go to a private cache folder that the janitor may clean."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(cache))
    app.state.settings.cache_dir = cache
    return cache


def test_janitor_runs_the_whole_pass_and_reports(app, own_cache):  # no client: the worker thread is not running
    old = _cache_file(own_cache, "renders", "x_p1_native.png", age=7 * 3600)
    orphan = app.state.files.root / uuid.uuid4().hex
    orphan.mkdir()
    _age(orphan, 3600)
    rep = app.state.worker.purge(force=True)
    assert not old.exists() and not orphan.exists()
    assert rep.cache_files_removed == 1 and rep.orphans_removed == 1


@needs_spike
def test_parse_job_renders_live_as_long_as_its_result(client, app, own_cache, tmp_path):
    from PIL import Image
    tif = tmp_path / "two.tiff"
    frames = [Image.new("RGB", (200, 260), "white") for _ in range(2)]
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    with open(tif, "rb") as fh:
        file_id = client.post("/upload", files={"file": ("two.tiff", fh)}).json()["file_id"]
    r = client.post("/parse", json={"input": file_id})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    renders = list((own_cache / "renders").glob("*_f*.png"))
    assert len(renders) == 2  # kept: Extract may reuse this parse through jobid:// with page images
    ids = json.loads((app.state.jobs.root / job_id / "scratch.json").read_text())
    assert len(ids) == 1 and renders[0].name.startswith(ids[0])
    assert client.delete(f"/job/{job_id}").status_code == 202
    assert not list((own_cache / "renders").glob("*.png"))


@needs_spike
def test_task_job_renders_are_removed_when_the_job_ends(client, app, own_cache, tmp_path, fake_tasks):
    from PIL import Image
    tif = tmp_path / "two.tiff"
    frames = [Image.new("RGB", (200, 260), "white") for _ in range(2)]
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    with open(tif, "rb") as fh:
        file_id = client.post("/upload", files={"file": ("two.tiff", fh)}).json()["file_id"]
    seen = {}

    def spy(document, config):
        seen["renders"] = len(list((own_cache / "renders").glob("*.png")))
        return {"result": [{}]}
    fake_tasks["extract"] = spy
    r = client.post("/extract", json={"input": file_id, "instructions": {"schema": {"type": "object"}}})
    assert r.status_code == 200, r.text
    assert seen["renders"] == 2  # there while the job ran
    assert not list((own_cache / "renders").glob("*.png"))


def test_cleanup_command(tmp_path, monkeypatch, capsys):
    from readanydoc import cli
    monkeypatch.setenv("READANYDOC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("READANYDOC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("READANYDOC_CLEAN_CACHE", "1")
    old = _cache_file(tmp_path / "cache", "renders", "x_p1_native.png", size=2_000_000, age=7 * 3600)
    assert cli.main(["cleanup", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["cache_files_removed"] == 1 and rep["bytes_freed"] == 2_000_000 and not old.exists()
