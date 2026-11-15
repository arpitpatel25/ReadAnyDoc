"""readanydoc: an open-source document parser around vision models, with Reducto's HTTP API.

Use it as a library (``readanydoc.parse``, ``readanydoc.extract``) or run the server (``readanydoc serve``).
"""

__version__ = "0.1.4"

from .library import (Block, BoundingBox, Citation, ExtractResult, ParseResult, Usage,  # noqa: E402
                      extract, parse)

__all__ = ["parse", "extract", "ParseResult", "ExtractResult", "Block", "BoundingBox", "Citation", "Usage",
           "__version__"]
