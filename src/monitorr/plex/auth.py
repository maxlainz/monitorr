"""Login with Plex (flujo PIN/OAuth). Ver .claude/plex.md → "Vinculación"."""


async def create_pin(client_id: str, product: str) -> tuple[int, str]:
    """POST plex.tv/api/v2/pins?strong=true → (pin_id, code)."""
    raise NotImplementedError  # TODO(plex-auth)


def build_auth_url(client_id: str, code: str, product: str, forward_url: str) -> str:
    """URL de app.plex.tv/auth a la que se envía al usuario para autorizar."""
    raise NotImplementedError  # TODO(plex-auth)


async def poll_pin(pin_id: int, code: str, client_id: str) -> str | None:
    """GET plex.tv/api/v2/pins/{id} → authToken cuando el usuario autoriza, si no None."""
    raise NotImplementedError  # TODO(plex-auth)
