"""Parse one shard of a public benchmark through a running ReadAnyDoc server, for the cloud benchmark workflow
(.github/workflows/benchmarks.yml). Downloads its own data, pinned to fixed revisions; sends each document the way
the published runs did (POST /upload, then POST /parse with default options); saves one JSON per case, with the
same case ids as the published results, so the existing scorers and per-document comparisons work unchanged.

    python research/benchmarks/ci_parse.py <olmocr|omnidocbench|funsd|rdtb> --shard 0/4 --out OUT [--server URL]

A case with a saved good response is skipped, so a re-run resumes. No scoring here (scoring is free and is done
afterwards next to the earlier results).
"""
import argparse
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from readanydoc.bench import fetch

OLM = ("allenai/olmOCR-bench", "54a96a6fb6a2bd3b297e59869491db4d3625b711")
OMNI = ("opendatalab/OmniDocBench", "aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec")
RDTB = ("reducto/rd-tablebench", "7748503e2bd5f210d27aa2ef5fdf4b8aa13099bb")
FUNSD_ZIP = "https://guillaumejaume.github.io/FUNSD/dataset.zip"
OLM_CATS = ["arxiv_math", "headers_footers", "long_tiny_text", "multi_column", "old_scans", "old_scans_math", "table_tests"]


def olm_cases():
    seen = {}
    for cat in OLM_CATS:
        for line in open(fetch.hf_file(OLM[0], f"bench_data/{cat}.jsonl", OLM[1])):
            if not line.strip():
                continue
            t = json.loads(line)
            stem = os.path.basename(t["pdf"])[:-4]
            if stem not in seen:
                seen[stem] = {"id": "olm_" + stem, "get": lambda p=t["pdf"]: fetch.hf_file(OLM[0], f"bench_data/pdfs/{p}", OLM[1])}
    return list(seen.values())


def omni_cases():
    pages = json.loads(fetch.hf_file(OMNI[0], "OmniDocBench.json", OMNI[1]).read_text())
    out = []
    for p in pages:
        name = p["page_info"]["image_path"]
        out.append({"id": "omni_" + os.path.splitext(name)[0],
                    "get": lambda n=name: fetch.hf_file(OMNI[0], f"images/{n}", OMNI[1])})
    return out


def _unzipped(url_or_path: Path, dest: Path) -> Path:
    if not dest.exists():
        with zipfile.ZipFile(url_or_path) as z:
            z.extractall(dest)
    return dest


def funsd_cases():
    z = fetch._get(FUNSD_ZIP, fetch.root() / "funsd" / "dataset.zip")
    d = _unzipped(z, fetch.root() / "funsd" / "unzipped")
    return [{"id": "funsd_" + f.stem, "get": lambda f=f: f}
            for part in ("training_data", "testing_data") for f in sorted((d / "dataset" / part / "images").glob("*.png"))]


def rdtb_cases():
    d = _unzipped(fetch.hf_file(RDTB[0], "rd-tablebench.zip", RDTB[1]), fetch.root() / "rdtb" / "unzipped")
    imgs = sorted(p for p in d.rglob("*.jpg") if "_images" in p.parts)
    return [{"id": "tb_" + p.stem, "get": lambda p=p: p} for p in imgs]


CASES = {"olmocr": olm_cases, "omnidocbench": omni_cases, "funsd": funsd_cases, "rdtb": rdtb_cases}


def good(f: Path) -> bool:
    try:
        d = json.loads(f.read_text())
    except Exception:
        return False
    return d.get("_harness", {}).get("status") == 200 and isinstance(d.get("result"), (dict, list))


def one(case: dict, out: Path, base: str) -> str:
    dest = out / f"{case['id']}.json"
    if dest.exists() and good(dest):
        return "saved"
    path = Path(case["get"]())
    t0 = time.time()
    d: dict = {}
    status = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=1800) as c:
                with open(path, "rb") as fh:
                    up = c.post(base + "/upload", files={"file": (path.name, fh)})
                up.raise_for_status()
                r = c.post(base + "/parse", json={"input": up.json()["file_id"]})
                status = r.status_code
                d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text[:2000]}
                if r.status_code == 200 and isinstance(d.get("result"), dict) and d["result"].get("type") == "url":
                    d["result"] = c.get(d["result"]["url"]).json()
            if status == 200:
                break
        except Exception as e:  # network or server error: retry, then save the failure
            d, status = {"error": str(e)[:500]}, None
        time.sleep(10 * (attempt + 1))
    d["_harness"] = {"target": "ci", "status": status, "seconds": round(time.time() - t0, 2), "body": {}}
    dest.write_text(json.dumps(d))
    return "ok" if status == 200 else "failed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", choices=sorted(CASES))
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8884")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    i, n = (int(x) for x in a.shard.split("/"))
    cases = sorted(CASES[a.bench](), key=lambda c: c["id"])[i::n]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{a.bench} shard {i}/{n}: {len(cases)} cases", flush=True)
    done = {"ok": 0, "saved": 0, "failed": 0}
    with ThreadPoolExecutor(a.workers) as ex:
        for k, res in enumerate(ex.map(lambda c: one(c, out, a.server), cases), 1):
            done[res] += 1
            if k % 25 == 0 or k == len(cases):
                print(f"  {k}/{len(cases)} {done}", flush=True)
    return 0 if done["failed"] <= max(3, len(cases) // 50) else 1


if __name__ == "__main__":
    sys.exit(main())
