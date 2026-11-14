"""Scores the LongExtract-50 documents that were run (whole documents, shortest first) with the benchmark's own
deterministic grader: benchmarks/longextract-bench/src/longextract_bench/grading.py `grade(pred, gt, schema)`
(micro1-research/longextract-bench; standard library only, unmodified). No model is called.

    cd product && uv run python ../e2e/full_bench/longextract/score.py     -> scores.json, results.md

Prediction = the response's result object (`e2e/compare_tasks.py result_obj`, as the devset), passed through the
grader's own `unwrap` and `_collapse` (what its `_load_doc` does for Reducto envelopes). The grader returns, per document:
row recall and row precision over the schema's top-level arrays (rows paired on a key it infers from the answer key),
and leaf accuracy over the values of matched rows plus document-level fields.
The devset scored its 3 documents with `compare_tasks.score_key`, which needs hand-picked key fields per document
(they exist only for those 3); the official grader needs none, so it is used for all documents here.
A failed, missing or non-object response counts as recall 0, precision 0, leaf accuracy 0 in the "all documents run"
numbers; "completed only" numbers (the benchmark's own convention) are given next to the completion count.
"""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); FBD = os.path.dirname(HERE); E2E = os.path.dirname(FBD); ROOT = os.path.dirname(E2E)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m


sys.argv = sys.argv[:1]
DEV = _load('devscore', E2E + '/devset/score.py'); sys.path.insert(0, FBD)
import common as C, stats as S   # noqa: E402
import compare_tasks as CT        # noqa: E402
RUNF = _load('fbrun', FBD + '/run.py'); C.STATE['bench'] = 'longextract'
G = _load('lx_grading', ROOT + '/benchmarks/longextract-bench/src/longextract_bench/grading.py')
R = C.R

MEASURES = [('row_recall', 'row recall, mean over documents (%)', '+', 100, '%.1f', False),
            ('row_precision', 'row precision, mean over documents (%)', '+', 100, '%.1f', False),
            ('leaf_accuracy', 'leaf accuracy on matched rows and document fields, mean over documents (%)', '+', 100, '%.1f', False),
            ('row_f1', 'row F1 (from the document recall and precision), mean over documents (%)', '+', 100, '%.1f', False),
            ('rows_pooled', 'rows of the answer keys returned and matched, pooled over documents (%)', '+', 100, '%.1f', False)]


