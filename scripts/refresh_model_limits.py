"""Refresh readanydoc/data/model_limits.json from LiteLLM's public model list (MIT licence,
https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json). Run at release time:

    python scripts/refresh_model_limits.py [path-or-url]

Keeps chat models with a known context size, and only the fields ReadAnyDoc uses: context and output limits, the
token count above which a request is billed at a higher rate, and prices. No API call, no tokens.
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
OUT = Path(__file__).resolve().parents[1] / "readanydoc" / "data" / "model_limits.json"
ABOVE = re.compile(r"^input_cost_per_token_above_(\d+)k_tokens$")


def entry(v: dict) -> dict:
    out = {"provider": v.get("litellm_provider"), "max_input": v.get("max_input_tokens"),
           "max_output": v.get("max_output_tokens") or v.get("max_tokens")}
    for src, dst in (("input_cost_per_token", "in"), ("output_cost_per_token", "out"),
                     ("cache_read_input_token_cost", "cache_read"), ("cache_creation_input_token_cost", "cache_write")):
        if isinstance(v.get(src), (int, float)):
            out[dst] = v[src]
    tiers = sorted(int(m.group(1)) for k in v if (m := ABOVE.match(k)))
    if tiers:  # the first price jump: a request above this many input tokens is billed at the higher rate
        n = tiers[0]
        out["price_jump"] = n * 1000
        for src, dst in ((f"input_cost_per_token_above_{n}k_tokens", "in_above"),
                         (f"output_cost_per_token_above_{n}k_tokens", "out_above"),
                         (f"cache_read_input_token_cost_above_{n}k_tokens", "cache_read_above"),
                         (f"cache_creation_input_token_cost_above_{n}k_tokens", "cache_write_above")):
            if isinstance(v.get(src), (int, float)):
                out[dst] = v[src]
    return {k: x for k, x in out.items() if x is not None}


def main(src: str = URL) -> None:
    raw = urllib.request.urlopen(src).read() if src.startswith("http") else Path(src).read_bytes()
    data = json.loads(raw)
    models = {k: entry(v) for k, v in data.items()
              if isinstance(v, dict) and v.get("mode") in ("chat", "responses") and v.get("max_input_tokens")}
    OUT.write_text(json.dumps({"source": URL, "licence": "MIT (BerriAI/litellm)", "refreshed": str(date.today()),
                               "models": dict(sorted(models.items()))}, separators=(",", ":")))
    print(f"{len(models)} models -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(*sys.argv[1:])
