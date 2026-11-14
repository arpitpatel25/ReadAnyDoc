"""Scores the full OmniDocBench run (1,651 pages) as the devset `omni` component does, on all pages.

    cd product && OMNIDOCBENCH_TEDS_TIMEOUT_SEC=1500 uv run python ../e2e/full_bench/omnidocbench/score.py eval <system> [--procs 3]
    cd product && uv run python ../e2e/full_bench/omnidocbench/score.py report

eval    Prediction of a page = devset `omni_markdown` (block contents in response order, Figure blocks removed; a failed
        or missing response is an empty prediction). Official evaluation code, unmodified: e2e/devset/vendor/omnidocbench_eval
        (opendatalab/OmniDocBench, commit in UPSTREAM_COMMIT), end-to-end task, quick_match, run through the devset's
        e2e/devset/tools/omni_eval.py (upstream configs/end2end.yaml without CDM) in the helper environment
        ~/.cache/devset_omni_env. The devset ran the official code once per page; here it runs once per CHUNK of 20 pages
        (same code, same per-page and per-table outputs, about 20 times fewer process starts). A chunk is cached by the
        hash of its pages and predictions. TEDS timeout raised with OMNIDOCBENCH_TEDS_TIMEOUT_SEC=1500 (final_report_r2.md).
        Aggregation as the devset / upstream: edit distances = mean over pages of the page value (ALL_page_avg);
        TEDS = mean over tables (TEDS.all); table_teds_page = mean over pages of the page's mean TEDS (devset addition).
        Layout (devset addition `score_omni_layout`, called unchanged, one page at a time): type recall, lenient type
        recall, type precision, mean IoU, IoU >= 0.5 against OmniDocBench's own layout annotations.
report  Tables with bootstrap intervals over pages -> scores.json, results.md
"""
import collections, hashlib, json, os, subprocess, sys, types

HERE = os.path.dirname(os.path.abspath(__file__)); FBD = os.path.dirname(HERE); E2E = os.path.dirname(FBD)
WORK = os.path.expanduser('~/.cache/full_bench/omni_eval')
CHUNK = 20
GT_FILE = os.path.expanduser('~/.cache/devset_dl/omni/OmniDocBench.json')


def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m


ARGV = sys.argv[:]; sys.argv = sys.argv[:1]
DEV = _load('devscore', E2E + '/devset/score.py'); sys.path.insert(0, FBD)
import common as C, stats as S   # noqa: E402
RUNF = _load('fbrun', FBD + '/run.py'); C.STATE['bench'] = 'omnidocbench'
flag = lambda n, d: ARGV[ARGV.index(n) + 1] if n in ARGV else d


def layout_page(page_gt, case, d):
    """devset score_omni_layout, unchanged, on one page -> counts (it reports percentages; the counts are recovered
    from its per-label detail)."""
    real = (DEV.json, DEV.split_cases, DEV.RUN.load)
    DEV.json = types.SimpleNamespace(load=lambda f: (f.close(), [page_gt])[1], dumps=json.dumps, loads=json.loads)
    DEV.split_cases = lambda comp, split: [case]; DEV.RUN.load = lambda target, c: d
    try:
        det = {}; v = DEV.score_omni_layout('x', 'all', det)
    finally:
        DEV.json, DEV.split_cases, DEV.RUN.load = real
    pl = det['omni_layout']['per_label']; pp = det['omni_layout']['per_predicted_type']
    n = sum(x['blocks'] for x in pl.values())
    return {'n': n, 'hit': sum(round(x['recall'] * x['blocks'] / 100) for x in pl.values()),
            'hit_l': sum(round(x['recall_lenient'] * x['blocks'] / 100) for x in pl.values()),
            'iou': sum(x['mean_iou'] * x['blocks'] for x in pl.values()), 'iou50': round((v['omnilay.iou50'] or 0) * n / 100),
            'np': sum(x['blocks'] for x in pp.values()), 'pok': sum(round(x['precision'] * x['blocks'] / 100) for x in pp.values()),
            'labels': {k: [x['blocks'], round(x['recall'] * x['blocks'] / 100), x['mean_iou'] * x['blocks']] for k, x in pl.items()},
            'pred_types': {k: [x['blocks'], round(x['precision'] * x['blocks'] / 100)] for k, x in pp.items()}}


