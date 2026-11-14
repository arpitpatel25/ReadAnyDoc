"""Shared runner for the four full public benchmarks (pattern: e2e/rdtb_full/run.py).

    cd product && ENGINE_TAG=<tag> uv run python ../e2e/full_bench/run.py <bench> <system>[,<system>] [--workers N] [--limit N]

bench: olmocr | omnidocbench | funsd | longextract        system: reducto | vanilla (:8883) | ours (:8884)
Responses: e2e/full_bench/<bench>/responses/<system>/<case id>.json, saved by e2e/harness.py run_one (parse) or
e2e/harness_tasks.py run_task (extract): the same HTTP requests as the devset. A case with a saved good response is
skipped; failed cases are retried once at the end. Reducto: a cached devset response is copied, never re-requested.

Budgets (hard): every new response is written to a ledger in ~/.cache/full_bench (anthropic.jsonl: USD from token
usage; reducto.jsonl: pages at list price). A run stops before a cap would be passed.
"""
import json, os, shutil, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.dirname(HERE) + '/devset')
import harness as H            # noqa: E402
import harness_tasks as T      # noqa: E402

R = H.R
FB = 'e2e/full_bench/'
CACHE = os.path.expanduser('~/.cache/full_bench')
URLS = {'vanilla': 'http://127.0.0.1:8883', 'ours': 'http://127.0.0.1:8884'}
PRICES = {'haiku': (0.10, 0.50), 'sonnet': (2.0, 10.0), 'task': (0.10, 0.50)}
ANTHROPIC_CAP = 40.0; ANTHROPIC_STOP = 38.5           # stop early: requests in flight still land after the stop
REDUCTO_PARSE_CAP = 30.0; REDUCTO_EXTRACT_CAP = 45.0  # extract cap raised from 15 to 45 by the owner; USD at list price ($0.01 parse page, $0.02 extract page)
LOCK = threading.Lock()
STATE = {'stop': False, 'pause_until': 0.0, 'bench': None}
for k, u in URLS.items():
    H.TARGETS[k] = (u, None)


def out_path(target, case):
    return f"{HERE}/{STATE['bench']}/responses/{target}/{case['id']}.json"


H.out_path = out_path


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    print(line, flush=True)
    with LOCK:
        open(f"{HERE}/{STATE['bench']}/progress.log", 'a').write(line + '\n')


def ledger_add(name, row):
    os.makedirs(CACHE, exist_ok=True)
    with open(f'{CACHE}/{name}.jsonl', 'a') as f:
        f.write(json.dumps(row) + '\n')


def ledger_sum(name, pred=lambda r: True):
    p = f'{CACHE}/{name}.jsonl'
    if not os.path.exists(p): return 0.0
    tot = 0.0
    for l in open(p):
        try: r = json.loads(l)
        except Exception: continue
        if pred(r): tot += r.get('usd', 0.0)
    return tot


def silent_empty(d):
    """Our server answered 200 with no block at all and made no model call: the page read was lost without an error.
    Treated as a failed request (retried once); if it stays empty it is scored as an empty response."""
    h = d.get('_harness', {}); r = d.get('result')
    if h.get('target') == 'reducto' or h.get('endpoint') == 'extract' or not isinstance(r, dict): return False
    blocks = [b for ch in (r.get('chunks') or []) for b in (ch.get('blocks') or [])]
    return not blocks and not ((d.get('usage') or {}).get('model_usage') or {}).get('calls')


def good(d):
    return bool(d) and d.get('_harness', {}).get('status') == 200 and isinstance(d.get('result'), (dict, list)) and not silent_empty(d)


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


def reducto_pages(c):
    return c.get('pages', 1) * (0.02 if c.get('endpoint') == 'extract' else 0.01)


