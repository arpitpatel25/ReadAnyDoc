"""Scores the full FUNSD run (199 forms) exactly as the devset `funsd` component: the functions of e2e/devset/score.py
are used unchanged (ok, blocks, content, norm, funsd_links, kv_pairs, _key_ok, _val_ok), applied per form.
No model is called.

    cd product && uv run python ../e2e/full_bench/funsd/score.py     -> scores.json, results.md
"""
import collections, importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); FBD = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m


sys.argv = sys.argv[:1]
DEV = _load('devscore', os.path.dirname(FBD) + '/devset/score.py')
sys.path.insert(0, FBD)
import common as C, stats as S   # noqa: E402
RUNF = _load('fbrun', FBD + '/run.py')
C.STATE['bench'] = 'funsd'
R = C.R

MEASURES = [('link_recall', 'labelled question->answer links found as key/value pairs (%)', '+', 100, '%.1f', False),
            ('word_recall', 'labelled words present in the response (%)', '+', 100, '%.1f', False)]


def form(d, ann):
    """One form, same statements as devset score_funsd."""
    ok = DEV.ok(d)
    links = DEV.funsd_links(ann); pairs = DEV.kv_pairs(d) if ok else []
    kv_text = [' ' + DEV.norm(b.get('content')) + ' ' for b in DEV.blocks(d) if b.get('type') == 'Key Value'] if ok else []
    hit = [any(DEV._key_ok(q, k) and DEV._val_ok(a, v) for k, v in pairs) or any(f' {q} {a} ' in t for t in kv_text) for q, a in links]
    want = collections.Counter(w for e in ann['form'] for w in DEV.norm(e['text']).split())
    got = collections.Counter(DEV.norm(DEV.content(d)).split()) if ok else collections.Counter()
    wh = sum(min(n, got[w]) for w, n in want.items())
    return hit, links, wh, sum(want.values())


def main():
    cs = RUNF.funsd_cases(); ids = [c['id'] for c in cs]; samp = {c['id'] for c in cs if c['sample']}
    systems = [s for s in S.SYSTEMS if os.path.isdir(f'{HERE}/responses/{s}')]
    per = {s: {'link_recall': {}, 'word_recall': {}} for s in systems}; rows = {s: [] for s in systems}; cases = {}
    for c in cs:
        ann = json.load(open(R + c['annotation'])); cases[c['id']] = {'sample': c['sample'], 'part': c['part']}
        for s in systems:
            d = C.load(s, c); ok = DEV.ok(d)
            hit, links, wh, w = form(d, ann)
            per[s]['link_recall'][c['id']] = (sum(hit), len(links)); per[s]['word_recall'][c['id']] = (wh, w)
            usd = DEV.response_cost(s, c, d)[0] if ok else None
            rows[s].append((ok, usd, 1, d['_harness'].get('seconds') if ok else None, ok and not DEV.content(d).strip()))
            cases[c['id']][s] = {'links': len(links), 'found': sum(hit), 'words': w, 'words_found': wh, 'ok': ok,
                                 'missed': [list(l) for l, h in zip(links, hit) if not h][:40]}
    subsets = {'full': ids, 'clean': [i for i in ids if i not in samp], 'sample': [i for i in ids if i in samp]}
    summ = S.summarize(per, subsets, MEASURES)
    labels = {'full': f'all {len(ids)} forms', 'clean': f"{len(subsets['clean'])} forms never in the sample (FUNSD training part)", 'sample': f"{len(subsets['sample'])} sample forms (testing part)"}
    L = S.tables(summ, MEASURES, ['full', 'clean', 'sample'], labels)
    cl, cost = S.cost_time(rows); L += cl + ['']
    L.append(f"Links in the key: all {sum(v[1] for v in per[systems[0]]['link_recall'].values())}, clean "
             f"{sum(per[systems[0]]['link_recall'][i][1] for i in subsets['clean'])}, sample {sum(per[systems[0]]['link_recall'][i][1] for i in subsets['sample'])}.")
    json.dump({'summary': summ, 'cost': cost, 'cases': cases}, open(HERE + '/scores.json', 'w'), indent=1)
    open(HERE + '/results.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
