"""Scores the full olmOCR-bench run (1,403 pages, 7 test files) with the official test classes.

    cd product && uv run python ../e2e/full_bench/olmocr/score.py md        responses -> md/<system>/<stem>.md + meta.json
    ~/.cache/full_bench/olm_env/bin/python e2e/full_bench/olmocr/score.py tests   official tests -> tests_<system>.json
    cd product && uv run python ../e2e/full_bench/olmocr/score.py report    -> scores.json, results.md

md      The prediction of a page is exactly the devset's (e2e/devset/score.py score_olm): block contents joined with
        blank lines, with Header, Footer and Page Number blocks removed (`content(d, drop=FURNITURE)`); a failed or
        missing response has no file and every test of the page fails ("missing output").
tests   Official code: spike/src/olmocr (allenai/olmocr, olmocr/bench/tests.py: load_tests and each test's run()),
        the same classes `readanydoc bench` uses (readanydoc/bench/olm.py). Differences from `readanydoc bench`: (1) the
        math tests are really run (Playwright + KaTeX render and compare, as upstream; `readanydoc bench` stubs
        Playwright because the 5 sample categories have no math test); (2) as upstream benchmark.py, one BaselineTest
        is added for every PDF that has none and they form their own group "baseline". Runs in a helper environment
        (~/.cache/full_bench/olm_env: playwright, rapidfuzz, fuzzysearch, bs4) because the product has no Playwright.
report  Category = share of the tests of that file passed. `overall8` = mean of the 7 file rates and the baseline
        group (the official score of benchmark.py); `mean5` = mean of the 5 categories of the development sample
        (the devset's olm.mean); `mean7` = mean of the 7 files. Intervals: bootstrap over PAGES within each category.
"""
import hashlib, json, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__)); FBD = os.path.dirname(HERE); E2E = os.path.dirname(FBD); ROOT = os.path.dirname(E2E)
CATS = ['arxiv_math', 'headers_footers', 'long_tiny_text', 'multi_column', 'old_scans', 'old_scans_math', 'table_tests']
CATS5 = ['headers_footers', 'long_tiny_text', 'multi_column', 'old_scans', 'table_tests']
SYSTEMS = ['reducto', 'ours', 'vanilla']
DATA = HERE + '/data/bench_data'


def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m


def cmd_md():
    sys.argv = sys.argv[:1]
    DEV = _load('devscore', E2E + '/devset/score.py'); sys.path.insert(0, FBD)
    import common as C
    RUNF = _load('fbrun', FBD + '/run.py'); C.STATE['bench'] = 'olmocr'
    cs = RUNF.olm_cases()
    for s in SYSTEMS:
        if not os.path.isdir(f'{HERE}/responses/{s}'): continue
        os.makedirs(f'{HERE}/md/{s}', exist_ok=True); meta = {}; n = 0
        for c in cs:
            d = C.load(s, c); ok = DEV.ok(d); p = f"{HERE}/md/{s}/{c['stem']}.md"
            if ok:
                md = DEV.content(d, drop=DEV.FURNITURE); open(p, 'w').write(md); n += 1
            elif os.path.exists(p): os.remove(p)
            meta[c['stem']] = {'ok': ok, 'missing': d is None, 'usd': DEV.response_cost(s, c, d)[0] if ok else None,
                               'seconds': d['_harness'].get('seconds') if ok else None,
                               'empty': ok and not DEV.content(d).strip(), 'empty_after_filter': ok and not md.strip(),
                               'types': sorted({b.get('type') for b in DEV.blocks(d)}) if ok else []}
        json.dump(meta, open(f'{HERE}/md/{s}/meta.json', 'w')); print(s, n, 'markdown files of', len(cs))
    json.dump([{k: c[k] for k in ('id', 'stem', 'category', 'pdf', 'sample')} for c in cs], open(HERE + '/cases.json', 'w'))


def cmd_tests():
    from concurrent.futures import ThreadPoolExecutor
    src = ROOT + '/spike/src/olmocr/olmocr'
    pkg = types.ModuleType('olmocr'); pkg.__path__ = [src]; sys.modules['olmocr'] = pkg     # the package without its heavy __init__
    from olmocr.bench.tests import load_tests, BaselineTest
    tests = []
    for c in CATS:
        for t in load_tests(f'{DATA}/{c}.jsonl'): tests.append((c, t))
    pdfs = sorted({t.pdf for _, t in tests})
    for pdf in pdfs:                                                                        # as upstream benchmark.py
        if not any(t.type == 'baseline' and t.pdf == pdf for _, t in tests):
            tests.append(('baseline', BaselineTest(id=f'{pdf}_baseline', pdf=pdf, page=1, type='baseline')))
    print('tests', len(tests), 'pdfs', len(pdfs), flush=True)
    only = sys.argv[2].split(',') if len(sys.argv) > 2 else SYSTEMS
    for s in only:
        if not os.path.isdir(f'{HERE}/md/{s}'): continue
        outp = f'{HERE}/tests_{s}.json'; old = json.load(open(outp)) if os.path.exists(outp) else {}
        mds = {}; shas = {}
        for pdf in pdfs:
            stem = os.path.basename(pdf)[:-4]; p = f'{HERE}/md/{s}/{stem}.md'
            mds[pdf] = open(p, encoding='utf-8').read() if os.path.exists(p) else None
            shas[pdf] = hashlib.sha1(mds[pdf].encode()).hexdigest() if mds[pdf] is not None else 'missing'

        def one(ct):
            c, t = ct; prev = old.get(t.id)
            if prev and prev[4] == shas[t.pdf]: return t.id, prev
            md = mds[t.pdf]
            if md is None: ok, why = False, 'missing output'
            else:
                try: ok, why = t.run(md)
                except Exception as e: ok, why = False, f'scorer error: {e}'
            return t.id, [c, t.pdf, t.type, bool(ok), shas[t.pdf], str(why)[:200]]
        res = {}
        with ThreadPoolExecutor(8) as ex:
            for n, (tid, r) in enumerate(ex.map(one, tests), 1):
                res[tid] = r
                if n % 1000 == 0: print(s, n, len(tests), flush=True)
        json.dump(res, open(outp, 'w')); print(s, 'passed', sum(r[3] for r in res.values()), 'of', len(res), flush=True)


