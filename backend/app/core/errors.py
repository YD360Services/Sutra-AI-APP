"""
Centralized Error Handling according to IETF RFC 7807 (Problem Details for HTTP APIs).
Guarantees a strict, typed API error contract for all client consumers.
"""

from typing import Any, Dict, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

from app.core.config import settings
from app.core.context import get_trace_id

logger = logging.getLogger("roundmate.errors")


class ProblemDetails(BaseModel):
    """
    Standard RFC 7807 Error Response Schema.
    """
    type: str = Field(default="about:blank", description="URI reference identifying the problem type")
    title: str = Field(..., description="Short, human-readable summary of the problem type")
    status: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation specific to this occurrence")
    instance: str = Field(..., description="URI reference identifying the specific occurrence of the problem")
    error_code: str = Field(..., description="Machine-readable application error code")
    trace_id: str = Field(default="", description="Correlation trace ID for distributed debugging")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    invalid_params: Optional[Dict[str, Any]] = Field(default=None, description="Details for validation errors")


class APIException(Exception):
    """Base application exception for all domain errors."""
    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        error_code: str = "INTERNAL_ERROR",
        invalid_params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ):
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.error_code = error_code
        self.invalid_params = invalid_params
        self.headers = headers or {}
        super().__init__(detail)


class ResourceNotFoundException(APIException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Resource Not Found",
            detail=f"{resource} identified by '{identifier}' was not found.",
            error_code="RESOURCE_NOT_FOUND",
            invalid_params={"resource": resource, "identifier": str(identifier)}
        )


class UnauthorizedException(APIException):
    def __init__(self, detail: str = "Authentication credentials are invalid or expired."):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail=detail,
            error_code="UNAUTHORIZED",
            headers={"WWW-Authenticate": "Bearer"}
        )


class ForbiddenException(APIException):
    def __init__(self, detail: str = "You do not have permission to access this resource."):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            title="Forbidden",
            detail=detail,
            error_code="FORBIDDEN"
        )


class RateLimitExceededException(APIException):
    def __init__(self, detail: str = "Rate limit exceeded. Please retry after the designated cooldown period.", retry_after: int = 60):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            title="Too Many Requests",
            detail=detail,
            error_code="RATE_LIMIT_EXCEEDED",
            headers={"Retry-After": str(retry_after)}
        )


def _build_cors_headers(request: Request, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/problem+json",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    # Only echo origins the API actually allows — reflecting any Origin with credentials
    # would let arbitrary sites read error responses with the user's cookies.
    origin = request.headers.get("origin")
    allowed_origins = settings.get_cors_origins()
    if origin and ("*" in allowed_origins or origin in allowed_origins):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    if extra_headers:
        headers.update(extra_headers)
    return headers


async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    """Handles custom domain APIException."""
    trace_id = getattr(request.state, "trace_id", None) or get_trace_id() or "unknown"
    problem = ProblemDetails(
        type=f"https://api.roundmate.ai/errors/{exc.error_code.lower()}",
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=request.url.path,
        error_code=exc.error_code,
        trace_id=trace_id,
        invalid_params=exc.invalid_params
    )
    headers = _build_cors_headers(request, exc.headers)
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        headers=headers
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Translates standard FastAPI/Starlette HTTPException into RFC 7807 Problem Details."""
    trace_id = getattr(request.state, "trace_id", None) or get_trace_id() or "unknown"
    detail_str = str(exc.detail) if exc.detail else "An HTTP error occurred."
    
    error_code = "HTTP_ERROR"
    if exc.status_code == 404:
        error_code = "NOT_FOUND"
    elif exc.status_code == 401:
        error_code = "UNAUTHORIZED"
    elif exc.status_code == 403:
        error_code = "FORBIDDEN"
    elif exc.status_code == 429:
        error_code = "RATE_LIMIT_EXCEEDED"

    problem = ProblemDetails(
        type=f"https://api.roundmate.ai/errors/{error_code.lower()}",
        title=detail_str if len(detail_str) < 50 else f"HTTP {exc.status_code}",
        status=exc.status_code,
        detail=detail_str,
        instance=request.url.path,
        error_code=error_code,
        trace_id=trace_id
    )
    headers = _build_cors_headers(request, getattr(exc, "headers", None))
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        headers=headers
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Transforms Pydantic validation errors into clean, structured RFC 7807 details."""
    trace_id = getattr(request.state, "trace_id", None) or get_trace_id() or "unknown"
    formatted_errors = {}
    for err in exc.errors():
        field_path = ".".join(str(loc) for loc in err.get("loc", []) if loc != "body")
        formatted_errors[field_path or "body"] = err.get("msg", "Invalid value")

    problem = ProblemDetails(
        type="https://api.roundmate.ai/errors/validation_failed",
        title="Request Validation Failed",
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="One or more fields in the request body, query, or path failed schema validation.",
        instance=request.url.path,
        error_code="VALIDATION_FAILED",
        trace_id=trace_id,
        invalid_params=formatted_errors
    )
    headers = _build_cors_headers(request)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=problem.model_dump(exclude_none=True),
        headers=headers
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all unhandled 500 error handler that protects against leaking internal tracebacks."""
    trace_id = getattr(request.state, "trace_id", None) or get_trace_id() or "unknown"
    logger.exception(f"[Unhandled Error] trace_id={trace_id} on {request.method} {request.url.path}: {exc}")
    
    problem = ProblemDetails(
        type="https://api.roundmate.ai/errors/internal_server_error",
        title="Internal Server Error",
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An internal server error occurred. Please contact support with the provided trace ID.",
        instance=request.url.path,
        error_code="INTERNAL_SERVER_ERROR",
        trace_id=trace_id
    )
    headers = _build_cors_headers(request)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=problem.model_dump(exclude_none=True),
        headers=headers
    )
