"""Webhook opcional `media.scrobble` (Plex Pass). Ver .claude/plex.md → "Webhook".

Solo se procesa el evento media.scrobble; el payload llega como multipart con un campo JSON.
"""

from typing import Any


def parse_scrobble(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extrae serie/temporada/episodio del payload si event == media.scrobble, si no None."""
    raise NotImplementedError  # TODO(plex-webhook)
