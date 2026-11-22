"""Profiles: named pipeline configurations.

readanydoc ships two profiles. Both read every page with a vision model (Claude Haiku 5.5 at high effort unless
you choose another model) and return the same Reducto-shaped result:

    engine       the readanydoc engine around the model (the default)
    model-only   one model read per page and nothing else

Measured results for both, on public benchmarks, are in BENCHMARKS.md. Further profiles can be added with
``register()``; modules listed in ``READANYDOC_PROFILE_MODULES`` (comma-separated) are imported on first use so
that they can register theirs. The ``research/`` folder of the repository uses this for the configurations
that were compared during development.
"""

from __future__ import annotations

import importlib
import os
from typing import Optional

from .pipeline import PipelineConfig

HAIKU = "anthropic_api:claude-haiku-5-5#high"
SONNET = "anthropic_api:claude-sonnet-5-5"

ENGINE = PipelineConfig.model_validate({
    "profile": "engine",
    "reader": HAIKU,
    "layout": "onnx:heron",              # open layout model on CPU: block types and boxes
    "words": "tesseract>rapidocr",       # word positions for citations; PP-OCR fills what Tesseract misses
    "stages": {
        # straighten pages that are turned (only when the evidence is unambiguous)
        "triage": {"options": {"prepare": "rotate", "prepare_method": "safe"}},
        # layout regions are used as evidence; they never replace the model's text
        "layout": {"options": {"detect_only": True}},
        "read": {"options": {
            # the page read labels running headers/footers/page and line numbers (engine-final-5, 2026-11-22)
            "page_prompt": "PAGE_PROMPT_LABELLED_FILE",
            "table_select_no_layout": False,
            "page_workers": 16,
            # scanned pages: a token is corrected only when two zoomed reads agree against the page read
            "zoom_vote": True, "zoom_pre_gate": 20, "zoom_prefetch": 1, "zoom_gate": "table_legible",
            "zoom_regions": "gate", "zoom_second": "disputed",
            # handwritten pages go to a stronger reader, unless local OCR reads the page as print
            "handwriting_route": True, "handwriting_reader": SONNET, "handwriting_print_ocr": True,
            "handwriting_illegible_max_agreement": 0.5, "handwriting_illegible_sure_agreement": 0.33,
            "handwriting_max_ocr_agreement": 0.33,
            # forms: fillable-PDF values, label/value lines, and form grids that are not data tables
            "forms": "fillable,kv_lines:join,kv_rules:rows,kv_guard", "form_prefetch": True,
        }},
        # deterministic checks: table grids repaired only into a consistent grid; printed totals checked
        "verify": {"options": {"checks": "grid=norm+draft;numbers=1;flags=1"}},
        "ground": {"impl": "ground_regions", "options": {
            "page_numbers": False,
            # block types, boxes, headers/footers, page numbers, form regions and prose from layout evidence
            "regions": "types=guarded,boxes,split,forms=notables,hfsplit,kvsplit,kv=page,formread,boxfill,"
                       "hfdet,pnsplit,prose",
            # each table is read again on its own and the second read is kept only when its cells agree
            "tables": "reread=missed+broken+all;accept=content;missed_prompt=force;clean=1;formulas=matrix;"
                      "prefetch=1;prefetch_score=0.6;prefetch_area=0.02",
        }},
        "shape": {"options": {"dynamic_tables": False}},
    },
})

MODEL_ONLY = PipelineConfig.model_validate({
    "profile": "model-only",
    "reader": HAIKU,
    "words": "tesseract",
    "stages": {
        "layout": {"enabled": False},
        "read": {"options": {"page_prompt": "PAGE_PROMPT_FULL_FILE", "table_select_no_layout": False,
                             "page_workers": 6}},
        "ground": {"options": {"page_numbers": False}},
        "shape": {"options": {"dynamic_tables": False}},
    },
})

DEFAULT_PROFILE = "engine"
PROFILES: dict[str, PipelineConfig] = {"engine": ENGINE, "model-only": MODEL_ONLY}
SHIPPED = frozenset(PROFILES)

# Reducto's ``settings.model`` values. r-1-mini and legacy are Reducto's cheaper tiers.
MODEL_TO_PROFILE = {"r-1": "engine", "r-1-mini": "model-only", "legacy": "model-only"}

# The cheaper sibling of a profile: used for r-1-mini / legacy when the server is configured with one profile.
CHEAPER: dict[str, str] = {"engine": "model-only"}

_plugins_loaded = False


def register(name: str, config: PipelineConfig, cheaper: Optional[str] = None) -> None:
    """Add a profile (and optionally its cheaper sibling for r-1-mini / legacy requests)."""
    if name in SHIPPED:
        raise ValueError(f"{name!r} is a built-in profile; register under another name")
    PROFILES[name] = config
    if cheaper:
        CHEAPER[name] = cheaper


def load_plugins() -> None:
    """Import the modules named in READANYDOC_PROFILE_MODULES once, so that they can register profiles."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for name in filter(None, (m.strip() for m in os.environ.get("READANYDOC_PROFILE_MODULES", "").split(","))):
        importlib.import_module(name)


def get_profile(name: str) -> PipelineConfig:
    if name not in PROFILES:
        load_plugins()
    if name not in PROFILES:
        raise KeyError(f"unknown profile {name!r}; known: {sorted(PROFILES)}")
    return PROFILES[name].model_copy(deep=True)
