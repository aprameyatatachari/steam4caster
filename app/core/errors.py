"""Application errors and the consistent API error envelope."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import correlation_id_var, get_logger

logger = get_logger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details
        self.headers = headers


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Resource conflict."


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Request validation failed."


class AuthenticationError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    message = "Authentication is required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class PermissionDenied(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have access to this resource."


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests."


class UpstreamUnavailable(AppError):
    status_code = 503
    code = "UPSTREAM_UNAVAILABLE"
    message = "The price data provider is temporarily unavailable."


class FeatureUnavailable(AppError):
    status_code = 503
    code = "FEATURE_UNAVAILABLE"
    message = "This feature is not configured on the server."


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code, message=message, correlation_id=correlation_id_var.get(), details=details
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("application error", extra={"code": exc.code})
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
