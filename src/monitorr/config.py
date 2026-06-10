from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    # The loops sleep these intervals between iterations: a zero/negative value would degenerate
    # into a hot loop hammering Plex/Sonarr, so fail fast at startup instead (ge validators).
    # sync_interval/full_sync_interval accept 0 as their documented "disabled" value.
    plex_poll_interval: int = Field(default=30, ge=1)
    grace_sweep_interval: int = Field(default=3600, ge=1)
    sync_interval: int = Field(default=21600, ge=0)
    # Rolling floor (seconds since the last FULL sync) that forces a full reconciliation as a safety
    # net; the periodic timer is otherwise incremental. 30 days by default; 0 disables the floor
    # (full only on connection of both deps, manual "Sync now", or when the history sweep is blind).
    full_sync_interval: int = Field(default=2592000, ge=0)
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
