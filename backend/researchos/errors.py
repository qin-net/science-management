"""Structured API errors: every failure reaches the client as {"error": {...}}."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("researchos.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(what: str = "resource") -> ApiError:
    # Also used for resources the caller may not see, so existence never leaks.
    return ApiError(404, "not_found", f"{what} not found")


def error_body(code: str, message: str, request_id: str, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, _request_id(request), exc.details),
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only: echoing `input` could reflect passwords.
        fields = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            error_body(
                "validation_error", "request is invalid", _request_id(request), {"fields": fields}
            ),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail), _request_id(request)), status_code=exc.status_code
        )

    @app.exception_handler(OperationalError)
    async def _database_down(request: Request, exc: OperationalError) -> JSONResponse:
        # The driver message can contain the connection string, so it is never forwarded.
        log.error(
            "database unavailable: %s",
            type(exc.orig).__name__,
            extra={"request_id": _request_id(request)},
        )
        return JSONResponse(
            error_body("database_unavailable", "database is unavailable", _request_id(request)),
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        log.exception("unhandled error", extra={"request_id": request_id})
        return JSONResponse(
            error_body("internal_error", "internal server error", request_id),
            status_code=500,
            # This handler runs outside RequestContext, so that middleware cannot add it.
            headers={"X-Request-ID": request_id},
        )
