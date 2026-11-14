"""Layout detectors. Spec strings: ``cached:pvl`` | ``cached:glm`` | ``claude`` (stub) | ``onnx:heron`` |
``onnx:ppdoclayout_plus`` (open models on CPU, see ``onnx.py``)."""

from __future__ import annotations

from typing import Callable

from .base import LABEL_MAP, LayoutBlock, LayoutDetector, LayoutError, map_label

LAYOUTS: dict[str, Callable[[str], LayoutDetector]] = {}


def register_layout(name: str):
    def deco(factory):
        LAYOUTS[name] = factory
        return factory
    return deco


@register_layout("cached")
def _cached(arg: str) -> LayoutDetector:
    from .cached import CachedGLMLayout, CachedPVLLayout
    if arg in ("", "pvl"):
        return CachedPVLLayout()
    if arg == "glm":
        return CachedGLMLayout()
    raise LayoutError(f"unknown cached layout {arg!r}")


@register_layout("claude")
def _claude(arg: str) -> LayoutDetector:
    from .claude import ClaudeLayout
    model, _, extra = arg.partition("+")
    return ClaudeLayout(model or "haiku", forms=(extra == "forms"))


@register_layout("onnx")
def _onnx(arg: str) -> LayoutDetector:
    from .onnx import OnnxLayout
    return OnnxLayout(arg or "heron")


def make_layout(spec: str) -> LayoutDetector:
    name, _, arg = spec.partition(":")
    if name not in LAYOUTS:
        raise LayoutError(f"unknown layout detector {name!r}; known: {sorted(LAYOUTS)}")
    return LAYOUTS[name](arg)


__all__ = ["LABEL_MAP", "LayoutBlock", "LayoutDetector", "LayoutError", "map_label", "LAYOUTS",
           "register_layout", "make_layout"]