def cmd_report():
    sys.path.insert(0, FBD)
    import stats as S
    cs = json.load(open(HERE + '/cases.json')); by_pdf = {c['pdf']: c for c in cs}
    ids = [c['id'] for c in cs]; samp = {c['id'] for c in cs if c['sample']}; cat = {c['id']: c['category'] for c in cs}
    systems = [s for s in SYSTEMS if os.path.exists(f'{HERE}/tests_{s}.json')]
    groups = CATS + ['baseline']
    per = {s: {g: {} for g in groups} for s in systems}; rows = {s: [] for s in systems}; fails = {s: {} for s in systems}
    types_ = {s: {} for s in systems}
    for s in systems:
        for tid, (g, pdf, typ, ok, _sha, why) in json.load(open(f'{HERE}/tests_{s}.json')).items():
            i = by_pdf[pdf]['id']; a = per[s][g].get(i, (0, 0)); per[s][g][i] = (a[0] + ok, a[1] + 1)
            b = types_[s].setdefault((g, typ), [0, 0]); b[0] += ok; b[1] += 1
            if not ok: fails[s].setdefault(i, []).append([g, tid, typ, why])
        meta = json.load(open(f'{HERE}/md/{s}/meta.json'))
        for c in cs:
            m = meta[c['stem']]; rows[s].append((m['ok'], m['usd'], 1, m['seconds'], m['empty']))
    # composites: a "measure" whose items carry (passed, tests) per page and whose groups are the categories
    comp = {'overall8': groups, 'mean7': CATS, 'mean5': CATS5}
    M = [('overall8', 'OVERALL: mean of the 7 categories and the baseline group (official benchmark.py score, %)', '+', 100, '%.1f', True),
         ('mean7', 'mean of the 7 categories, no baseline group (%)', '+', 100, '%.1f', True),
         ('mean5', 'mean of the 5 categories of the development sample (devset olm.mean, %)', '+', 100, '%.1f', True)]
    M += [(g, f'{g}: tests passed (%)', '+', 100, '%.1f', False) for g in groups]
    subsets = {'full': ids, 'clean': [i for i in ids if i not in samp], 'sample': [i for i in ids if i in samp]}
    out = {'systems': {s: {} for s in systems}, 'paired': {}}
    for key, label, better, scale, fmt, grouped in M:
        if grouped:      # one item per (page, group); groups = the member categories
            gs = comp[key]; data = {s: {key: {f'{g}|{i}': v for g in gs for i, v in per[s][g].items()}} for s in systems}
            sub = {k: [f'{g}|{i}' for g in gs for i in v if any(i in per[s][g] for s in systems)] for k, v in subsets.items()}
            grp = {f'{g}|{i}': g for g in gs for i in ids}
            r = S.summarize(data, sub, [(key, label, better, scale, fmt, True)], grp)
        else:
            r = S.summarize({s: {key: per[s][key]} for s in systems}, subsets, [(key, label, better, scale, fmt, False)])
        for s in systems: out['systems'][s].update(r['systems'][s])
        for p, v in r['paired'].items(): out['paired'].setdefault(p, {}).update(v)
    npg = lambda k: len(subsets[k])
    labels = {'full': f'all {npg("full")} pages', 'clean': f'{npg("clean")} pages never in the sample', 'sample': f'{npg("sample")} sample pages'}
    L = S.tables(out, M, ['full', 'clean', 'sample'], labels)
    cl, cost = S.cost_time(rows); L += cl + ['']
    L += ['Tests per group and type (passed / tests, all pages):', '', '| group | type | ' + ' | '.join(systems) + ' |', '|---|---|' + '---|' * len(systems)]
    for g, typ in sorted(types_[systems[0]]):
        L.append(f'| {g} | {typ} | ' + ' | '.join(f"{types_[s].get((g, typ), [0, 0])[0]} / {types_[s].get((g, typ), [0, 0])[1]}" for s in systems) + ' |')
    pages_cat = {g: sum(1 for c in cs if c['category'] == g) for g in CATS}
    L += ['', 'Pages per category: ' + ', '.join(f'{g} {n}' for g, n in pages_cat.items()) + '.']
    json.dump({'summary': out, 'cost': cost, 'per_page': {s: {g: per[s][g] for g in groups} for s in systems}, 'failures': fails,
               'category_of_page': cat, 'sample': sorted(samp)}, open(HERE + '/scores.json', 'w'))
    open(HERE + '/results.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    {'md': cmd_md, 'tests': cmd_tests, 'report': cmd_report}[sys.argv[1]]()
