"""Scores the full RD-TableBench run exactly as the devset `tables` component (e2e/devset/score.py): the largest table
of each response against the ground truth with official/grading.py (through devset/tools/table_grade.py in the helper
environment); a response without a table, or a failed request, scores 0. No model is called.

    cd product && uv run python ../e2e/rdtb_full/score.py        -> e2e/rdtb_full/scores.json and results.md
"""
import importlib.util, json, os, random, statistics as st, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m


sys.argv = sys.argv[:1]
DEV = _load('devscore', os.path.dirname(HERE) + '/devset/score.py')   # ok, largest_table, grade_tables: used unchanged
FULL = _load('fullrun', HERE + '/run.py')
DEV.SCORES = HERE + '/grader_cache'                                    # grader cache of this run, not the devset's
R = FULL.R
SYSTEMS = ['reducto', 'ours', 'vanilla']
B = 10000


def boot(xs, seed=1):
    rnd = random.Random(seed); n = len(xs); ms = []
    for _ in range(B):
        ms.append(sum(xs[rnd.randrange(n)] for _ in range(n)) / n)
    ms.sort(); return ms[int(0.025 * B)], ms[int(0.975 * B) - 1]


def main():
    cs = FULL.all_cases(); out = {'cases': {}, 'systems': {}}
    gts = {c['id']: open(R + c['gt']).read() for c in cs}
    per = {s: {} for s in SYSTEMS}; info = {s: {} for s in SYSTEMS}
    for s in SYSTEMS:
        pairs = []; meta = []
        for c in cs:
            d = FULL.load(s, c); good = DEV.ok(d); t = DEV.largest_table(d) if good else None
            pairs.append((gts[c['id']], t or ''))
            usd = (DEV.response_cost(s, c, d)[0] if good else None)
            meta.append({'failed': not good, 'missing': d is None, 'no_table': good and t is None, 'usd': usd,
                         'seconds': d['_harness'].get('seconds') if good else None,
                         'n_tables': len(DEV.all_tables(d)) if good else 0})
        sc = DEV.grade_tables(pairs)
        sc = [0.0 if not q else x for x, (_, q) in zip(sc, pairs)]
        for c, x, m in zip(cs, sc, meta):
            per[s][c['id']] = x; info[s][c['id']] = m
    ids = [c['id'] for c in cs]; samp = {c['id'] for c in cs if c['sample']}
    clean = [i for i in ids if i not in samp]; dev = [i for i in ids if i in samp]
    old = {c['id']: c['reducto_2024_score'] for c in cs}
    mean = lambda xs: sum(xs) / len(xs)
    for s in SYSTEMS:
        v = per[s]; m = info[s]; lo, hi = boot([v[i] for i in ids]); lo9, hi9 = boot([v[i] for i in clean])
        usd = [m[i]['usd'] for i in ids if m[i]['usd'] is not None]; sec = [m[i]['seconds'] for i in ids if m[i]['seconds'] is not None]
        out['systems'][s] = {
            'mean_all': mean([v[i] for i in ids]), 'ci_all': [lo, hi], 'mean_clean900': mean([v[i] for i in clean]), 'ci_clean900': [lo9, hi9],
            'mean_sample100': mean([v[i] for i in dev]), 'no_table': sum(m[i]['no_table'] for i in ids),
            'failed': sum(m[i]['failed'] for i in ids), 'missing': sum(m[i]['missing'] for i in ids),
            'usd_per_table': mean(usd) if usd else None, 'usd_total': sum(usd), 'seconds_median': st.median(sec) if sec else None,
            'seconds_mean': mean(sec) if sec else None, 'seconds_p90': sorted(sec)[int(0.9 * len(sec))] if sec else None,
            'mean_all_excl_failed': mean([v[i] for i in ids if not m[i]['failed']]) if any(not m[i]['failed'] for i in ids) else None}
    o = [old[i] for i in ids if old[i] is not None]
    out['reducto_2024_saved'] = {'mean_all': mean(o), 'n': len(o), 'mean_clean900': mean([old[i] for i in clean if old[i] is not None]),
                                 'mean_sample100': mean([old[i] for i in dev if old[i] is not None])}
    out['paired'] = {}
    for a, b in (('ours', 'reducto'), ('ours', 'vanilla'), ('vanilla', 'reducto')):
        for name, sel in (('all', ids), ('clean900', clean), ('sample100', dev)):
            df = [per[a][i] - per[b][i] for i in sel]; lo, hi = boot(df)
            out['paired'][f'{a}-{b}/{name}'] = {'mean_diff': mean(df), 'ci': [lo, hi], 'a_ahead_005': sum(x > 0.05 for x in df),
                                                'b_ahead_005': sum(x < -0.05 for x in df), 'within_005': sum(abs(x) <= 0.05 for x in df), 'n': len(df)}
    for i in ids:
        out['cases'][i] = {'sample': i in samp, 'reducto_2024': old[i], **{s: round(per[s][i], 4) for s in SYSTEMS},
                           **{f'{s}_info': info[s][i] for s in SYSTEMS}}
    json.dump(out, open(HERE + '/scores.json', 'w'), indent=1)
    S = out['systems']; L = []
    L.append('| system | mean, all 1,000 (95% CI) | mean, 900 never in the sample (95% CI) | mean, 100 sample tables | no table | failed | $ / table | median s / table |')
    L.append('|---|---|---|---|---|---|---|---|')
    f = lambda x: f'{100 * x:.2f}'
    for s in SYSTEMS:
        x = S[s]
        L.append(f"| {s} | {f(x['mean_all'])} ({f(x['ci_all'][0])} to {f(x['ci_all'][1])}) | {f(x['mean_clean900'])} ({f(x['ci_clean900'][0])} to {f(x['ci_clean900'][1])}) | "
                 f"{f(x['mean_sample100'])} | {x['no_table']} | {x['failed']} | {x['usd_per_table']:.4f} | {x['seconds_median']:.1f} |")
    x = out['reducto_2024_saved']
    L.append(f"| reducto, saved 2024 scores (scores.csv) | {f(x['mean_all'])} | {f(x['mean_clean900'])} | {f(x['mean_sample100'])} | - | - | - | - |")
    L.append('')
    L.append('| paired comparison | tables | mean difference, points (95% CI) | first ahead by > 0.05 | second ahead by > 0.05 | within 0.05 |')
    L.append('|---|---|---|---|---|---|')
    for k, p in out['paired'].items():
        L.append(f"| {k.split('/')[0].replace('-', ' minus ')} | {k.split('/')[1]} | {100 * p['mean_diff']:+.2f} ({100 * p['ci'][0]:+.2f} to {100 * p['ci'][1]:+.2f}) | {p['a_ahead_005']} | {p['b_ahead_005']} | {p['within_005']} |")
    L.append('')
    rk = sorted(ids, key=lambda i: per['ours'][i] - per['reducto'][i])
    for title, sel in (('15 tables where ours loses most to r-1', rk[:15]), ('15 tables where ours wins most over r-1', rk[::-1][:15])):
        L += [f'**{title}**', '', '| id | ours | reducto | vanilla | difference | in sample |', '|---|---|---|---|---|---|']
        for i in sel:
            L.append(f"| {i[3:]} | {per['ours'][i]:.3f} | {per['reducto'][i]:.3f} | {per['vanilla'][i]:.3f} | {per['ours'][i] - per['reducto'][i]:+.3f} | {'yes' if i in samp else 'no'} |")
        L.append('')
    open(HERE + '/results.md', 'w').write('\n'.join(L)); print('\n'.join(L))


if __name__ == '__main__':
    main()
