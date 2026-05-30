from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de infraestructura, leída de env vars `MONITORR_*`.

    La configuración de la app (Sonarr, parámetros de ventana, grace, overrides) vive en
    SQLite y se edita por la Web UI, no aquí.
    """

    model_config = SettingsConfigDict(env_prefix="MONITORR_", env_file=".env", extra="ignore")

    config_dir: Path = Path("/config")
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    plex_poll_interval: int = 30
    grace_sweep_interval: int = 3600
    sync_interval: int = 21600
    sync_on_startup: bool = True
    webhook_secret: str = ""

    # Metadatos de build, inyectados en la imagen Docker (vacíos en dev).
    build_sha: str = ""
    build_date: str = ""

    @property
    def db_path(self) -> Path:
        return self.config_dir / "monitorr.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
