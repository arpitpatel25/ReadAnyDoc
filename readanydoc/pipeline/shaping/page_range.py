"""settings.page_range in every form Reducto accepts -> 1-based page numbers of the source file.

Forms: ``{"start": 2, "end": 5}``, a list of those, a list of ints, ``"2-5"`` strings, and (for
spreadsheets) a list of sheet names. Pages are 1-indexed and inclusive; an open end runs to the last
page. The selected pages keep their source number as ``original_page`` while ``page`` restarts at 1.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


class PageRangeError(ValueError):
    pass


def resolve_page_range(spec: Any, total: int, names: Optional[Sequence[str]] = None) -> list[int]:
    """Sorted, de-duplicated 1-based page numbers within ``1..total``. ``None`` means every page."""
    if spec is None or spec == [] or spec == {}:
        return list(range(1, total + 1))
    items = spec if isinstance(spec, (list, tuple)) else [spec]
    lowered = {str(n).strip().lower(): i + 1 for i, n in enumerate(names or [])}
    pages: set[int] = set()
    for it in items:
        if isinstance(it, bool):
            raise PageRangeError("Invalid page range: booleans are not page numbers")
        if isinstance(it, int):
            lo, hi = it, it
        elif isinstance(it, dict):
            lo = 1 if it.get("start") is None else int(it["start"])
            hi = total if it.get("end") is None else int(it["end"])
        elif isinstance(it, str):
            key = it.strip().lower()
            if key in lowered:  # a sheet name wins over a numeric reading ("2024" can be a sheet)
                pages.add(lowered[key])
                continue
            parts = it.replace(" ", "").split("-")
            if len(parts) in (1, 2) and all(p.isdigit() for p in parts):
                lo, hi = int(parts[0]), int(parts[-1])
            elif names is not None:
                raise PageRangeError(f"Invalid page range: no sheet named {it!r}")
            else:
                raise PageRangeError(f"Invalid page range: {it!r}")
        else:
            raise PageRangeError(f"Invalid page range: {it!r}")
        if lo < 1 or hi < lo:
            raise PageRangeError(f"Invalid page range: start={lo}, end={hi} (pages are 1-indexed, start <= end)")
        pages.update(range(lo, min(hi, total) + 1))
    return sorted(pages)
