"""Input formats: file-type sniffing and digital-native readers.

``load_native(path, kind, opts)`` builds the pages of a DOCX / XLSX / CSV / TXT / PPTX / EML file
directly from the file: no rendering, no model calls. PDFs and images are opened by
``pipeline/intake.py``; ``pdf.py`` here adds properties, link annotations and comments.
"""

from .errors import CellCountExceeded, IntakeError, cell_count_exceeded, unsupported
from .sniff import IMAGE_KINDS, LEGACY_KINDS, NATIVE_KINDS, sniff

__all__ = ["CellCountExceeded", "IntakeError", "cell_count_exceeded", "unsupported", "sniff", "IMAGE_KINDS", "LEGACY_KINDS",
           "NATIVE_KINDS"]
