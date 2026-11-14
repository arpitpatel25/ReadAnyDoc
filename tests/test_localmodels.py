"""Round 4: the local models (PP-OCR through RapidOCR / OpenCV, the ONNX layout detector) under concurrent requests.

Two things killed the server in the full RD-TableBench run and are tested here:
* ``cv2.resize`` reading past the end of its source image (OpenCV 5, arm64 KleidiCV): ``readanydoc.cvsafe``;
* local models loaded and run from several request threads at once: ``readanydoc.localmodels`` (one worker thread
  per model).
"""
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from readanydoc import localmodels

WORKER = localmodels.Worker("test")

# ---------------------------------------------------------------------------------------------- localmodels.call


def test_call_runs_on_one_thread_one_at_a_time():
    seen, running, most = set(), [0], [0]
    guard = threading.Lock()

    def work(i):
        with guard:
            running[0] += 1
            most[0] = max(most[0], running[0])
        seen.add(threading.get_ident())
        time.sleep(0.002)
        with guard:
            running[0] -= 1
        return i * 2

    with ThreadPoolExecutor(12) as ex:
        out = list(ex.map(lambda i: WORKER.call(work, i), range(120)))
    assert out == [i * 2 for i in range(120)]
    assert len(seen) == 1 and threading.get_ident() not in seen     # one worker thread, not the callers
    assert most[0] == 1                                             # never two local-model calls at once


def test_call_passes_exceptions_and_nests():
    with pytest.raises(ZeroDivisionError):
        WORKER.call(lambda: 1 / 0)
    assert WORKER.call(lambda: WORKER.call(lambda: WORKER.on_worker())) is True   # no deadlock
    assert WORKER.on_worker() is False
    assert WORKER.call(lambda a, b=0: a + b, 2, b=3) == 5           # the worker survives an exception
    assert localmodels.ocr is not localmodels.layout
    assert localmodels.ocr.call(localmodels.layout.on_worker) is False   # one thread per model


