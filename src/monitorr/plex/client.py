"""Plex client: server discovery, sessions and correlation to TVDB.

See .claude/plex.md → "Server discovery", "Detection" and "Correlation".
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from monitorr import constants, store

logger = logging.getLogger(__name__)

_RESOURCES_URL = "https://plex.tv/api/v2/resources"
_WEBHOOKS_URL = "https://plex.tv/api/v2/user/webhooks"
_TIMEOUT = 30.0
_TVDB_GUID = re.compile(r"tvdb://(\d+)")


class PlexConnection(BaseModel):
    uri: str
    local: bool = False
    relay: bool = False


class PlexServer(BaseModel):
    name: str
    client_identifier: str
    access_token: str
    connections: list[PlexConnection]


class PlexSession(BaseModel):
    grandparent_title: str
    grandparent_rating_key: str
    season: int
    episode: int
    view_offset: int
    duration: int
    session_key: str
    user: str

    @property
    def progress(self) -> float:
        return self.view_offset / self.duration if self.duration > 0 else 0.0


class ShowRef(BaseModel):
    rating_key: str
    title: str
    tvdb_id: int | None


class WatchedEpisode(BaseModel):
    season: int
    episode: int
    viewed_at: str


def _headers(token: str, client_id: str) -> dict[str, str]:
    return {
        "X-Plex-Token": token,
        "X-Plex-Client-Identifier": client_id,
        "Accept": "application/json",
    }


async def get_server() -> tuple[str, str] | None:
    uri = await store.get_setting(constants.PLEX_SERVER_URI)
    token = await store.get_setting(constants.PLEX_SERVER_TOKEN)
    if not uri or not token:
        return None
    return uri, token


async def discover_servers(account_token: str, client_id: str) -> list[PlexServer]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            _RESOURCES_URL,
            params={"includeHttps": "1", "includeRelay": "1"},
            headers=_headers(account_token, client_id),
        )
        response.raise_for_status()
        servers: list[PlexServer] = []
        for resource in response.json():
            if "server" not in str(resource.get("provides", "")):
                continue
            connections = [
                PlexConnection(
                    uri=str(conn["uri"]),
                    local=bool(conn.get("local", False)),
                    relay=bool(conn.get("relay", False)),
                )
                for conn in resource.get("connections", [])
            ]
            servers.append(
                PlexServer(
                    name=str(resource.get("name", "Plex")),
                    client_identifier=str(resource["clientIdentifier"]),
                    access_token=str(resource["accessToken"]),
                    connections=connections,
                )
            )
        return servers


def _webhook_headers(account_token: str, client_id: str) -> dict[str, str]:
    return {**_headers(account_token, client_id), "X-Plex-Product": constants.PLEX_PRODUCT}


async def list_account_webhooks(account_token: str, client_id: str) -> list[str]:
    """The account's configured webhook URLs (account-level, shared with other integrations)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            _WEBHOOKS_URL, headers=_webhook_headers(account_token, client_id)
        )
        response.raise_for_status()
        data = response.json()
    urls: list[str] = []
    for item in data if isinstance(data, list) else []:
        url = item.get("url") if isinstance(item, dict) else item
        if url:
            urls.append(str(url))
    return urls


async def set_account_webhooks(account_token: str, client_id: str, urls: list[str]) -> None:
    """Replaces the whole account webhook list (POST is destructive; pass the full set)."""
    data = {"urls[]": urls} if urls else {"urls": ""}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _WEBHOOKS_URL, data=data, headers=_webhook_headers(account_token, client_id)
        )
        response.raise_for_status()


def choose_connection(connections: list[PlexConnection]) -> str | None:
    """Prefer direct local, then direct remote, then relay."""
    local = [c for c in connections if c.local and not c.relay]
    direct = [c for c in connections if not c.relay]
    for candidates in (local, direct, connections):
        if candidates:
            return candidates[0].uri
    return None


def _metadata(data: Any) -> list[dict[str, Any]]:
    container = data.get("MediaContainer", {})
    items = container.get("Metadata", [])
    return list(items)


def _directory(data: Any) -> list[dict[str, Any]]:
    container = data.get("MediaContainer", {})
    items = container.get("Directory", [])
    return list(items)


def _tvdb_from_guids(item: dict[str, Any]) -> int | None:
    for guid in item.get("Guid", []):
        match = _TVDB_GUID.search(str(guid.get("id", "")))
        if match:
            return int(match.group(1))
    return None


async def get_sessions(server_uri: str, token: str, client_id: str) -> list[PlexSession]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{server_uri}/status/sessions", headers=_headers(token, client_id)
        )
        response.raise_for_status()
        sessions: list[PlexSession] = []
        for item in _metadata(response.json()):
            if item.get("type") != "episode":
                continue
            user = item.get("User", {})
            sessions.append(
                PlexSession(
                    grandparent_title=str(item.get("grandparentTitle", "")),
                    grandparent_rating_key=str(item.get("grandparentRatingKey", "")),
                    season=int(item.get("parentIndex", 0)),
                    episode=int(item.get("index", 0)),
                    view_offset=int(item.get("viewOffset", 0)),
                    duration=int(item.get("duration", 0)),
                    session_key=str(item.get("sessionKey", "")),
                    user=str(user.get("title", "")),
                )
            )
        return sessions


async def resolve_tvdb_id(
    server_uri: str, token: str, client_id: str, grandparent_rating_key: str
) -> int | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{server_uri}/library/metadata/{grandparent_rating_key}",
            params={"includeGuids": "1"},
            headers=_headers(token, client_id),
        )
        response.raise_for_status()
        metadata = _metadata(response.json())
        if not metadata:
            return None
        return _tvdb_from_guids(metadata[0])