def run_chunk(job):
    k, names, gts, mds, system = job
    h = hashlib.sha1(('\x00'.join(n + '\x01' + m for n, m in zip(names, mds))).encode()).hexdigest()[:12]
    work = f'{WORK}/{system}/c{k:03d}__{h}'; res = work + '/result/pred_quick_match_'
    if not os.path.exists(res + 'metric_result.json'):
        os.makedirs(work + '/pred', exist_ok=True)
        json.dump(gts, open(work + '/gt.json', 'w'), ensure_ascii=False)
        for n, m in zip(names, mds): open(f'{work}/pred/{os.path.splitext(n)[0]}.md', 'w').write(m)
        r = subprocess.run([DEV.HELPER_PY, E2E + '/devset/tools/omni_eval.py', work + '/gt.json', work + '/pred', work], capture_output=True, text=True)
        open(work + '/eval.log', 'w').write(r.stdout[-20000:] + '\n--- stderr ---\n' + r.stderr[-20000:])
        if not os.path.exists(res + 'metric_result.json'):
            return k, None, work
    out = {n: {} for n in names}
    for elem in ('text_block', 'table', 'reading_order', 'display_formula'):
        f = f'{res}{elem}_per_page_edit.json'
        for n, v in (json.load(open(f)).items() if os.path.exists(f) else []):
            if isinstance(v, (int, float)) and v == v and n in out: out[n][elem] = v
    f = res + 'table_result.json'
    for smp in (json.load(open(f)) if os.path.exists(f) else []):
        m = smp.get('metric') or {}; n = smp.get('image_name') or smp.get('img_id')
        if n in out and isinstance(m.get('TEDS'), (int, float)):
            out[n].setdefault('teds', []).append(m['TEDS']); out[n].setdefault('teds_s', []).append(m.get('TEDS_structure_only'))
    return k, out, work


