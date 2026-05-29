"""Cliente Plex: descubrimiento de servidor, sesiones y correlación a TVDB.

Ver .claude/plex.md → "Descubrimiento del servidor", "Detección" y "Correlación".
"""

import re
from typing import Any

import httpx
from pydantic import BaseModel

from monitorr import constants, store

_RESOURCES_URL = "https://plex.tv/api/v2/resources"
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


def choose_connection(connections: list[PlexConnection]) -> str | None:
    """Preferir local directa, luego remota directa, luego relay."""
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
        for guid in metadata[0].get("Guid", []):
            match = _TVDB_GUID.search(str(guid.get("id", "")))
            if match:
                return int(match.group(1))
        return None