def main():
    cs = RUNF.lx_cases(); ids = [c['id'] for c in cs]; samp = {c['id'] for c in cs if c['sample']}
    systems = [s for s in S.SYSTEMS if os.path.isdir(f'{HERE}/responses/{s}')]
    per = {s: {k: {} for k, *_ in MEASURES} for s in systems}; rows = {s: [] for s in systems}; docs = {}
    for c in cs:
        gt = G._load_gt(__import__('pathlib').Path(R + c['gt'])); schema = json.load(open(R + c['schema']))
        docs[c['id']] = {'slug': c['slug'], 'pages': c['pages'], 'gt_rows': c['gt_rows'], 'sample': c['sample'], 'sample_note': c.get('sample_note')}
        for s in systems:
            d = C.load(s, c); ok = DEV.ok(d); pred = G._collapse(G.unwrap(CT.result_obj(d))) if ok else None
            done = isinstance(pred, dict)
            g = G.grade(pred, gt, schema) if done else {'gt_rows': G.grade({}, gt, schema)['gt_rows'], 'pred_rows': 0, 'matched': 0, 'precision': 0.0, 'recall': 0.0, 'leaf_accuracy': 0.0}
            pr, rc = g['precision'], g['recall']; f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
            i = c['id']
            per[s]['row_recall'][i] = (rc, 1); per[s]['row_precision'][i] = (pr, 1); per[s]['leaf_accuracy'][i] = (g['leaf_accuracy'] / 100, 1)
            per[s]['row_f1'][i] = (f1, 1); per[s]['rows_pooled'][i] = (g['matched'], g['gt_rows'])
            usd, pages = DEV.response_cost(s, c, d) if ok else (None, c['pages'])
            sec = (d or {}).get('_harness', {}).get('seconds')
            if s == 'reducto' or c['id'] in {x['id'] for x in cs[:RUNF.LX_ANTHROPIC_DOCS]}:
                rows[s].append((ok and done, usd, c['pages'], sec if ok else None, ok and done and g['pred_rows'] == 0))
            err = None
            if not (ok and done):
                err = 'no response saved' if d is None else json.dumps({k: v for k, v in d.items() if k not in ('result', '_harness')})[:300] + f" status {d.get('_harness', {}).get('status')}"
            docs[i][s] = {'completed': bool(ok and done), 'gt_rows': g['gt_rows'], 'pred_rows': g['pred_rows'], 'matched': g['matched'], 'recall': round(rc, 4),
                          'precision': round(pr, 4), 'leaf_accuracy': round(g['leaf_accuracy'], 2), 'usd': usd, 'seconds': sec, 'error': err,
                          'pages_reported': ((d or {}).get('usage') or {}).get('num_pages')}
    com = ids[:RUNF.LX_ANTHROPIC_DOCS]                      # documents every system was asked for
    subsets = {'full': com, 'clean': [i for i in com if i not in samp], 'sample': [i for i in com if i in samp], 'r_all': ids,
               'r_clean': [i for i in ids if i not in samp]}
    summ = S.summarize(per, subsets, MEASURES)
    for s in systems:                                        # only Reducto was run beyond the common documents
        if s != 'reducto':
            for k in summ['systems'][s]: summ['systems'][s][k].pop('r_all', None); summ['systems'][s][k].pop('r_clean', None)
    for p in summ['paired'].values():
        for k in p: p[k].pop('r_all', None); p[k].pop('r_clean', None)
    labels = {'full': f'the {len(com)} shortest documents (run on every system)', 'clean': f"of those, {len(subsets['clean'])} never used before",
              'sample': f"of those, {len(subsets['sample'])} used before (first 8 of 33 pages)", 'r_all': f'all {len(ids)} documents run on Reducto',
              'r_clean': f"of those, {len(subsets['r_clean'])} never used before"}
    L = S.tables(summ, MEASURES, ['full', 'clean', 'sample', 'r_all', 'r_clean'], labels)
    L += ['Completed documents only (the benchmark\'s own convention):', '', '| system | completed | mean row recall | mean row precision | mean leaf accuracy |', '|---|---|---|---|---|']
    for s in systems:
        asked = ids if s == 'reducto' else com
        ok = [docs[i][s] for i in asked if docs[i][s]['completed']]; m = lambda k: sum(x[k] for x in ok) / len(ok) if ok else float('nan')
        L.append(f"| {s} | {len(ok)} of {len(asked)} | {100 * m('recall'):.1f} | {100 * m('precision'):.1f} | {m('leaf_accuracy'):.1f} |")
    L.append('')
    cl, cost = S.cost_time(rows); L += cl + ['']
    L += ['Per document (recall / precision / leaf accuracy, %; "-" = no usable response):', '',
          '| document | pages | key rows | ' + ' | '.join(f'{s} R / P / leaf | {s} rows | {s} s | {s} $' for s in systems) + ' |', '|---|---|---|' + '---|---|---|---|' * len(systems)]
    for c in cs:
        x = docs[c['id']]; cells = []
        for s in systems:
            y = x[s]
            cells += [f"{100 * y['recall']:.1f} / {100 * y['precision']:.1f} / {y['leaf_accuracy']:.1f}" if y['completed'] else '-', str(y['pred_rows']),
                      '-' if y['seconds'] is None else f"{y['seconds']:.0f}", '-' if y['usd'] is None else f"{y['usd']:.3f}"] if (y['completed'] or y['error'] != 'no response saved') else ['not run', '', '', '']
        L.append(f"| {c['slug'][:44]}{' (used before: ' + c['sample_note'] + ')' if c.get('sample_note') else ''} | {c['pages']} | {x[systems[0]]['gt_rows']} | " + ' | '.join(cells) + ' |')
    json.dump({'summary': summ, 'cost': cost, 'docs': docs}, open(HERE + '/scores.json', 'w'), indent=1)
    open(HERE + '/results.md', 'w').write('\n'.join(L) + '\n'); print('\n'.join(L))


if __name__ == '__main__':
    main()