def cmd_eval(system):
    from concurrent.futures import ThreadPoolExecutor
    cs = sorted(RUNF.omni_cases(), key=lambda c: c['image'])
    gt = {p['page_info']['image_path']: p for p in json.load(open(GT_FILE))}
    pages = {}; mds = []
    for c in cs:
        d = C.load(system, c); ok = DEV.ok(d); md = DEV.omni_markdown(d) if ok else ''
        mds.append(md)
        pages[c['image']] = {'id': c['id'], 'ok': ok, 'missing': d is None, 'usd': DEV.response_cost(system, c, d)[0] if ok else None,
                             'seconds': d['_harness'].get('seconds') if ok else None, 'empty': ok and not DEV.content(d).strip(),
                             'layout': layout_page(gt[c['image']], c, d), 'md_chars': len(md)}
    jobs = [(k // CHUNK, [c['image'] for c in cs[k:k + CHUNK]], [gt[c['image']] for c in cs[k:k + CHUNK]], mds[k:k + CHUNK], system) for k in range(0, len(cs), CHUNK)]
    bad = []
    with ThreadPoolExecutor(int(flag('--procs', 3))) as ex:
        for n, (k, out, work) in enumerate(ex.map(run_chunk, jobs), 1):
            if out is None: bad.append(work); print('CHUNK FAILED', work, flush=True); continue
            for name, v in out.items(): pages[name]['official'] = v
            if n % 5 == 0: print(f'{system}: {n}/{len(jobs)} chunks', flush=True)
    json.dump(pages, open(f'{HERE}/eval_{system}.json', 'w'), ensure_ascii=False)
    print(system, 'done', len(pages), 'pages,', len(bad), 'failed chunks')


MEASURES = [('text_edit', 'text edit distance (0-1)', '-', 1, '%.3f', False),
            ('table_teds', 'table TEDS, mean over tables (%)', '+', 100, '%.1f', False),
            ('table_teds_struct', 'table TEDS structure only, mean over tables (%)', '+', 100, '%.1f', False),
            ('table_teds_page', 'table TEDS, mean over pages with tables (%)', '+', 100, '%.1f', False),
            ('table_edit', 'table edit distance (0-1)', '-', 1, '%.3f', False),
            ('order_edit', 'reading-order edit distance (0-1)', '-', 1, '%.3f', False),
            ('formula_edit', 'display-formula edit distance (0-1)', '-', 1, '%.3f', False),
            ('lay_type_recall', 'layout: labelled blocks whose best-overlapping block has the mapped type (%)', '+', 100, '%.1f', False),
            ('lay_type_recall_lenient', 'layout: same, lenient type sets (%)', '+', 100, '%.1f', False),
            ('lay_type_precision', 'layout: predicted blocks whose best-overlapping label accepts their type (%)', '+', 100, '%.1f', False),
            ('lay_mean_iou', 'layout: mean box IoU of labelled block and best prediction (0-1)', '+', 1, '%.3f', False),
            ('lay_iou50', 'layout: labelled blocks with a prediction at IoU >= 0.5 (%)', '+', 100, '%.1f', False)]


def page_measures(p):
    o = p.get('official') or {}; ly = p['layout']; m = {}
    for key, elem in (('text_edit', 'text_block'), ('table_edit', 'table'), ('order_edit', 'reading_order'), ('formula_edit', 'display_formula')):
        if elem in o: m[key] = (o[elem], 1)
    t = o.get('teds') or []
    if t:
        m['table_teds'] = (sum(t), len(t)); ts = [x for x in o.get('teds_s') or [] if isinstance(x, (int, float))]
        if ts: m['table_teds_struct'] = (sum(ts), len(ts))
        m['table_teds_page'] = (sum(t) / len(t), 1)
    if ly['n']:
        m['lay_type_recall'] = (ly['hit'], ly['n']); m['lay_type_recall_lenient'] = (ly['hit_l'], ly['n'])
        m['lay_mean_iou'] = (ly['iou'], ly['n']); m['lay_iou50'] = (ly['iou50'], ly['n'])
    if ly['np']: m['lay_type_precision'] = (ly['pok'], ly['np'])
    return m


def cmd_report():
    cs = RUNF.omni_cases(); ids = [c['id'] for c in cs]; samp = {c['id'] for c in cs if c['sample']}; byimg = {c['image']: c for c in cs}
    systems = [s for s in S.SYSTEMS if os.path.exists(f'{HERE}/eval_{s}.json')]
    per = {s: collections.defaultdict(dict) for s in systems}; rows = {s: [] for s in systems}; pages = {}
    for s in systems:
        ev = json.load(open(f'{HERE}/eval_{s}.json'))
        for img, p in ev.items():
            for k, v in page_measures(p).items(): per[s][k][p['id']] = v
            rows[s].append((p['ok'], p['usd'], 1, p['seconds'], p['empty']))
            pages.setdefault(p['id'], {'data_source': byimg[img]['data_source'], 'language': byimg[img]['language'], 'sample': p['id'] in samp})[s] = {
                'ok': p['ok'], 'md_chars': p['md_chars'], **{k: round(v[0] / v[1], 4) for k, v in page_measures(p).items()}}
    allok = [i for i in ids if all(pages[i][s]['ok'] for s in systems)]
    subsets = {'full': ids, 'clean': [i for i in ids if i not in samp], 'sample': [i for i in ids if i in samp], 'allok': allok}
    summ = S.summarize(per, subsets, MEASURES)
    labels = {'full': f'all {len(ids)} pages', 'clean': f"{len(subsets['clean'])} pages never in the sample", 'sample': f"{len(subsets['sample'])} sample pages",
              'allok': f'{len(allok)} pages every system answered'}
    L = S.tables(summ, MEASURES, ['full', 'clean', 'sample', 'allok'], labels)
    cl, cost = S.cost_time(rows); L += cl + ['']
    n_items = {k: {s: summ['systems'][s][k]['full']['n_items'] for s in systems} for k, *_ in MEASURES}
    L.append('Pages (or pages with tables / formulas) that carry each measure, all pages: ' + '; '.join(f"{k} {v[systems[0]]}" for k, v in n_items.items()) + '.')
    L.append('Tables scored (TEDS), all pages: ' + ', '.join(f"{s} {int(sum(v[1] for v in per[s]['table_teds'].values()))}" for s in systems) + '.')
    L += ['', 'Text edit distance by document type (all pages; lower is better):', '', '| document type | pages | ' + ' | '.join(systems) + ' |', '|---|---|' + '---|' * len(systems)]
    for attr in ('data_source', 'language'):
        if attr == 'language': L += ['', 'Text edit distance by language:', '', '| language | pages | ' + ' | '.join(systems) + ' |', '|---|---|' + '---|' * len(systems)]
        for g in sorted({c[attr] or '-' for c in cs}):
            sel = [c['id'] for c in cs if (c[attr] or '-') == g]
            L.append(f'| {g} | {len(sel)} | ' + ' | '.join('-' if S.value(per[s]['text_edit'], sel) is None else '%.3f' % S.value(per[s]['text_edit'], sel) for s in systems) + ' |')
    json.dump({'summary': summ, 'cost': cost, 'pages': pages}, open(HERE + '/scores.json', 'w'), ensure_ascii=False)
    open(HERE + '/results.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    if ARGV[1] == 'eval': cmd_eval(ARGV[2])
    else: cmd_report()
