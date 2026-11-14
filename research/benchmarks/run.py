"""Runs one of the four full public benchmarks for one or more systems. See common.py.

    cd product && ENGINE_TAG=<tag> uv run python ../e2e/full_bench/run.py <bench> <system>[,<system>] [--workers N] [--limit N]

Cases (all through the real HTTP servers, default options):
  olmocr        1,403 one-page PDFs of allenai/olmOCR-bench (bench_data/pdfs) on /parse. The 140 development-sample
                pages are sent from spike/olmbench/pdfs (same bytes as the files Reducto was sent before).
  omnidocbench  1,651 page images of opendatalab/OmniDocBench on /parse (the 100 sample images from e2e/devset/data).
  funsd         199 form images (149 training + 50 testing; the 50 testing forms are the development sample) on /parse.
  longextract   whole documents of micro1-inc/longextract-bench-50 on /extract with the document's schema,
                settings {array_extract: true, citations off} (the request of e2e/cases_extract.jsonl; no deep mode),
                shortest first, as many as the Reducto budget allows (cases.json written by lx_cases()).
"""
import json, os, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C   # noqa: E402

R = C.R; FB = C.FB
OLM_CATS = ['arxiv_math', 'headers_footers', 'long_tiny_text', 'multi_column', 'old_scans', 'old_scans_math', 'table_tests']


def olm_cases():
    samp = {c['stem']: c for c in map(json.loads, open(R + 'e2e/devset/cases/olm.jsonl'))}
    D = FB + 'olmocr/data/bench_data/'; seen = {}
    for cat in OLM_CATS:
        for l in open(R + D + cat + '.jsonl'):
            if not l.strip(): continue
            t = json.loads(l); stem = os.path.basename(t['pdf'])[:-4]
            if stem in seen: continue
            s = samp.get(stem)
            seen[stem] = {'id': 'olm_' + stem, 'file': s['file'] if s else D + 'pdfs/' + t['pdf'], 'endpoint': 'parse', 'body': {},
                          'stem': stem, 'category': cat, 'pdf': t['pdf'], 'sample': bool(s)}
    return list(seen.values())


def omni_cases():
    samp = {c['image']: c for c in map(json.loads, open(R + 'e2e/devset/cases/omni.jsonl'))}
    out = []
    for p in json.load(open(os.path.expanduser('~/.cache/devset_dl/omni/OmniDocBench.json'))):
        name = p['page_info']['image_path']; a = p['page_info']['page_attribute']; s = samp.get(name)
        out.append({'id': 'omni_' + os.path.splitext(name)[0], 'file': s['file'] if s else FB + 'omnidocbench/data/images/' + name,
                    'endpoint': 'parse', 'body': {}, 'image': name, 'data_source': a.get('data_source'), 'language': a.get('language'),
                    'layout': a.get('layout'), 'sample': bool(s)})
    return out


def funsd_cases():
    samp = {c['id']: c for c in map(json.loads, open(R + 'e2e/devset/cases/funsd.jsonl'))}
    dst = R + FB + 'funsd/data'; out = []
    z = os.path.expanduser('~/.cache/devset_dl/funsd/dataset.zip')
    with zipfile.ZipFile(z) as zf:
        for part in ('training_data', 'testing_data'):
            for n in sorted(x for x in zf.namelist() if x.startswith(f'dataset/{part}/images/') and x.endswith('.png')):
                stem = os.path.basename(n)[:-4]; cid = 'funsd_' + stem; s = samp.get(cid)
                for kind, ext in (('images', 'png'), ('annotations', 'json')):
                    p = f'{dst}/{kind}/{stem}.{ext}'
                    if not os.path.exists(p):
                        os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'wb').write(zf.read(f'dataset/{part}/{kind}/{stem}.{ext}'))
                out.append({'id': cid, 'file': s['file'] if s else f'{FB}funsd/data/images/{stem}.png', 'endpoint': 'parse', 'body': {},
                            'annotation': f'{FB}funsd/data/annotations/{stem}.json', 'part': part, 'sample': bool(s)})
    return out


LX_SETTINGS = {'array_extract': True, 'citations': {'enabled': False}}
LX_FOI = '53cf7eae__foi-eir-request-log-2023-1'          # Reducto's response for this whole document is already cached
LX_PARTIAL = {'6c9a8d3f__auslaendische-bevoelkerung-kreise-pdf': 'first 8 of 33 pages', '06_19_Bankruptcy_Filings_Statistics': 'first 5 of 202 pages'}


def lx_all():
    """Every document of the dataset, shortest first, with its Reducto list price ($0.02 per page)."""
    D = FB + 'longextract/data/'; out = []
    for r in json.load(open(R + FB + 'longextract/sizing.json')):
        s = r['slug']; schema = json.load(open(f'{R}{D}{s}/schema.json'))
        c = {'id': 'lx_' + s[:48].replace(' ', '_'), 'slug': s, 'file': f'{D}{s}/document.pdf', 'endpoint': 'extract', 'pages': r['pages'],
             'gt_rows': r['gt_rows'], 'body': {'instructions': {'schema': schema}, 'settings': LX_SETTINGS},
             'gt': f'{D}{s}/ground_truth.json', 'schema': f'{D}{s}/schema.json', 'sample': s == LX_FOI or s in LX_PARTIAL,
             'sample_note': 'whole document used before' if s == LX_FOI else LX_PARTIAL.get(s)}
        if s == LX_FOI:      # the same 63 pages Reducto was sent before (e2e/task_files/lx_foi_log.pdf): response copied, nothing requested
            c.update(file='e2e/task_files/lx_foi_log.pdf', cache_id='ex_long_foi_log', cached_reducto=True)
        out.append(c)
    return out


LX_ANTHROPIC_DOCS = 10          # vanilla and ours: the 10 shortest documents only (Anthropic budget; see longextract/report.md)
LX_RETRIES_USD = 0.72 + 0.66    # ledger cost of the two repeated requests (Manufacturing: dropped connection; Inactive Ingredient: HTTP 500)


def lx_cases():
    """Shortest first while the new Reducto requests stay within the extract cap; the cached document costs nothing."""
    out = []; usd = LX_RETRIES_USD
    for c in lx_all():
        price = 0.0 if c.get('cached_reducto') else c['pages'] * 0.02
        if usd + price > C.REDUCTO_EXTRACT_CAP: break
        usd += price; out.append(c)
    return out


BENCH = {'olmocr': (olm_cases, {'reducto': 12, 'vanilla': 16, 'ours': 8}),
         'omnidocbench': (omni_cases, {'reducto': 12, 'vanilla': 16, 'ours': 8}),
         'funsd': (funsd_cases, {'reducto': 12, 'vanilla': 16, 'ours': 8}),
         'longextract': (lx_cases, {'reducto': 3, 'vanilla': 2, 'ours': 2})}

if __name__ == '__main__':
    b = sys.argv[1]
    if b == 'longextract' and 'reducto' not in sys.argv[2]:
        BENCH[b] = (lambda: lx_cases()[:LX_ANTHROPIC_DOCS], BENCH[b][1])
    C.main(b, *BENCH[b])
