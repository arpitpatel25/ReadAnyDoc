"""Check: the chunked official evaluation gives the same per-page values as the devset's page-by-page evaluation
(Reducto, the 100 sample pages; devset values from e2e/devset/scores/reducto.json)."""
import importlib.util, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('omniscore', HERE + '/score.py'); SC = importlib.util.module_from_spec(spec); spec.loader.exec_module(SC)
SC.WORK = os.path.expanduser('~/.cache/full_bench/omni_eval_validate')
cs = sorted([c for c in SC.RUNF.omni_cases() if c['sample']], key=lambda c: c['image'])
gt = {p['page_info']['image_path']: p for p in json.load(open(SC.GT_FILE))}
from concurrent.futures import ThreadPoolExecutor
mds = [SC.DEV.omni_markdown(SC.C.load('reducto', c)) for c in cs]
jobs = [(k // 20, [c['image'] for c in cs[k:k + 20]], [gt[c['image']] for c in cs[k:k + 20]], mds[k:k + 20], 'reducto') for k in range(0, len(cs), 20)]
got = {}
with ThreadPoolExecutor(3) as ex:
    for k, out, work in ex.map(SC.run_chunk, jobs):
        assert out is not None, work
        got.update(out)
dev = json.load(open(HERE + '/../../devset/scores/reducto.json'))['detail']['all']['omni']['per_page']
n = bad = 0; worst = []
for c in cs:
    d = dev[c['id']]; g = got[c['image']]
    for k in ('text_block', 'table', 'reading_order', 'display_formula'):
        if k in d or k in g:
            n += 1; a, b = d.get(k), g.get(k)
            if a is None or b is None or abs(a - b) > 1e-3: bad += 1; worst.append((c['id'], k, a, b))
    if 'table_teds' in d or g.get('teds'):
        n += 1; a = d.get('table_teds'); b = sum(g['teds']) / len(g['teds']) if g.get('teds') else None
        if a is None or b is None or abs(a - b) > 1e-3: bad += 1; worst.append((c['id'], 'teds', a, b))
print('values compared', n, 'different', bad); print(worst[:15])
lay = [SC.layout_page(gt[c['image']], c, SC.C.load('reducto', c)) for c in cs]
N = sum(x['n'] for x in lay); v = json.load(open(HERE + '/../../devset/scores/reducto.json'))['splits']['all']
print('layout chunk-free check: type_recall', 100 * sum(x['hit'] for x in lay) / N, 'devset', v.get('omnilay.type_recall'), '| lenient', 100 * sum(x['hit_l'] for x in lay) / N, v.get('omnilay.type_recall_lenient'),
      '| precision', 100 * sum(x['pok'] for x in lay) / sum(x['np'] for x in lay), v.get('omnilay.type_precision'), '| iou', sum(x['iou'] for x in lay) / N, v.get('omnilay.mean_iou'),
      '| iou50', 100 * sum(x['iou50'] for x in lay) / N, v.get('omnilay.iou50'))
