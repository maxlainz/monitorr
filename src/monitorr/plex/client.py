"""Plex client: server discovery, sessions and correlation to TVDB.

See .claude/plex.md → "Server discovery", "Detection" and "Correlation".
"""

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from monitorr import constants, store

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
        return watched


async def get_watch_history(
    server_uri: str, token: str, client_id: str, show_rating_key: str
) -> list[WatchedEpisode]:
    """Watched episodes of a show from Plex's play history (`/status/sessions/history/all`).

    Unlike `allLeaves`, the play history **persists after the files are deleted**: Plex keeps it
    independently of the current library items, so this catches back-catalog viewing whose episodes
    are no longer in the library (the reason `allLeaves` alone anchors too early). Filtered to this
    show and paginated.
    """
    latest: dict[tuple[int, int], str] = {}
    start = 0
    page_size = 500
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            response = await client.get(
                f"{server_uri}/status/sessions/history/all",
                params={
                    "metadataItemID": show_rating_key,
                    "sort": "viewedAt:desc",
                    "X-Plex-Container-Start": start,
                    "X-Plex-Container-Size": page_size,
                },
                headers=_headers(token, client_id),
            )
            response.raise_for_status()
            items = _metadata(response.json())
            for item in items:
                if item.get("type") != "episode":
                    continue
                # metadataItemID should already scope to this show; double-check when present, so a
                # server that ignores the filter doesn't leak other shows' history in.
                grandparent = item.get("grandparentRatingKey")
                if grandparent is not None and str(grandparent) != str(show_rating_key):
                    continue
                viewed = item.get("viewedAt")
                viewed_at = (
                    datetime.fromtimestamp(int(viewed), tz=UTC).isoformat()
                    if viewed
                    else datetime.now(UTC).isoformat()
                )
                key = (int(item.get("parentIndex", 0)), int(item.get("index", 0)))
                if key not in latest or viewed_at > latest[key]:
                    latest[key] = viewed_at
            if len(items) < page_size:
                break
            start += page_size
    return [WatchedEpisode(season=s, episode=e, viewed_at=v) for (s, e), v in latest.items()]