async def list_show_libraries(server_uri: str, token: str, client_id: str) -> list[str]:
    """Keys of the show-type sections (`/library/sections`)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{server_uri}/library/sections", headers=_headers(token, client_id)
        )
        response.raise_for_status()
        return [
            str(section["key"])
            for section in _directory(response.json())
            if section.get("type") == "show"
        ]


async def list_shows(
    server_uri: str, token: str, client_id: str, section_key: str
) -> list[ShowRef]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{server_uri}/library/sections/{section_key}/all",
            params={"type": "2", "includeGuids": "1"},
            headers=_headers(token, client_id),
        )
        response.raise_for_status()
        return [
            ShowRef(
                rating_key=str(item.get("ratingKey", "")),
                title=str(item.get("title", "")),
                tvdb_id=_tvdb_from_guids(item),
            )
            for item in _metadata(response.json())
        ]


async def get_watched_episodes(
    server_uri: str, token: str, client_id: str, show_rating_key: str
) -> list[WatchedEpisode]:
    """Episodes with `viewCount>0` of a show (`/library/metadata/{key}/allLeaves`)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{server_uri}/library/metadata/{show_rating_key}/allLeaves",
            headers=_headers(token, client_id),
        )
        response.raise_for_status()
        watched: list[WatchedEpisode] = []
        for item in _metadata(response.json()):
            if int(item.get("viewCount", 0)) <= 0:
                continue
            last_viewed = item.get("lastViewedAt")
            viewed_at = (
                datetime.fromtimestamp(int(last_viewed), tz=UTC).isoformat()
                if last_viewed
                else datetime.now(UTC).isoformat()
            )
            watched.append(
                WatchedEpisode(
                    season=int(item.get("parentIndex", 0)),
                    episode=int(item.get("index", 0)),
                    viewed_at=viewed_at,
                )
            )
        logger.debug(
            "allLeaves rating_key=%s watched=%d keys=%s",
            show_rating_key,
            len(watched),
            sorted((w.season, w.episode) for w in watched),
        )
        return watched


def normalize_title(title: str) -> str:
    """Join key for correlating Plex play history to a show. Both sides come from the same Plex
    server (history `grandparentTitle` ↔ library `ShowRef.title`), so a casefold+strip suffices and
    is **stable across a remove/re-add** (which changes ratingKeys but not the display title)."""
    return title.strip().casefold()


async def get_watch_history_by_show(
    server_uri: str, token: str, client_id: str, since: str | None = None
) -> dict[str, list[WatchedEpisode]]:
    """All episode plays from Plex's global play history, grouped by normalized show title.

    Unlike `allLeaves`, the play history **persists after the files are deleted**, catching
    back-catalog viewing whose episodes left the library (the reason `allLeaves` alone anchors too
    early). It must NOT be scoped with `metadataItemID=<show ratingKey>`: that filter only matches
    history whose metadata items still resolve under the show's **current** ratingKey, so a series
    **removed and re-added** in Plex (new ratingKey) loses all its history and the anchor falls back
    to whatever is still on disk — re-downloading already-watched episodes. Sweeping the whole
    history once and correlating by `grandparentTitle` (stable across re-add) fixes that and is one
    paginated call per sync instead of one per show. Keeps the most recent viewed_at per episode.

    With `since` (ISO-8601 UTC watermark), the sweep is **incremental**: rows come `viewedAt:desc`,
    so it stops at the first play older than `since` (every later row is older too) and returns only
    the new tail. The caller passes the previous max viewed_at minus a small overlap; re-recording
    an already-seen play is idempotent, so the overlap is safe.
    """
    latest: dict[tuple[str, int, int], str] = {}
    start = 0
    page_size = 1000
    pages = 0
    rows = 0
    stop = False
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while not stop:
            response = await client.get(
                f"{server_uri}/status/sessions/history/all",
                params={
                    "sort": "viewedAt:desc",
                    "X-Plex-Container-Start": start,
                    "X-Plex-Container-Size": page_size,
                },
                headers=_headers(token, client_id),
            )
            response.raise_for_status()
            items = _metadata(response.json())
            pages += 1
            for item in items:
                viewed = item.get("viewedAt")
                viewed_at = (
                    datetime.fromtimestamp(int(viewed), tz=UTC).isoformat()
                    if viewed
                    else datetime.now(UTC).isoformat()
                )
                # Newest-first: the first row older than the watermark ends the whole sweep.
                if since is not None and viewed_at < since:
                    stop = True
                    break
                if item.get("type") != "episode":
                    continue
                title = normalize_title(str(item.get("grandparentTitle", "")))
                if not title:
                    continue
                rows += 1
                key = (title, int(item.get("parentIndex", 0)), int(item.get("index", 0)))
                if key not in latest or viewed_at > latest[key]:
                    latest[key] = viewed_at
            if len(items) < page_size:
                break
            start += page_size
    by_show: dict[str, list[WatchedEpisode]] = {}
    for (title, season, episode), viewed_at in latest.items():
        by_show.setdefault(title, []).append(
            WatchedEpisode(season=season, episode=episode, viewed_at=viewed_at)
        )
    logger.debug(
        "history sweep: pages=%d episode_rows=%d shows=%d per_show=%s",
        pages,
        rows,
        len(by_show),
        {t: sorted((w.season, w.episode) for w in eps) for t, eps in by_show.items()},
    )
    return by_show
