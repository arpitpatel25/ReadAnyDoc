"""Readers: one interface, swappable adapters. Spec strings: ``<adapter>[:<arg>][?<json options>]``.

    anthropic_api:claude-haiku-5-5#high       Anthropic Messages API (``#effort`` optional)
    anthropic_batch:claude-haiku-5-5#high     the same requests through the Message Batches API (half price)
    openai:<model>                            OpenAI API (OPENAI_API_KEY)
    openai_compatible:<model>@<base_url>      any OpenAI-compatible endpoint, e.g. a self-hosted vLLM server
    claude_cli:haiku | cached:<system>        development only: Claude Code CLI, saved outputs

The JSON after ``?`` holds extra fields for the provider's request body (see ``readanydoc.modelspec``).
"""

from __future__ import annotations

import inspect
from typing import Callable, Optional

from .base import (ImageInput, ReadBlock, ReadContext, Reader, ReaderError, ReadResult, Usage,
                   WordBoxProvider)

READERS: dict[str, Callable[[str], Reader]] = {}
WORD_PROVIDERS: dict[str, Callable[[str], WordBoxProvider]] = {}


def register_reader(name: str):
    def deco(factory):
        READERS[name] = factory
        return factory
    return deco


def register_word_provider(name: str):
    def deco(factory):
        WORD_PROVIDERS[name] = factory
        return factory
    return deco


@register_reader("cached")
def _cached(arg: str) -> Reader:
    from .cached import CachedReader
    return CachedReader(arg or "haiku")


@register_reader("claude_cli")
def _claude_cli(arg: str) -> Reader:
    from .claude_cli import ClaudeCLIReader
    return ClaudeCLIReader(arg or "haiku")


@register_reader("anthropic_api")
def _anthropic(arg: str, options: Optional[dict] = None) -> Reader:
    from .anthropic_api import AnthropicAPIReader
    # anthropic_api:haiku#high -> output_config.effort. A trailing @<sample> (anthropic_api:haiku#high@run1)
    # keeps every answer on disk under READANYDOC_CALL_CACHE, keyed by image bytes, prompt, model and sample:
    # used by test servers so that a rerun after a change downstream of reading costs nothing.
    arg, _, sample = (arg or "haiku").partition("@")
    model, _, effort = arg.partition("#")
    reader = AnthropicAPIReader(model or "haiku", effort=effort or None, max_tokens=32000, timeout=900.0,
                                extra=options)
    if sample:
        from .live_cache import LiveCacheReader
        cached = LiveCacheReader(model or "haiku", sample, inner=reader)
        cached.name = reader.name
        return cached
    return reader


@register_reader("anthropic_batch")
def _anthropic_batch(arg: str, options: Optional[dict] = None) -> Reader:
    """anthropic_batch:haiku#high[@sample]: the requests of ``anthropic_api`` sent through the Message Batches
    API (half price; a read returns when its batch has ended). For async jobs."""
    from .anthropic_batch import AnthropicBatchReader
    arg, _, sample = (arg or "haiku").partition("@")
    model, _, effort = arg.partition("#")
    reader = AnthropicBatchReader(model or "haiku", effort=effort or None, max_tokens=32000, timeout=900.0,
                                  extra=options)
    if sample:
        from .live_cache import LiveCacheReader
        cached = LiveCacheReader(model or "haiku", sample, inner=reader)
        cached.name = reader.name
        return cached
    return reader


@register_reader("openai_compatible")
def _openai_compatible(arg: str, options: Optional[dict] = None) -> Reader:
    from .openai_compatible import OpenAICompatibleReader
    model, _, base = arg.partition("@")
    kwargs = {"base_url": base} if base else {}
    return OpenAICompatibleReader(model, extra_body=options, **kwargs)


@register_reader("openai")
def _openai(arg: str, options: Optional[dict] = None) -> Reader:
    """OpenAI's own API: OPENAI_API_KEY, no temperature, ``max_completion_tokens``."""
    from .openai_compatible import OpenAICompatibleReader
    model, _, effort = arg.partition("#")
    extra = dict(options or {})
    if effort:
        extra.setdefault("reasoning_effort", effort)
    return OpenAICompatibleReader(model, base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY",
                                  temperature=None, token_field="max_completion_tokens", max_tokens=32000,
                                  extra_body=extra, name_prefix="openai")


@register_reader("crop_cache")
def _crop_cache(arg: str) -> Reader:
    from .crop_cache import CropCacheReader
    return CropCacheReader(arg or "haiku")


@register_reader("live_cache")
def _live_cache(arg: str) -> Reader:
    from .live_cache import LiveCacheReader
    model, _, sample = arg.partition("#")
    return LiveCacheReader(model or "haiku", sample or "1")


@register_word_provider("tesseract")
def _tesseract_words(arg: str):
    from .live_cache import TesseractWords
    return TesseractWords(arg)


@register_word_provider("rapidocr")
def _rapidocr_words(arg: str):
    from .ocr_words import RapidOcrWords
    return RapidOcrWords(arg)


@register_word_provider("tesseract+rapidocr")
def _tesseract_rapidocr_words(arg: str):
    from .ocr_words import TesseractPlusRapidOcr
    return TesseractPlusRapidOcr(arg)


@register_word_provider("tesseract>rapidocr")
def _tesseract_then_rapidocr_words(arg: str):
    from .ocr_words import TesseractThenRapidOcr
    return TesseractThenRapidOcr(arg)


@register_word_provider("cached")
def _cached_words(arg: str) -> WordBoxProvider:
    from .cached import CachedPPOCR
    if arg not in ("", "ppocr"):
        raise ReaderError(f"unknown cached word source {arg!r}")
    return CachedPPOCR()


def make_reader(spec: str) -> Reader:
    from ..modelspec import split_options
    spec, options = split_options(spec)
    name, _, arg = spec.partition(":")
    if name not in READERS:
        raise ReaderError(f"unknown reader {name!r}; known: {sorted(READERS)}")
    if options:
        if "options" not in inspect.signature(READERS[name]).parameters:
            raise ReaderError(f"reader {name!r} takes no model options")
        return READERS[name](arg, options=options)
    return READERS[name](arg)


def make_word_provider(spec: str) -> WordBoxProvider:
    name, _, arg = spec.partition(":")
    if name not in WORD_PROVIDERS:
        raise ReaderError(f"unknown word provider {name!r}; known: {sorted(WORD_PROVIDERS)}")
    return WORD_PROVIDERS[name](arg)


__all__ = ["ImageInput", "ReadBlock", "ReadContext", "Reader", "ReaderError", "ReadResult", "Usage",
           "WordBoxProvider", "READERS", "WORD_PROVIDERS", "register_reader", "register_word_provider",
           "make_reader", "make_word_provider"]
