"""ASGI application: routers, request IDs, access log and error handling."""

import logging
import time
import uuid

from fastapi import Depends, FastAPI
from starlette.datastructures import MutableHeaders

from researchos import __version__
from researchos.api import auth, health, papers, projects, workspaces
from researchos.config import get_settings
from researchos.errors import install_error_handlers
from researchos.logs import configure_logging
from researchos.security import csrf_guard

log = logging.getLogger("researchos.access")


class RequestContext:
    """Assigns the request ID and writes the access log. Client-sent IDs are ignored so
    they cannot forge log correlation, and the query string is never logged."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status = {"code": 500}

        async def send_with_id(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status["code"],
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(title="ResearchOS", version=__version__)
    app.add_middleware(RequestContext)
    install_error_handlers(app)
    for module in (auth, workspaces, projects, papers):
        app.include_router(module.router, prefix="/api/v1", dependencies=[Depends(csrf_guard)])
    app.include_router(health.router)
    return app


app = create_app()