def one(target, c):
    if STATE['stop']: return 'skipped'
    d = load(target, c)
    if good(d): return 'cached'
    extract = c.get('endpoint') == 'extract'
    if target == 'reducto':
        with LOCK:
            kind = 'extract' if extract else 'parse'
            spent = ledger_sum('reducto', lambda r: r['kind'] == kind)
            if spent + reducto_pages(c) > (REDUCTO_EXTRACT_CAP if extract else REDUCTO_PARSE_CAP):
                STATE['stop'] = True; log(f'REDUCTO {kind} BUDGET STOP at ${spent:.2f}'); return 'skipped'
            ledger_add('reducto', {'bench': STATE['bench'], 'id': c['id'], 'kind': kind, 'usd': reducto_pages(c), 't': time.time()})
    else:
        spent = ledger_sum('anthropic')
        if spent > ANTHROPIC_STOP:
            with LOCK:
                if not STATE['stop']: STATE['stop'] = True; log(f'ANTHROPIC BUDGET STOP at ${spent:.3f}')
            return 'skipped'
    if d is not None and target != 'reducto' and silent_empty(d):
        os.replace(out_path(target, c), out_path(target, c)[:-5] + '.empty_first_attempt')
    for attempt in range(4):                          # throttling: wait and send again (not counted as the retry)
        w = STATE['pause_until'] - time.time()
        if w > 0: time.sleep(w)
        cid, st, sec = (T.run_task(target, c, force=target != 'reducto') if extract else H.run_one(target, c))
        d = load(target, c)
        if st == 200 and good(d): break
        if (isinstance(st, int) or str(st).startswith('ERR')) and throttled(d) :
            with LOCK: STATE['pause_until'] = time.time() + 30 * (attempt + 1)
            log(f'{target} {cid} throttled (status {st}), pausing {30 * (attempt + 1)}s')
            continue
        break
    if target != 'reducto' and d and st != 'cached':
        ledger_add('anthropic', {'bench': STATE['bench'], 'target': target, 'id': c['id'], 'usd': cost(d), 't': time.time()})
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
                    log(f'{target} {rnd}: {n}/{len(todo)} done, {len(bad)} failed, {time.time() - t0:.0f}s, anthropic ledger ${ledger_sum("anthropic"):.3f}, '
                        f'reducto ledger ${ledger_sum("reducto"):.2f}')
        log(f'{target} {rnd} finished: {len(todo)} requested, {len(bad)} failed, wall {time.time() - t0:.0f}s')
        todo = bad
    log(f'{target}: done, {len(todo)} cases without a good response')


def copy_reducto_cache(cs, sources):
    """Development-sample responses of Reducto: copied when the request is the same (same file bytes and body)."""
    n = 0
    for c in cs:
        if not c.get('sample') or os.path.exists(out_path('reducto', c)): continue
        for s in sources:
            p = s + (c.get('cache_id') or c['id']) + '.json'
            if not os.path.exists(p): continue
            d = json.load(open(p))
            if c.get('cache_same_request') or d.get('_harness', {}).get('key') == H.key('reducto', c):
                os.makedirs(os.path.dirname(out_path('reducto', c)), exist_ok=True); shutil.copy(p, out_path('reducto', c)); n += 1
                break
    return n


def main(bench, all_cases, default_workers):
    STATE['bench'] = bench
    systems = sys.argv[2].split(',')
    flag = lambda n, d: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
    cs = all_cases(); limit = int(flag('--limit', 0))
    if 'reducto' in systems:
        n = copy_reducto_cache(cs, [R + 'e2e/devset/responses/reducto/', R + 'e2e/responses/reducto/'])
        missing = [c['id'] for c in cs if c.get('sample') and not good(load('reducto', c))]
        log(f'reducto: {n} cached development-sample responses copied; sample cases still without one: {len(missing)}')
    if limit:
        cs = [c for c in cs if not c.get('sample')][:limit]
    log(f'start {bench} {systems}, {len(cs)} cases, anthropic ledger ${ledger_sum("anthropic"):.3f}, reducto ledger ${ledger_sum("reducto"):.2f}')
    ths = [threading.Thread(target=run, args=(t, cs, int(flag('--workers', default_workers[t])))) for t in systems]
    [t.start() for t in ths]; [t.join() for t in ths]
    log(f'end {bench} {systems}, anthropic ledger ${ledger_sum("anthropic"):.3f}, reducto ledger ${ledger_sum("reducto"):.2f}, stop={STATE["stop"]}')
