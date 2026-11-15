"""LlamaIndex reader.

    pip install "readanydoc[engine,llamaindex]"

    from llama_index.core import SimpleDirectoryReader
    from readanydoc.integrations.llamaindex import ReadAnyDocReader

    docs = ReadAnyDocReader().load_data("report.pdf")
    # or for a folder:
    docs = SimpleDirectoryReader("contracts/", file_extractor={".pdf": ReadAnyDocReader()}).load_data()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

try:
    from llama_index.core.readers.base import BaseReader
    from llama_index.core.schema import Document
except ImportError as e:  # pragma: no cover
    raise ImportError('The LlamaIndex reader needs llama-index-core: pip install "readanydoc[llamaindex]"') from e

from . import chunks


class ReadAnyDocReader(BaseReader):
    """Reads PDFs, scans, images and Office files into Markdown Documents, one per page by default.

    ``split``: "page" (default), "document" or "block". Other keyword arguments go to ``readanydoc.parse``."""

    def __init__(self, split: str = "page", **parse_kwargs: Any):
        super().__init__()
        self.split, self.parse_kwargs = split, parse_kwargs

    def load_data(self, file: Union[str, Path], extra_info: Optional[dict] = None, **kwargs: Any) -> list[Document]:
        out = []
        for text, metadata in chunks(file, self.split, **self.parse_kwargs):
            meta = {**metadata, **(extra_info or {})}
            out.append(Document(text=text, metadata=meta))
        return out
