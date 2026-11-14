"""Pipeline stages. Import ``Pipeline``/``PipelineConfig`` from here."""

from __future__ import annotations

import importlib

from .base import (STAGE_ORDER, STAGES, ParseOptions, Pipeline, PipelineConfig, PipelineContext, PipelineTimeout,
                   Stage, StageConfig, register_stage)

_loaded = False


def _load_builtin_stages() -> None:
    global _loaded
    if _loaded:
        return
    for name in STAGE_ORDER:
        importlib.import_module(f"{__name__}.{name}")
    _loaded = True


__all__ = ["STAGE_ORDER", "STAGES", "ParseOptions", "Pipeline", "PipelineConfig", "PipelineContext",
           "PipelineTimeout", "Stage", "StageConfig", "register_stage"]
