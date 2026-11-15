"""LangChain document loader.

    pip install "readanydoc[engine,langchain]"

    from readanydoc.integrations.langchain import ReadAnyDocLoader
    docs = ReadAnyDocLoader("report.pdf", split="page").load()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Union

try:
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.documents import Document
except ImportError as e:  # pragma: no cover
    raise ImportError('The LangChain loader needs langchain-core: pip install "readanydoc[langchain]"') from e

from . import chunks


class ReadAnyDocLoader(BaseLoader):
    """Loads PDFs, scans, images and Office files as Markdown Documents, one per page by default.

    ``split``: "page" (default), "document" or "block". Other keyword arguments go to ``readanydoc.parse``
    (``mode``, ``model``, ``pages``, ...)."""

    def __init__(self, file_path: Union[str, Path], split: str = "page", **parse_kwargs: Any):
        self.file_path, self.split, self.parse_kwargs = file_path, split, parse_kwargs

    def lazy_load(self) -> Iterator[Document]:
        for text, metadata in chunks(self.file_path, self.split, **self.parse_kwargs):
            yield Document(page_content=text, metadata=metadata)
