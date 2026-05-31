"""Sonarr client (API v3/v4). See .claude/sonarr.md for endpoints and gotchas."""

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from monitorr import constants, store

_TIMEOUT = 30.0


class SonarrSeason(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    season_number: int = Field(alias="seasonNumber")
    monitored: bool = False


class SonarrSeries(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    title: str
    tvdb_id: int = Field(alias="tvdbId")
    series_type: str = Field(alias="seriesType", default="standard")
    seasons: list[SonarrSeason] = Field(default_factory=list)


class SonarrEpisode(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    season_number: int = Field(alias="seasonNumber")
    episode_number: int = Field(alias="episodeNumber")
    absolute_episode_number: int | None = Field(alias="absoluteEpisodeNumber", default=None)
    title: str | None = None
    monitored: bool = False
    has_file: bool = Field(alias="hasFile", default=False)
    episode_file_id: int = Field(alias="episodeFileId", default=0)
    air_date_utc: str | None = Field(alias="airDateUtc", default=None)

    def has_aired(self) -> bool:
        if not self.air_date_utc:
            return False
        return datetime.fromisoformat(self.air_date_utc) <= datetime.now(UTC)


async def get_config() -> tuple[str, str] | None:
    base_url = await store.get_setting(constants.SONARR_URL)
    api_key = await store.get_setting(constants.SONARR_API_KEY)
    if not base_url or not api_key:
        return None
    return base_url.rstrip("/"), api_key


def _client(base_url: str, api_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{base_url.rstrip('/')}/api/v3",
        headers={"X-Api-Key": api_key},
        timeout=_TIMEOUT,
    )


async def system_status(base_url: str, api_key: str) -> str | None:
    """Sonarr version if the connection works, else None (for the 'test' button)."""
    try:
        async with _client(base_url, api_key) as client:
            response = await client.get("/system/status")
            response.raise_for_status()
            data = response.json()
            return str(data.get("version", "ok"))
    except (httpx.HTTPError, ValueError):
        return None


async def find_series_by_tvdb(base_url: str, api_key: str, tvdb_id: int) -> SonarrSeries | None:
    async with _client(base_url, api_key) as client:
        response = await client.get("/series", params={"tvdbId": tvdb_id})
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return SonarrSeries.model_validate(data[0])


async def list_series(base_url: str, api_key: str) -> list[SonarrSeries]:
    async with _client(base_url, api_key) as client:
        response = await client.get("/series")
        response.raise_for_status()
        return [SonarrSeries.model_validate(item) for item in response.json()]


async def get_episodes(base_url: str, api_key: str, series_id: int) -> list[SonarrEpisode]:
    async with _client(base_url, api_key) as client:
        response = await client.get("/episode", params={"seriesId": series_id})
        response.raise_for_status()
        return [SonarrEpisode.model_validate(item) for item in response.json()]


async def set_monitored(
    base_url: str, api_key: str, episode_ids: list[int], monitored: bool
) -> None:
    if not episode_ids:
        return
    async with _client(base_url, api_key) as client:
        response = await client.put(
            "/episode/monitor",
            json={"episodeIds": episode_ids, "monitored": monitored},
        )
        response.raise_for_status()


async def set_seasons_monitored(
    base_url: str, api_key: str, series_id: int, desired: dict[int, bool]
) -> None:
    """Sets `seasons[].monitored` of the series object (GET-modify-PUT, which is how Sonarr
    requires editing seasons). Idempotent: only rewrites if some flag changes. `desired` maps
    `seasonNumber → monitored`; seasons not listed are not touched."""
    if not desired:
        return
    async with _client(base_url, api_key) as client:
        response = await client.get(f"/series/{series_id}")
        response.raise_for_status()
        data = response.json()
        changed = False
        for season in data.get("seasons", []):
            number = season.get("seasonNumber")
            if number in desired and season.get("monitored") != desired[number]:
                season["monitored"] = desired[number]
                changed = True
        if not changed:
            return
        put = await client.put(f"/series/{series_id}", json=data)
        put.raise_for_status()


async def search_episodes(base_url: str, api_key: str, episode_ids: list[int]) -> None:
    if not episode_ids:
        return
    async with _client(base_url, api_key) as client:
        response = await client.post(
            "/command",
            json={"name": "EpisodeSearch", "episodeIds": episode_ids},
        )
        response.raise_for_status()


async def delete_episode_file(base_url: str, api_key: str, episode_file_id: int) -> None:
    async with _client(base_url, api_key) as client:
        response = await client.delete(f"/episodefile/{episode_file_id}")
        response.raise_for_status()


class QueueItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    episode_id: int = Field(alias="episodeId", default=0)


async def get_queue(base_url: str, api_key: str) -> list[QueueItem]:
    async with _client(base_url, api_key) as client:
        response = await client.get("/queue", params={"pageSize": 1000})
        response.raise_for_status()
        records = response.json().get("records", [])
        return [QueueItem.model_validate(item) for item in records]


async def delete_queue_item(
    base_url: str,
    api_key: str,
    queue_id: int,
    remove_from_client: bool = False,
    blocklist: bool = False,
) -> None:
    """Removes an item from the queue. With remove_from_client=False the torrent stays in the client
    (seeding until its ratio); Sonarr stops tracking it and doesn't import it."""
    async with _client(base_url, api_key) as client:
        response = await client.delete(
            f"/queue/{queue_id}",
            params={
                "removeFromClient": str(remove_from_client).lower(),
                "blocklist": str(blocklist).lower(),
            },
        )
        response.raise_for_status()