def test_first_use_from_many_threads_starts_one_worker_per_model():
    code = textwrap.dedent("""
        import threading
        from readanydoc import localmodels
        go = threading.Barrier(16); ids = set()
        def first():
            go.wait(); ids.add((localmodels.ocr.call(threading.get_ident), localmodels.layout.call(threading.get_ident)))
        ts = [threading.Thread(target=first) for _ in range(16)]
        [t.start() for t in ts]; [t.join() for t in ts]
        workers = [t for t in threading.enumerate() if t.name.startswith("readanydoc-local-")]
        print(len(ids), len(workers))
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.stdout.split() == ["1", "2"], r.stderr[-500:]     # one OCR thread, one layout thread


# ---------------------------------------------------------------------------------------------- cv2.resize

GUARD = textwrap.dedent("""
    import ctypes, mmap, sys
    import numpy as np, cv2
    if sys.argv[1] == "safe":
        from readanydoc import cvsafe
        assert cvsafe.install() and cvsafe.install()
    h, w, dh, dw = (int(x) for x in sys.argv[2:6])
    PAGE = mmap.PAGESIZE; n = h * w * 3; pages = (n + PAGE - 1) // PAGE
    m = mmap.mmap(-1, (pages + 1) * PAGE)
    addr = ctypes.addressof(ctypes.c_char.from_buffer(m))
    assert ctypes.CDLL(None).mprotect(ctypes.c_void_p(addr + pages * PAGE), ctypes.c_size_t(PAGE), 0) == 0
    img = np.frombuffer(m, dtype=np.uint8, count=n, offset=pages * PAGE - n).reshape(h, w, 3)   # ends at a protected page
    img[:] = (np.arange(n, dtype=np.uint32) % 251).astype(np.uint8).reshape(h, w, 3)
    out = cv2.resize(img, (dw, dh))
    print("ok", out.shape[0], out.shape[1], int(out.astype(np.int64).sum()))
""")
# source h, w -> h, w of the text detector's "round to a multiple of 32": sizes that crashed without the wrapper
SIZES = [(706, 1481, 704, 1472), (1474, 771, 1472, 768), (801, 389, 800, 384), (1060, 1061, 1056, 1056)]


def _guard(mode, size):
    return subprocess.run([sys.executable, "-c", GUARD, mode, *map(str, size)], capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(sys.platform == "win32", reason="mprotect")
@pytest.mark.parametrize("size", SIZES)
def test_resize_does_not_read_past_the_image(size):
    """An image whose last byte is the last readable byte of memory: the wrapped ``cv2.resize`` must survive it.
    (Without the wrapper this dies with SIGBUS / SIGSEGV on arm64 with OpenCV 5.0.0: the server crash.)"""
    pytest.importorskip("cv2")
    r = _guard("safe", size)
    assert r.returncode == 0 and r.stdout.startswith("ok"), (r.returncode, r.stderr[-400:])
    raw = _guard("raw", size)
    if raw.returncode == 0:                      # a build without the over-read: the wrapper must change nothing
        assert raw.stdout == r.stdout


def test_safe_resize_gives_the_same_pixels():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from readanydoc import cvsafe
    assert cvsafe.install()
    raw = cv2.resize.__readanydoc_raw__
    rng = np.random.default_rng(4)
    for shape, dsize in (((120, 333, 3), (320, 48)), ((706, 500, 3), (496, 704)), ((64, 64), (200, 100)),
                         ((37, 211, 3), (274, 48)), ((300, 200, 3), (100, 150))):
        src = rng.integers(0, 255, shape, dtype=np.uint8)
        assert np.array_equal(raw(src, dsize), cv2.resize(src, dsize))
        assert np.array_equal(raw(src, dsize, interpolation=cv2.INTER_CUBIC), cv2.resize(src, dsize, interpolation=cv2.INTER_CUBIC))
    f = rng.random((50, 70, 3), dtype=np.float32)
    assert np.array_equal(raw(f, (64, 32)), cv2.resize(f, (64, 32)))
    assert cvsafe.padded("not an array") == "not an array"


# ---------------------------------------------------------------------------------------------- the real models


def _page(seed: int):
    """A page image with a few text lines and a ruled table, as a numpy BGR array and PNG bytes."""
    import io
    import numpy as np
    from PIL import Image, ImageDraw
    w, h = 900 + 37 * (seed % 5), 700 + 41 * (seed % 7)
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    for k in range(6):
        d.text((60, 40 + 34 * k), f"Line {k} of page {seed}: invoice total {1000 + seed * 7 + k}", fill="black")
    for r in range(6):
        d.line((60, 300 + 40 * r, w - 60, 300 + 40 * r), fill="black", width=2)
    for c in range(5):
        x = 60 + c * (w - 120) // 4
        d.line((x, 300, x, 500), fill="black", width=2)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return np.ascontiguousarray(np.asarray(im)[:, :, ::-1]), buf.getvalue()


def test_ocr_and_layout_from_many_threads():
    """Detection, recognition, full OCR and the layout model called from 8 threads at once, first use included:
    no crash, every OCR call on the one OCR thread, and the same answers as one call at a time."""
    from readanydoc.layout import onnx as lay
    from readanydoc.pipeline.grounding import ocr
    have_ocr, have_layout = ocr.available(), lay.available("heron")
    if not have_ocr and not have_layout:
        pytest.skip("no local model installed")
    pages = [_page(s) for s in range(6)]
    threads_seen = set()
    real_engine = ocr._engine

    def engine(*a, **k):
        threads_seen.add(threading.get_ident())
        assert localmodels.ocr.on_worker()
        return real_engine(*a, **k)

    ocr._engine = engine
    try:
        def job(i):
            arr, png = pages[i % len(pages)]
            out = {}
            if have_ocr:
                out["det"] = len(ocr.detect_lines(arr))
                out["rec"] = [t for t, _ in ocr.recognise([arr[30:70, 50:520]])]
                out["full"] = [r[0] for r in ocr.run_image(arr)[0]]
            if have_layout:
                out["layout"] = sorted((lab, round(sc, 3)) for lab, sc, _ in lay.OnnxLayout("heron").raw(png))
            return out
        with ThreadPoolExecutor(8) as ex:              # the first calls load the models: started together
            together = list(ex.map(job, range(24)))
        alone = [job(i) for i in range(len(pages))]
    finally:
        ocr._engine = real_engine
    for i, got in enumerate(together):
        assert got == alone[i % len(pages)], i
    if have_ocr:
        assert len(threads_seen) == 1
        assert any("invoice" in " ".join(a["full"]).lower() for a in alone)
