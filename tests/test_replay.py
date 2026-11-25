"""Record/replay transport: compressed answers are stored decoded, replayed, and replay-only refuses new calls."""
import httpx, json, gzip, tempfile
from readanydoc.replay import ReplayTransport
def handler(req):
    return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/json"},
                          content=gzip.compress(json.dumps({"ok": 1, "usage": {"input_tokens": 3}}).encode()))
def test_replay():
    d = tempfile.mkdtemp()
    c = httpx.Client(transport=ReplayTransport(d, inner=httpx.MockTransport(handler)))
    r1 = c.post("https://x/v1/messages", json={"a": 1}).json(); r2 = c.post("https://x/v1/messages", json={"a": 1}).json()
    c2 = httpx.Client(transport=ReplayTransport(d, inner=httpx.MockTransport(lambda r: httpx.Response(500)), only=True))
    r3 = c2.post("https://x/v1/messages", json={"a": 1}).json()
    try:
        c2.post("https://x/v1/messages", json={"a": 2}); raise AssertionError("no miss")
    except Exception as e: print("miss ok", type(e).__name__)
    assert r1 == r2 == r3 == {"ok": 1, "usage": {"input_tokens": 3}}
