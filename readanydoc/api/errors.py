"""Errors in Reducto's shape.

Reducto's error-codes page shows ``{"error": {"message", "type", "code"}}``; FastAPI-style clients read
``detail``. We send both, plus ``name`` (the ``ErrorCode`` enum of the OpenAPI spec) inside ``error``.
Failed jobs carry an ``ErrorDetail`` object: ``{code, name, message, job_id}``.
"""

from __future__ import annotations

from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, name: str, message: str, type: Optional[str] = None,
                 job_id: Optional[str] = None, headers: Optional[dict[str, str]] = None):
        super().__init__(message)
        self.status = status
        self.name = name  # ErrorCode enum value
        self.message = message
        self.type = type or name.lower()
        self.job_id = job_id
        self.headers = headers

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"message": self.message, "type": self.type, "code": self.status, "name": self.name}
        if self.job_id:
            err["job_id"] = self.job_id
        return {"error": err, "detail": self.message}

    def detail(self, job_id: Optional[str] = None) -> dict[str, Any]:
        """``ErrorDetail`` for GET /job."""
        return {"code": self.status, "name": self.name, "message": self.message, "job_id": job_id or self.job_id}


def bad_request(message: str, name: str = "INVALID_CONFIG", type: str = "bad_request") -> ApiError:
    return ApiError(400, name, message, type)


def download_failed(extra: str = "") -> ApiError:
    msg = "Failed to download file from url. Please check the document url and try again"
    return ApiError(400, "PROCESSING_FAILED", msg + (f" ({extra})" if extra else ""), "file_download_failure")


def not_found(message: str, type: str = "not_found") -> ApiError:
    return ApiError(404, "NOT_FOUND", message, type)


def unauthorized() -> ApiError:
    return ApiError(401, "AUTH_ERROR", "Invalid or missing API key. Send 'Authorization: Bearer <key>'.",
                    "authentication_error", headers={"WWW-Authenticate": "Bearer"})


def not_implemented(message: str) -> ApiError:
    return ApiError(501, "NOT_APPLICABLE", message, "not_implemented")


def internal(message: str) -> ApiError:
    return ApiError(500, "INTERNAL_ERROR", message, "internal_error")


def from_exception(exc: BaseException) -> ApiError:
    """Map pipeline / task exceptions onto Reducto's status codes."""
    if isinstance(exc, ApiError):
        return exc
    text = str(exc) or exc.__class__.__name__
    cls = exc.__class__.__name__
    low = text.lower()
    if cls == "IntakeError":
        if "password" in low:
            return ApiError(442, "DOCUMENT_PASSWORD_PROTECTED",
                            "Document is password protected or the password is wrong. "
                            "Pass settings.document_password.", "password_protected")
        if "not supported" in low:
            return ApiError(415, "DOCUMENT_UNSUPPORTED", text, "file_conversion_error")
        if "not found" in low:
            return bad_request(text, "NOT_FOUND", "invalid_input")
        return ApiError(415, "DOCUMENT_CORRUPT", text, "pdf_handling_error")
    if isinstance(exc, NotImplementedError):
        return not_implemented(text if str(exc) else "This operation is not implemented in this build.")
    if cls in ("ReaderError", "LayoutError"):
        return ApiError(502, "LLM_PROVIDER_ERROR", text, "llm_provider_error")
    if cls in ("SchemaError", "ValidationError") and exc.__class__.__module__.startswith("jsonschema"):
        return ApiError(422, "INVALID_SCHEMA", f"Invalid JSON schema: {getattr(exc, 'message', text)}", "invalid_schema")
    if isinstance(exc, TimeoutError):
        return ApiError(504, "TIMEOUT", text, "timeout")
    return internal(f"{cls}: {text}")
