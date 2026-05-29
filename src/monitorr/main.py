import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from monitorr.config import get_settings
from monitorr.db import init_db
from monitorr.logging import configure_logging
from monitorr.web import mount_web

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_db(settings.db_path)
    logger.info("monitorr iniciado (db=%s)", settings.db_path)
    # TODO(plex): lanzar poller de /status/sessions como tarea asyncio (ver .claude/plex.md)
    # TODO(engine): lanzar barrido periódico de grace periods (ver .claude/behavior.md)
    yield
    # TODO: cancelar las tareas de fondo al apagar


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
