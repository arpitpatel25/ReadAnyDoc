"""Bootstrap intervals and report tables shared by the four full-benchmark scorers.

Every measure is a ratio of sums over items: value = sum(num_i) / sum(den_i) (a plain mean has den_i = 1; a pooled rate
such as "links found / links" has the counts). A composite measure is the mean of such ratios over groups of items
(olmOCR: mean of the category pass rates). Intervals: percentile bootstrap, 10,000 resamples of the ITEMS (pages, forms,
documents; within each group for a composite), seed 1. Paired comparisons resample the same items for both systems.
"""
import statistics as st
import numpy as np

B = 10000
SYSTEMS = ['reducto', 'ours', 'vanilla']
PAIRS = [('ours', 'reducto'), ('ours', 'vanilla'), ('vanilla', 'reducto')]


def _arr(data, ids):
    a = np.array([data.get(i, (0.0, 0.0)) for i in ids], dtype=float).reshape(-1, 2)
    return a[:, 0], a[:, 1]


def _idx(n, rng):
    return rng.integers(0, n, size=(B, n)) if n else np.zeros((B, 0), dtype=int)


def value(data, ids, groups=None):
    """Point value. data: {id: (num, den)}; groups: {id: group} for a mean of per-group ratios."""
    if groups is None:
        num, den = _arr(data, ids)
        return float(num.sum() / den.sum()) if den.sum() > 0 else None
    vals = [value(data, [i for i in ids if groups[i] == g]) for g in sorted({groups[i] for i in ids})]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _boot(datas, ids, groups, seed=1):
    """Bootstrap draws for several systems on the SAME resampled items -> list of arrays (one per system)."""
    rng = np.random.default_rng(seed)
    if groups is None:
        idx = _idx(len(ids), rng); out = []
        for d in datas:
            num, den = _arr(d, ids)
            with np.errstate(invalid='ignore', divide='ignore'):
                out.append(num[idx].sum(1) / den[idx].sum(1))
        return out
    acc = [[] for _ in datas]
    for g in sorted({groups[i] for i in ids}):
        sub = [i for i in ids if groups[i] == g]
        if all(_arr(d, sub)[1].sum() == 0 for d in datas): continue
        for k, a in enumerate(_boot(datas, sub, None, seed=rng.integers(1 << 30))): acc[k].append(a)
    return [np.nanmean(np.vstack(a), axis=0) if a else np.full(B, np.nan) for a in acc]


def ci(draws):
    d = draws[~np.isnan(draws)]
    return (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))) if len(d) else (None, None)


def summarize(per, ids_by_subset, measures, groups=None):
    """per: {system: {measure key: {id: (num, den)}}}. measures: [(key, label, better, scale, fmt, grouped)].
    Returns {'systems': {s: {key: {subset: {value, lo, hi, n}}}}, 'paired': {'a-b': {key: {subset: {diff, lo, hi}}}}}."""
    systems = [s for s in SYSTEMS if s in per]
    out = {'systems': {s: {} for s in systems}, 'paired': {}}
    for key, label, better, scale, fmt, grouped in measures:
        g = groups if grouped else None
        for sub, ids in ids_by_subset.items():
            if not ids: continue
            draws = dict(zip(systems, _boot([per[s].get(key, {}) for s in systems], ids, g)))
            for s in systems:
                v = value(per[s].get(key, {}), ids, g); lo, hi = ci(draws[s])
                n = sum(1 for i in ids if per[s].get(key, {}).get(i, (0, 0))[1] > 0)
                out['systems'][s].setdefault(key, {})[sub] = {'value': None if v is None else v * scale, 'lo': None if lo is None else lo * scale,
                                                               'hi': None if hi is None else hi * scale, 'n_items': n}
            for a, b in PAIRS:
                if a not in per or b not in per: continue
                va, vb = value(per[a].get(key, {}), ids, g), value(per[b].get(key, {}), ids, g)
                lo, hi = ci(draws[a] - draws[b])
                out['paired'].setdefault(f'{a}-{b}', {}).setdefault(key, {})[sub] = {
                    'diff': None if va is None or vb is None else (va - vb) * scale, 'lo': None if lo is None else lo * scale, 'hi': None if hi is None else hi * scale}
    return out


