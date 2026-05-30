"""Optional `media.scrobble` webhook (Plex Pass). See .claude/plex.md → "Webhook".

Only the media.scrobble event is processed; the payload arrives as multipart with a JSON field.
The protecting secret is auto-generated and stored in SQLite (surfaced in the Settings UI);
`MONITORR_WEBHOOK_SECRET` is an optional override to pin the same secret across instances.
"""

import secrets
from typing import Any

from pydantic import BaseModel

from monitorr import constants, store
from monitorr.config import get_settings


class ScrobbleEvent(BaseModel):
    grandparent_rating_key: str
    season: int
    episode: int
    user: str


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
        user=str(payload.get("Account", {}).get("title", "")),
    )


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(24)


async def get_or_create_webhook_secret() -> str:
    """The secret protecting the webhook URL.

    `MONITORR_WEBHOOK_SECRET` wins (infra override, not persisted); otherwise it's read from
    SQLite, generating and storing one on first access so the feature works out of the box.
    """
    override = get_settings().webhook_secret
    if override:
        return override
    stored = await store.get_setting(constants.WEBHOOK_SECRET)
    if stored:
        return stored
    new = generate_webhook_secret()
    await store.set_setting(constants.WEBHOOK_SECRET, new)
    return new


async def regenerate_webhook_secret() -> str:
    """Generates a new secret and persists it, invalidating the previous URL."""
    new = generate_webhook_secret()
    await store.set_setting(constants.WEBHOOK_SECRET, new)
    return new
