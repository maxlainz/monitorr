"""Cliente Plex: descubrimiento de servidor, sesiones y correlación a TVDB.

Ver .claude/plex.md → "Descubrimiento del servidor", "Detección" y "Correlación".
"""

from typing import Any


async def discover_servers(account_token: str, client_id: str) -> list[dict[str, Any]]:
    """GET plex.tv/api/v2/resources → servidores con su accessToken y Connection[]."""
    raise NotImplementedError  # TODO(plex-discovery)


async def get_sessions(server_uri: str, token: str) -> list[dict[str, Any]]:
    """GET {server}/status/sessions → sesiones activas (episodio + viewOffset/duration)."""
    raise NotImplementedError  # TODO(plex-sessions)


async def resolve_tvdb_id(server_uri: str, token: str, grandparent_rating_key: str) -> int | None:
    """GET {server}/library/metadata/{key}?includeGuids=1 → tvdb id de la serie."""
    raise NotImplementedError  # TODO(plex-correlation)
