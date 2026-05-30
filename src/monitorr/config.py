from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Infrastructure configuration, read from `MONITORR_*` env vars.

    The app configuration (Sonarr, window parameters, grace, overrides) lives in
    SQLite and is edited via the Web UI, not here.
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

    # Build metadata, injected into the Docker image (empty in dev).
    build_sha: str = ""
    build_date: str = ""

    @property
    def db_path(self) -> Path:
        return self.config_dir / "monitorr.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
