from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from monitorr.web.routes import router

WEB_DIR = Path(__file__).parent


def mount_web(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(router)
