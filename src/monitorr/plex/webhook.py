"""Webhook opcional `media.scrobble` (Plex Pass). Ver .claude/plex.md → "Webhook".

Solo se procesa el evento media.scrobble; el payload llega como multipart con un campo JSON.
"""

from typing import Any

from pydantic import BaseModel


class ScrobbleEvent(BaseModel):
    grandparent_rating_key: str
    season: int
    episode: int


def parse_scrobble(payload: dict[str, Any]) -> ScrobbleEvent | None:
    """Devuelve el evento si es media.scrobble de un episodio, si no None."""
    if payload.get("event") != "media.scrobble":
        return None
    metadata = payload.get("Metadata", {})
    if metadata.get("type") != "episode":
        return None
    rating_key = metadata.get("grandparentRatingKey")
    if not rating_key:
        return None
    return ScrobbleEvent(
        grandparent_rating_key=str(rating_key),
        season=int(metadata.get("parentIndex", 0)),
        episode=int(metadata.get("index", 0)),
    )
