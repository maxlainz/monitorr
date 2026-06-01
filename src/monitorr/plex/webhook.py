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
from monitorr.plex import client

# Marker identifying monitorr's own entries in the shared, account-level webhook list.
MONITORR_WEBHOOK_PATH = "/webhook/plex/"


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


# --- account-level registration in Plex (auto-configure the webhook) ---


def _is_monitorr(url: str) -> bool:
    return MONITORR_WEBHOOK_PATH in url


async def register_webhook(account_token: str, client_id: str, url: str) -> None:
    """Adds `url` to the account webhook list, replacing any previous monitorr entry.

    Account webhooks are shared with other integrations, so we keep everyone else's URLs and
    only swap out monitorr's own (a rotated secret/host is replaced, not duplicated).
    """
    others = [
        existing
        for existing in await client.list_account_webhooks(account_token, client_id)
        if not _is_monitorr(existing)
    ]
    await client.set_account_webhooks(account_token, client_id, [*others, url])


async def unregister_webhooks(account_token: str, client_id: str) -> None:
    """Removes monitorr's entries from the account webhook list, preserving the rest."""
    current = await client.list_account_webhooks(account_token, client_id)
    others = [url for url in current if not _is_monitorr(url)]
    if others != current:
        await client.set_account_webhooks(account_token, client_id, others)


async def resync_webhook(account_token: str, client_id: str, url: str) -> bool:
    """Replaces monitorr's entry with `url` only if one already exists (e.g. after a rotation).

    Leaves Plex untouched when monitorr isn't registered, so rotating the secret never
    silently opts a user into the webhook.
    """
    current = await client.list_account_webhooks(account_token, client_id)
    others = [u for u in current if not _is_monitorr(u)]
    if others == current:
        return False
    await client.set_account_webhooks(account_token, client_id, [*others, url])
    return True


async def is_webhook_registered(account_token: str, client_id: str, url: str) -> bool:
    return url in await client.list_account_webhooks(account_token, client_id)
