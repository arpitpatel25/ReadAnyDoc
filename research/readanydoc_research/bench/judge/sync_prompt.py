"""Regenerate ``prompt_template.txt`` from Parse Judge's ``prompt.ts`` (run by hand after updating
``benchmarks/parsejudge``):  uv run python -m readanydoc_research.bench.judge.sync_prompt [path/to/prompt.ts]

The TypeScript source is one template literal. The only transformations are the ones the JavaScript
engine itself applies: ``\\``` becomes a backtick. The two interpolations stay as ``${parseSections}``
and ``${labels}`` and are filled by ``prompt.compare_prompt``.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parents[3] / "benchmarks" / "parsejudge" / "packages" / "core" / "src" / "prompt.ts"


def template_from_ts(source: str) -> str:
    start = source.index("): string => `") + len("): string => `")
    end = source.rindex("`;")
    body = source[start:end]
    if "\\${" in body or "\\\\" in body:
        raise ValueError("prompt.ts uses an escape this converter does not handle")
    return body.replace("\\`", "`")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    out = HERE / "prompt_template.txt"
    out.write_text(template_from_ts(src.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes) from {src}")
