"""Downloads files of a public Hugging Face dataset (anonymous, resumable: existing files of the right size are kept).

    python hf_fetch.py <repo id> <path prefix in repo or ''> <destination dir> [--workers N] [--only-ext .pdf,.json]

Prints the number of files and bytes. Nothing but the repo id and file paths is sent.
"""
import json, os, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import httpx

repo, prefix, dst = sys.argv[1:4]
flag = lambda n, d: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
workers = int(flag('--workers', 8)); exts = tuple(x for x in flag('--only-ext', '').split(',') if x)
UA = {'User-Agent': 'bench-fetch/1.0'}


def tree(c, path):
    out = []; url = f'https://huggingface.co/api/datasets/{repo}/tree/main/{urllib.parse.quote(path)}?recursive=true&limit=1000'
    while url:
        for attempt in range(6):
            r = c.get(url, headers=UA)
            if r.status_code == 200: break
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()
        out += [x for x in r.json() if x['type'] == 'file']
        nxt = r.links.get('next'); url = nxt['url'] if nxt else None
    return out


def fetch(x):
    p = os.path.join(dst, x['path']); size = (x.get('lfs') or {}).get('size') or x.get('size')
    if os.path.exists(p) and os.path.getsize(p) == size: return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    url = f'https://huggingface.co/datasets/{repo}/resolve/main/' + urllib.parse.quote(x['path'])
    for attempt in range(6):
        try:
            with httpx.Client(timeout=300, follow_redirects=True, headers=UA) as c:
                r = c.get(url)
                if r.status_code == 200 and len(r.content) == size:
                    open(p + '.part', 'wb').write(r.content); os.replace(p + '.part', p); return size
                err = f'status {r.status_code} bytes {len(r.content)} want {size}'
        except Exception as e:
            err = repr(e)[:100]
        time.sleep(4 * (attempt + 1))
    print('FAILED', x['path'], err, flush=True); return -1


with httpx.Client(timeout=120, follow_redirects=True) as c:
    files = tree(c, prefix)
files = [x for x in files if not os.path.basename(x['path']).startswith('.') and (not exts or x['path'].endswith(exts))]
total = sum((x.get('lfs') or {}).get('size') or x.get('size') or 0 for x in files)
print(f'{repo}/{prefix}: {len(files)} files, {total / 1e6:.1f} MB', flush=True)
t0 = time.time(); got = bad = 0
with ThreadPoolExecutor(workers) as ex:
    for i, n in enumerate(ex.map(fetch, files), 1):
        bad += n < 0; got += max(n, 0)
        if i % 200 == 0: print(f'  {i}/{len(files)} files, {got / 1e6:.1f} MB new, {bad} failed, {time.time() - t0:.0f}s', flush=True)
print(f'done: {len(files)} files, {got / 1e6:.1f} MB downloaded now, {bad} failed, {time.time() - t0:.0f}s')
json.dump({'repo': repo, 'prefix': prefix, 'files': len(files), 'bytes': total, 'failed': bad}, open(os.path.join(dst, '_fetch_' + (prefix.replace('/', '_') or 'root') + '.json'), 'w'))