def _f(fmt, v):
    return '-' if v is None else fmt % v


def tables(summary, measures, subsets, subset_labels):
    """Markdown: one table per subset group for the systems, one for the paired comparisons."""
    L = []; systems = list(summary['systems'])
    L.append('| measure | better | subset | ' + ' | '.join(f'{s} (95% CI)' for s in systems) + ' |')
    L.append('|---|---|---|' + '---|' * len(systems))
    for key, label, better, scale, fmt, _ in measures:
        for sub in subsets:
            cells = []
            for s in systems:
                x = (summary['systems'][s].get(key) or {}).get(sub)
                cells.append('-' if not x or x['value'] is None else f"{_f(fmt, x['value'])} ({_f(fmt, x['lo'])} to {_f(fmt, x['hi'])})")
            L.append(f"| {label} | {'higher' if better == '+' else 'lower'} | {subset_labels[sub]} | " + ' | '.join(cells) + ' |')
    L.append('')
    pairs = [p for p in summary['paired']]
    if pairs:
        L.append('Paired differences (first minus second, same items resampled together; **bold** = the interval excludes 0):')
        L.append('')
        L.append('| measure | better | subset | ' + ' | '.join(p.replace('-', ' minus ') for p in pairs) + ' |')
        L.append('|---|---|---|' + '---|' * len(pairs))
        for key, label, better, scale, fmt, _ in measures:
            sfmt = fmt.replace('%', '%+')
            for sub in subsets:
                cells = []
                for p in pairs:
                    x = (summary['paired'][p].get(key) or {}).get(sub)
                    if not x or x['diff'] is None: cells.append('-'); continue
                    t = f"{_f(sfmt, x['diff'])} ({_f(sfmt, x['lo'])} to {_f(sfmt, x['hi'])})"
                    cells.append(f'**{t}**' if x['lo'] is not None and (x['lo'] > 0 or x['hi'] < 0) else t)
                L.append(f"| {label} | {'higher' if better == '+' else 'lower'} | {subset_labels[sub]} | " + ' | '.join(cells) + ' |')
        L.append('')
    return L


def cost_time(rows):
    """rows: {system: [(ok, usd or None, pages, seconds or None, empty)]} -> markdown lines + dict."""
    L = ['| system | responses | failed or missing | empty (no content) | USD per page | USD total | median wall s per page | mean s per page | p90 s per page |',
         '|---|---|---|---|---|---|---|---|---|']; out = {}
    for s in SYSTEMS:
        if s not in rows: continue
        r = rows[s]; good = [x for x in r if x[0]]
        usd = sum(x[1] for x in good if x[1] is not None); pages = sum(x[2] for x in good if x[1] is not None)
        sec = sorted(x[3] / max(x[2], 1) for x in good if x[3] is not None)
        o = {'responses': len(r), 'failed': sum(1 for x in r if not x[0]), 'empty': sum(1 for x in good if x[4]), 'usd_page': usd / pages if pages else None,
             'usd_total': usd, 'pages': sum(x[2] for x in good), 's_median': st.median(sec) if sec else None, 's_mean': sum(sec) / len(sec) if sec else None,
             's_p90': sec[int(0.9 * len(sec))] if sec else None}
        out[s] = o
        L.append(f"| {s} | {o['responses']} | {o['failed']} | {o['empty']} | {_f('%.4f', o['usd_page'])} | {_f('%.2f', o['usd_total'])} | {_f('%.1f', o['s_median'])} | "
                 f"{_f('%.1f', o['s_mean'])} | {_f('%.1f', o['s_p90'])} |")
    return L, out
