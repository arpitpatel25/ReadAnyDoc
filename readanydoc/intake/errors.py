"""Intake errors.

The API maps an exception whose class is named ``IntakeError`` by its message: "password" -> 442,
"not supported" -> 415 DOCUMENT_UNSUPPORTED, "not found" -> 400, anything else -> 415 DOCUMENT_CORRUPT.
An error that needs another status (422 for ``spreadsheet.max_cell_count``) is built at raise time
as a subclass of both ``IntakeError`` and the API's ``ApiError`` (when that module can be imported),
so the API returns it unchanged and the pipeline still works without the HTTP layer.
"""

from __future__ import annotations


class IntakeError(RuntimeError):
    pass


class CellCountExceeded(IntakeError):
    """spreadsheet.max_cell_count exceeded: HTTP 422, ErrorCode CELL_COUNT_EXCEEDED."""

    status_code = 422
    code = "CELL_COUNT_EXCEEDED"


def unsupported(kind: str, why: str = "", hint: str = "") -> IntakeError:
    """415 in Reducto's style. The words "not supported" select the status code in the API layer."""
    msg = f"File type {kind!r} is not supported"
    if why:
        msg += f": {why}"
    msg += ". " + (hint or "Convert the file to PDF, DOCX, XLSX, PPTX, CSV, TXT or an image and try again.")
    return IntakeError(msg)


def damaged(kind: str, exc: BaseException) -> IntakeError:
    """415 DOCUMENT_CORRUPT. The library's own message is left out: its wording must not pick the status."""
    return IntakeError(f"cannot open the {kind} file: it is damaged or is not really a {kind} file "
                       f"({type(exc).__name__})")


def cell_count_exceeded(count: int, limit: int) -> CellCountExceeded:
    msg = (f"Spreadsheet has {count} non-empty cells, more than spreadsheet.max_cell_count={limit}. "
           "Raise the limit, set it to null, or narrow settings.page_range to fewer sheets.")
    try:
        from ..api.errors import ApiError
    except Exception:  # no HTTP layer installed
        err = CellCountExceeded(msg)
    else:
        cls = type("CellCountExceededApi", (CellCountExceeded, ApiError), {})
        err = cls.__new__(cls)
        ApiError.__init__(err, 422, "CELL_COUNT_EXCEEDED", msg, "cell_count_exceeded")
    err.count, err.limit = count, limit  # type: ignore[attr-defined]
    return err


class NoPagesInRange(IntakeError):
    """settings.page_range selects no page of the document: HTTP 400 INVALID_CONFIG, as on Reducto."""

    status_code = 400
    code = "INVALID_CONFIG"


def no_pages_in_range(total: int) -> IntakeError:
    msg = (f"Invalid configuration for 'page_range': No pages found in the requested range. The document has "
           f"{total} pages; page_range is 1-indexed and inclusive.")
    try:
        from ..api.errors import ApiError
    except Exception:  # no HTTP layer installed
        return NoPagesInRange(msg)
    cls = type("NoPagesInRangeApi", (NoPagesInRange, ApiError), {})
    err = cls.__new__(cls)
    ApiError.__init__(err, 400, "INVALID_CONFIG", msg, "invalid_page_range")
    return err
