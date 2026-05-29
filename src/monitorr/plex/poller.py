"""Poller de sesiones (mecanismo de detección principal). Ver .claude/plex.md.

Bucle asyncio lanzado en el lifespan: consulta /status/sessions cada `interval` s, detecta
"visto" (viewOffset/duration ≥ threshold) con debounce por sessionKey y dispara el motor.
"""

import asyncio
import logging

from monitorr import constants, store
from monitorr.engine.policy import get_user_filter, get_watched_threshold
from monitorr.engine.window import apply_window
from monitorr.plex.client import get_server, get_sessions, resolve_tvdb_id

logger = logging.getLogger(__name__)


async def process_watch(tvdb_id: int, season: int, episode: int) -> None:
    """Registra el visionado y recalcula la ventana. Compartido por poller y webhook."""
    await store.record_watch(tvdb_id, season, episode)
    await apply_window(tvdb_id, season, episode)


async def _poll_once(fired: set[str]) -> None:
    server = await get_server()
    if server is None:
        return
    uri, token = server
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if client_id is None:
        return

    threshold = await get_watched_threshold()
    user_filter = await get_user_filter()
    sessions = await get_sessions(uri, token, client_id)

    active: set[str] = set()
    for session in sessions:
        active.add(session.session_key)
        if user_filter and session.user not in user_filter:
            continue
        if session.progress < threshold:
            continue
        if session.session_key in fired:
            continue
        tvdb_id = await resolve_tvdb_id(uri, token, client_id, session.grandparent_rating_key)
        if tvdb_id is None:
            logger.warning("sin tvdb para %s; se omite", session.grandparent_title)
            continue
        logger.info(
            "visto S%02dE%02d de %s (tvdb=%s)",
            session.season,
            session.episode,
            session.grandparent_title,
            tvdb_id,
        )
        await process_watch(tvdb_id, session.season, session.episode)
        fired.add(session.session_key)

    fired.intersection_update(active)


async def poll_loop(interval: int) -> None:
    fired: set[str] = set()
    while True:
        try:
            await _poll_once(fired)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error en el ciclo de polling de Plex")
        await asyncio.sleep(interval)
