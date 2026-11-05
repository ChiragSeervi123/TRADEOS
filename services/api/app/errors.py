"""Standard error envelope: {"error": {"code", "message", "details", "request_id"}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("tradeos")


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def not_found(entity: str = "Resource") -> AppError:
    return AppError("NOT_FOUND", f"{entity} not found.", 404)


def forbidden(message: str = "You do not have permission to perform this action.") -> AppError:
    return AppError("FORBIDDEN", message, 403)


def conflict(code: str, message: str, details: Any = None) -> AppError:
    return AppError(code, message, 409, details)


def _envelope(request: Request, code: str, message: str, status: int, details: Any = None):
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details, "request_id": rid}},
        headers={"X-Request-ID": rid} if rid else None,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _envelope(request, exc.code, exc.message, exc.status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", []) if p != "body"), "message": e.get("msg")}
            for e in exc.errors()
        ]
        return _envelope(request, "VALIDATION_ERROR", "The request is invalid.", 422, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        return _envelope(request, code, str(exc.detail), exc.status_code)

    @app.exception_handler(StaleDataError)
    async def _stale(request: Request, exc: StaleDataError):
        return _envelope(
            request, "CONCURRENT_MODIFICATION", "The record was modified by someone else. Reload and retry.", 409
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", None)})
        return _envelope(request, "INTERNAL_ERROR", "An unexpected error occurred.", 500)
