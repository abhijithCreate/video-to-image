"""Application entrypoint: FastAPI app serving both the UI and the JSON API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import jobs
from app.config import BASE_DIR, settings
from app.routes import router

logger = logging.getLogger("video_to_image")

SWEEP_INTERVAL_SECONDS = 300


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        removed = await asyncio.to_thread(jobs.purge_expired)
        if removed:
            logger.info("Removed %s expired job(s)", removed)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    jobs.root()
    await asyncio.to_thread(jobs.purge_expired)
    task = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    app = FastAPI(
        title="Video to Image",
        description="Extract images from video frames.",
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.mount(
        "/static", StaticFiles(directory=BASE_DIR / "static"), name="static"
    )
    app.include_router(router)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Never surface pydantic internals or server paths to the browser.
        return JSONResponse(
            {"error": "That request was not valid. Please check your options."},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            {"error": "Something went wrong while processing your video."},
            status_code=500,
        )

    return app


app = create_app()
