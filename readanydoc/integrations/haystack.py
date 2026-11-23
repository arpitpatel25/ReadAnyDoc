"""Haystack document converter.

    pip install "readanydoc[engine,haystack]"

    from readanydoc.integrations.haystack import ReadAnyDocConverter
    docs = ReadAnyDocConverter().run(sources=["report.pdf"])["documents"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

try:
    from haystack import Document, component, default_to_dict
except ImportError as e:  # pragma: no cover
    raise ImportError('The Haystack converter needs haystack-ai: pip install "readanydoc[haystack]"') from e

from . import chunks


@component
class ReadAnyDocConverter:
    """Converts local files to Markdown Documents, one per page by default.

    ``split``: "page" (default), "document" or "block". Other keyword arguments go to ``readanydoc.parse``.
    """

    def __init__(self, split: str = "page", **parse_kwargs: Any):
        self.split, self.parse_kwargs = split, parse_kwargs

    def to_dict(self) -> dict[str, Any]:
        """Serialize parse options as constructor keywords for Haystack pipelines."""
        return default_to_dict(self, split=self.split, **self.parse_kwargs)

    @component.output_types(documents=list[Document])
    def run(self, sources: list[Union[str, Path]]) -> dict[str, list[Document]]:
        """Convert the given file paths in order. Parser errors propagate to the caller."""
        documents = []
        for source in sources:
            for text, metadata in chunks(source, self.split, **self.parse_kwargs):
                documents.append(Document(content=text, meta=metadata))
        return {"documents": documents}
