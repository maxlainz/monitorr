"""Cliente Sonarr (API v3/v4). Ver .claude/sonarr.md para endpoints y gotchas."""

from typing import Any


async def find_series_by_tvdb(base_url: str, api_key: str, tvdb_id: int) -> dict[str, Any] | None:
    """GET /api/v3/series?tvdbId={id} → serie o None si no está en Sonarr."""
    raise NotImplementedError  # TODO(sonarr)


async def get_episodes(base_url: str, api_key: str, series_id: int) -> list[dict[str, Any]]:
    """GET /api/v3/episode?seriesId={id}."""
    raise NotImplementedError  # TODO(sonarr)


async def set_monitored(
    base_url: str, api_key: str, episode_ids: list[int], monitored: bool
) -> None:
    """PUT /api/v3/episode/monitor {episodeIds, monitored}."""
    raise NotImplementedError  # TODO(sonarr)


async def search_episodes(base_url: str, api_key: str, episode_ids: list[int]) -> None:
    """POST /api/v3/command {name: EpisodeSearch, episodeIds}. Monitorizar no descarga."""
    raise NotImplementedError  # TODO(sonarr)


async def list_episode_files(base_url: str, api_key: str, series_id: int) -> list[dict[str, Any]]:
    """GET /api/v3/episodefile?seriesId={id}."""
    raise NotImplementedError  # TODO(sonarr)


async def delete_episode_file(base_url: str, api_key: str, episode_file_id: int) -> None:
    """DELETE /api/v3/episodefile/{id} → borra el fichero físico del disco."""
    raise NotImplementedError  # TODO(sonarr)
