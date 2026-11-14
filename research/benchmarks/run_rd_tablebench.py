"""Full RD-TableBench (1,000 tables) through the real HTTP servers, resumable.

    cd product && ENGINE_TAG=final-round-3 uv run python ../e2e/rdtb_full/run.py <system>[,<system>] [--limit N] [--workers N]

systems: reducto (live API; the 100 devset responses are copied from the cache and never requested again),
         ours (http://127.0.0.1:8881), vanilla (http://127.0.0.1:8882).
Responses: e2e/rdtb_full/responses/<system>/<case id>.json, saved by e2e/harness.py run_one (same request as the devset).
A case with a saved good response (status 200 and a result) is skipped. Failed cases are retried once at the end.
Budgets: Anthropic $9 in total (stops at $8.60 measured from token usage), Reducto at most 900 new requests.
"""
import csv, json, os, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.dirname(HERE) + '/devset')
import harness as H            # noqa: E402

R = H.R
URLS = {'ours': 'http://127.0.0.1:8881', 'vanilla': 'http://127.0.0.1:8882'}
PRICES = {'haiku': (0.10, 0.50), 'sonnet': (2.0, 10.0), 'task': (0.10, 0.50)}
ANTHROPIC_STOP = 8.60
LOCK = threading.Lock()
STATE = {'spend': 0.0, 'stop': False, 'pause_until': 0.0, 'reducto_new': 0}


def out_path(target, case):
    return f"{HERE}/responses/{target}/{case['id']}.json"


H.out_path = out_path
for k, u in URLS.items():
    H.TARGETS[k] = (u, None)


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    print(line, flush=True)
    with LOCK:
        open(HERE + '/progress.log', 'a').write(line + '\n')


def all_cases():
    sample = {json.loads(l)['id'] for l in open(R + 'e2e/devset/cases/tables.jsonl') if l.strip()}
    old = {}
    for row in csv.DictReader(open(R + 'data/rd-tablebench/providers/scores.csv')):
        old[row['pdf_path'][:-4]] = float(row['reducto_score']) if row['reducto_score'] else None
    cs = []
    for f in sorted(os.listdir(R + 'data/rd-tablebench/_images')):
        if not f.endswith('.jpg'): continue
        stem = f[:-4]
        cs.append({'id': 'tb_' + stem, 'file': 'data/rd-tablebench/_images/' + f, 'endpoint': 'parse', 'body': {},
                   'gt': f'data/rd-tablebench/groundtruth/{stem}.html', 'reducto_2024_score': old.get(stem),
                   'sample': ('tb_' + stem) in sample})
    return cs


def good(d):
    return bool(d) and d.get('_harness', {}).get('status') == 200 and isinstance(d.get('result'), (dict, list))


def cost(d):
    usd = 0.0
    for name, m in (((d.get('usage') or {}).get('model_usage') or {}).get('models') or {}).items():
        fam = 'task' if name.startswith('task:') else next((f for f in ('haiku', 'sonnet') if f in name.lower()), 'sonnet')
        usd += (m.get('input_tokens', 0) * PRICES[fam][0] + m.get('output_tokens', 0) * PRICES[fam][1]) / 1e6
    return usd


def load(target, c):
    p = out_path(target, c)
    try:
        return json.load(open(p)) if os.path.exists(p) else None
    except Exception:
        return None


def throttled(d):
    s = json.dumps({k: v for k, v in (d or {}).items() if k != 'result'})[:3000].lower()
    return any(w in s for w in ('rate limit', 'rate_limit', '429', 'overloaded', '529', 'too many requests'))


def one(target, c):
    if STATE['stop']: return 'skipped'
    d = load(target, c)
    if good(d): return 'cached'
    if target == 'reducto':
        with LOCK:
            if STATE['reducto_new'] >= 995:          # 900 new cases + one retry of failures can never pass $10
                STATE['stop'] = True; return 'skipped'
            STATE['reducto_new'] += 1
    for attempt in range(4):                          # throttling: wait and send again (not counted as the retry)
        w = STATE['pause_until'] - time.time()
        if w > 0: time.sleep(w)
        cid, st, sec = H.run_one(target, c)
        d = load(target, c)
        if st == 200 and good(d): break
        if isinstance(st, int) and throttled(d) and target != 'reducto':
            with LOCK: STATE['pause_until'] = time.time() + 30 * (attempt + 1)
            log(f'{target} {cid} throttled (status {st}), pausing {30 * (attempt + 1)}s')
            continue
        break
    if target != 'reducto' and d and st != 'cached':
        with LOCK:
            STATE['spend'] += cost(d)
            if STATE['spend'] > ANTHROPIC_STOP and not STATE['stop']:
                STATE['stop'] = True
        if STATE['stop']: log(f'ANTHROPIC BUDGET STOP at ${STATE["spend"]:.3f}')
    return 'ok' if st == 200 and good(d) else f'FAIL {st}'


def run(target, cs, workers):
    todo = [c for c in cs if not good(load(target, c))]
    log(f'{target}: {len(cs)} cases, {len(cs) - len(todo)} already saved, {len(todo)} to request, {workers} at once')
    for rnd, w in (('pass 1', workers), ('retry', max(2, workers // 4))):
        if not todo or STATE['stop']: break
        t0 = time.time(); n = 0; bad = []
        with ThreadPoolExecutor(w) as ex:
            for c, r in zip(todo, ex.map(lambda c: one(target, c), todo)):
                n += 1
                if r not in ('ok', 'cached'):
                    bad.append(c)
                    if r != 'skipped': log(f'{target} {c["id"]} {r}')
                if n % 50 == 0:
                    log(f'{target} {rnd}: {n}/{len(todo)} done, {len(bad)} failed, {time.time() - t0:.0f}s, anthropic spend so far ${STATE["spend"]:.3f}')
        log(f'{target} {rnd} finished: {len(todo)} requested, {len(bad)} failed, wall {time.time() - t0:.0f}s')
        todo = bad
    log(f'{target}: done, {len(todo)} cases without a good response')


if __name__ == '__main__':
    systems = sys.argv[1].split(',')
    flag = lambda n, d: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
    cs = all_cases(); limit = int(flag('--limit', 0))
    assert len(cs) == 1000 and sum(c['sample'] for c in cs) == 100, (len(cs), sum(c['sample'] for c in cs))
    if 'reducto' in systems:                           # the 100 cached responses: copied, never requested
        for c in cs:
            if c['sample'] and not os.path.exists(out_path('reducto', c)):
                src = next(p for p in (f"{R}e2e/responses/reducto/{c['id']}.json", f"{R}e2e/devset/responses/reducto/{c['id']}.json") if os.path.exists(p))
                os.makedirs(os.path.dirname(out_path('reducto', c)), exist_ok=True); shutil.copy(src, out_path('reducto', c))
        missing = [c['id'] for c in cs if c['sample'] and not good(load('reducto', c))]
        assert not missing, missing
    if limit:
        cs = [c for c in cs if not c['sample']][:limit]
    for t in ('ours', 'vanilla'):                      # spend already on disk (resumed run)
        for c in all_cases():
            d = load(t, c)
            if d: STATE['spend'] += cost(d)
    log(f'start {systems}, anthropic spend on disk ${STATE["spend"]:.3f}')
    ths = [threading.Thread(target=run, args=(t, cs, int(flag('--workers', 6 if t == 'reducto' else 16)))) for t in systems]
    [t.start() for t in ths]; [t.join() for t in ths]
    log(f'end {systems}, anthropic spend measured ${STATE["spend"]:.3f}, stop={STATE["stop"]}')
