from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_config
from .events import get_logger, log_event, setup_logging

from .api import analytics_api, director_api, jobs_api, logs, media, settings_api, system, videos, youtube_api

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    cfg = get_config()
    if not cfg.admin_token:
        log.error("ADMIN_TOKEN is not set: every dashboard API call will be rejected (fail-closed)")
    if not cfg.secret_key:
        log.error("SECRET_KEY is not set: YouTube tokens cannot be stored")
    log_event("api", "backend started", version=system.VERSION)
    yield


app = FastAPI(title="YouTube AI Manager API", version=system.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_config().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log_event("api", f"unhandled error on {request.method} {request.url.path}: {exc}", level="ERROR")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


for module in (system, settings_api, logs, media, videos, youtube_api, director_api, analytics_api, jobs_api):
    app.include_router(module.router)
