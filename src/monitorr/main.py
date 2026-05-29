import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from monitorr.config import get_settings
from monitorr.db import init_db
from monitorr.engine.grace import sweep
from monitorr.logging import configure_logging
from monitorr.plex.poller import poll_loop
from monitorr.web import mount_web

logger = logging.getLogger(__name__)


async def _grace_loop(interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error en el barrido de grace periods")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_db(settings.db_path)
    logger.info("monitorr iniciado (db=%s)", settings.db_path)

    tasks = [
        asyncio.create_task(poll_loop(settings.plex_poll_interval)),
        asyncio.create_task(_grace_loop(settings.grace_sweep_interval)),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="monitorr", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    mount_web(app)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("monitorr.main:app", host=settings.host, port=settings.port)
