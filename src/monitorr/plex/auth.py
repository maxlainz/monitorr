"""Login with Plex (PIN/OAuth flow). See .claude/plex.md → "Linking"."""

import uuid
from urllib.parse import urlencode

import httpx

from monitorr.constants import PLEX_PRODUCT

_PINS_URL = "https://plex.tv/api/v2/pins"
_AUTH_URL = "https://app.plex.tv/auth#?"
_TIMEOUT = 30.0


def generate_client_id() -> str:
    return str(uuid.uuid4())


def _headers(client_id: str) -> dict[str, str]:
    return {
        "X-Plex-Product": PLEX_PRODUCT,
        "X-Plex-Client-Identifier": client_id,
        "Accept": "application/json",
    }


async def create_pin(client_id: str) -> tuple[int, str]:
    """POST plex.tv/api/v2/pins?strong=true → (pin_id, code)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _PINS_URL, params={"strong": "true"}, headers=_headers(client_id)
        )
        response.raise_for_status()
        data = response.json()
        return int(data["id"]), str(data["code"])


def build_auth_url(client_id: str, code: str, forward_url: str) -> str:
    """app.plex.tv/auth URL the user is sent to in order to authorize."""
    query = urlencode(
        {
            "clientID": client_id,
            "code": code,
            "context[device][product]": PLEX_PRODUCT,
            "forwardUrl": forward_url,
        }
    )
    return _AUTH_URL + query


async def poll_pin(pin_id: int, code: str, client_id: str) -> str | None:
    """GET plex.tv/api/v2/pins/{id} → authToken when the user authorizes, else None."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{_PINS_URL}/{pin_id}", params={"code": code}, headers=_headers(client_id)
        )
        response.raise_for_status()
        token = response.json().get("authToken")
        return str(token) if token else None
