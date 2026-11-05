import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import db as dbmod
from app.config import get_settings
from app.errors import install_error_handlers
from app.routers import ROUTERS


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("request_id", "method", "path", "status", "duration_ms", "job_id"):
            v = getattr(record, k, None)
            if v is not None:
                base[k] = v
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base)


def _configure_logging():
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger("tradeos")
    root.handlers = [h]
    root.setLevel(logging.INFO)
    root.propagate = False


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if get_settings().auto_create_schema:
        dbmod.create_schema()
    yield


def create_app() -> FastAPI:
    _configure_logging()
    s = get_settings()
    app = FastAPI(
        lifespan=_lifespan,
        title="TRADEOS API",
        version="0.1.0",
        description="Trade operations and financial intelligence for FMCG distributors. "
        "All endpoints are tenant-scoped via the bearer token.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    log = logging.getLogger("tradeos.http")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        start = time.perf_counter()
        response = await call_next(request)
        dur = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if s.environment != "test":
            log.info(
                "request",
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": dur,
                },
            )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )
    install_error_handlers(app)

    for r in ROUTERS:
        app.include_router(r, prefix="/api/v1")

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        try:
            with dbmod.engine.connect() as c:
                c.execute(text("SELECT 1"))
            return {"status": "ok", "database": "ok"}
        except Exception:  # pragma: no cover
            from fastapi.responses import JSONResponse

            return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)

    return app


app = create_app()
