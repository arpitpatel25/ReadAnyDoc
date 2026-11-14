"""Internal document format shared by every stage.

Stable API: import from ``readanydoc.model``.
"""

from .document import (
    SCHEMA_VERSION,
    BBox,
    Candidate,
    Chunk,
    Confidence,
    CrossPageLink,
    Document,
    Evidence,
    HeadingNode,
    Line,
    Page,
    Region,
    RegionType,
    StageNote,
    TableCell,
    Word,
)

__all__ = [
    "SCHEMA_VERSION",
    "BBox",
    "Candidate",
    "Chunk",
    "Confidence",
    "CrossPageLink",
    "Document",
    "Evidence",
    "HeadingNode",
    "Line",
    "Page",
    "Region",
    "RegionType",
    "StageNote",
    "TableCell",
    "Word",
]
