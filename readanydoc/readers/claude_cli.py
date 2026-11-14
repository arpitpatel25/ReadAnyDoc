"""Claude through the Claude Code CLI subscription (dev only). Pattern: spike/run_claude.py.

No API key is used: ANTHROPIC_API_KEY and CLAUDECODE are stripped from the child environment.
Run at most 4 of these in parallel.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..model import RegionType
from . import prompts
from .base import ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage, strip_code_fence

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}


class ClaudeCLIReader(Reader):
    granularity = "page"

    def __init__(self, model: str = "haiku", timeout: float = 900.0, binary: str = "claude"):
        self.model = MODELS.get(model, model)
        self.name = f"claude_cli:{model}"
        self.timeout = timeout
        self.binary = binary

    def build_command(self, prompt: str) -> list[str]:
        return [self.binary, "-p", prompt, "--model", self.model, "--allowedTools", "Read",
                "--output-format", "json", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']

    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        context = context or ReadContext()
        tmp = None
        if isinstance(image, bytes):
            fd, tmp = tempfile.mkstemp(suffix=".png", prefix="readanydoc_")
            os.write(fd, image)
            os.close(fd)
            img_path = tmp
        elif image is None:
            raise ReaderError("ClaudeCLIReader needs an image path or bytes")
        else:
            img_path = str(Path(image).resolve())
        prompt = (context.prompt or prompts.PAGE_PROMPT_FILE).format(img=img_path)
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDECODE", None)
        t0 = time.time()
        try:
            p = subprocess.run(self.build_command(prompt), capture_output=True, text=True,
                               timeout=self.timeout, stdin=subprocess.DEVNULL, env=env,
                               cwd=tempfile.gettempdir())
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ReaderError(f"claude CLI failed: {e}") from e
        finally:
            if tmp:
                os.unlink(tmp)
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise ReaderError(f"claude CLI returned non-JSON output: {p.stdout[:200]!r} {p.stderr[:200]!r}") from e
        text = strip_code_fence(j.get("result") or "")
        u = j.get("usage") or {}
        usage = Usage(model=self.model, input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                      cost_usd=j.get("total_cost_usd"), seconds=round(time.time() - t0, 1))
        return ReadResult(text=text, format="markdown", usage=usage, raw=j,
                          stop_reason="error" if j.get("is_error") else "end_turn")
