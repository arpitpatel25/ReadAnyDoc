"""OpenAI-compatible chat-completions adapter (vLLM / ollama endpoints: GLM-OCR, PaddleOCR-VL, ...).

Self-hosted endpoints usually need no key; if ``api_key_env`` is set and present it is sent as a
Bearer token. Unit-tested with mocked HTTP only.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Optional

import httpx

from ..model import RegionType
from . import prompts
from .base import (ImageInput, ReadContext, Reader, ReaderError, ReadResult, Usage, image_bytes,
                   media_type, strip_code_fence)


class OpenAICompatibleReader(Reader):
    granularity = "page"

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:8000/v1", prompt: Optional[str] = None,
                 max_tokens: int = 8192, temperature: Optional[float] = 0.0, timeout: float = 300.0,
                 max_retries: int = 2, api_key_env: Optional[str] = None, extra_body: Optional[dict] = None,
                 client: Optional[httpx.Client] = None, token_field: str = "max_tokens",
                 name_prefix: str = "openai_compatible"):
        self.model = model
        self.name = f"{name_prefix}:{model}"
        self.token_field = token_field
        self.base_url = base_url.rstrip("/")
        self.prompt = prompt or prompts.PAGE_PROMPT_INLINE
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.api_key_env = api_key_env
        self.extra_body = extra_body or {}
        self._client = client or httpx.Client(timeout=timeout)

    def build_request(self, data: bytes, prompt: str, max_tokens: int) -> dict:
        url = f"data:{media_type(data)};base64,{base64.standard_b64encode(data).decode('ascii')}"
        body: dict = {
            "model": self.model,
            self.token_field: max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": prompt},
            ]}],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.extra_body)
        return body

    def read(self, image: ImageInput, hint: Optional[RegionType] = None,
             context: Optional[ReadContext] = None) -> ReadResult:
        context = context or ReadContext()
        body = self.build_request(image_bytes(image), context.prompt or self.prompt,
                                  context.max_tokens or self.max_tokens)
        headers = {"content-type": "application/json"}
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if key:
            headers["authorization"] = f"Bearer {key}"
        t0 = time.time()
        resp = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise ReaderError(f"endpoint connection error: {e}") from e
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            break
        assert resp is not None
        if resp.status_code != 200:
            raise ReaderError(f"endpoint error {resp.status_code}: {resp.text[:300]}")
        j = resp.json()
        choice = (j.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        finish = choice.get("finish_reason")
        u = j.get("usage") or {}
        usage = Usage(model=j.get("model", self.model), input_tokens=u.get("prompt_tokens"),
                      output_tokens=u.get("completion_tokens"), seconds=round(time.time() - t0, 2))
        return ReadResult(text=strip_code_fence(content), format="markdown", usage=usage,
                          stop_reason={"stop": "end_turn", "length": "max_tokens"}.get(finish, finish),
                          truncated=finish == "length", raw=j)
