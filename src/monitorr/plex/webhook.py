"""Optional `media.scrobble` webhook (Plex Pass). See .claude/plex.md → "Webhook".

Only the media.scrobble event is processed; the payload arrives as multipart with a JSON field.
"""

from typing import Any

from pydantic import BaseModel


class ScrobbleEvent(BaseModel):
    grandparent_rating_key: str
    season: int
    episode: int


def parse_scrobble(payload: dict[str, Any]) -> ScrobbleEvent | None:
    """Returns the event if it's a media.scrobble of an episode, else None."""
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
