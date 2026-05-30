"""Poller de sesiones (mecanismo de detección principal). Ver .claude/plex.md.

Bucle asyncio lanzado en el lifespan: consulta /status/sessions cada `interval` s, detecta
"visto" (viewOffset/duration ≥ threshold, o sesión casi completa que desaparece) con debounce
por episodio y dispara el motor.
"""

import asyncio
import logging
from dataclasses import dataclass

from monitorr import constants, store
from monitorr.engine.policy import get_user_filter, get_watched_threshold
from monitorr.engine.window import apply_window
from monitorr.plex.client import get_server, get_sessions, resolve_tvdb_id

logger = logging.getLogger(__name__)

# Clave de debounce. Plex puede mantener el mismo sessionKey al auto-reproducir el siguiente
# episodio de un binge, así que la identidad del visionado incluye (temporada, episodio).
WatchKey = tuple[str, int, int]


@dataclass
class _PrevSession:
    """Lo mínimo para disparar si una sesión casi completa desaparece entre sondeos."""

    grandparent_rating_key: str
    grandparent_title: str
    season: int
    episode: int
    progress: float


async def process_watch(tvdb_id: int, season: int, episode: int) -> None:
    """Registra el visionado y recalcula la ventana. Compartido por poller y webhook."""
    await store.record_watch(tvdb_id, season, episode)
    await apply_window(tvdb_id, season, episode)


async def _poll_once(
    fired: set[WatchKey],
    unresolved: set[str],
    prev: dict[WatchKey, _PrevSession],
) -> None:
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

    current: dict[WatchKey, _PrevSession] = {}
    active_rating_keys: set[str] = set()
    for session in sessions:
        if user_filter and session.user not in user_filter:
            continue
        key = (session.session_key, session.season, session.episode)
        active_rating_keys.add(session.grandparent_rating_key)
        current[key] = _PrevSession(
            grandparent_rating_key=session.grandparent_rating_key,
            grandparent_title=session.grandparent_title,
            season=session.season,
            episode=session.episode,
            progress=session.progress,
        )
        if session.progress < threshold or key in fired:
            continue
        tvdb_id = await _resolve(uri, token, client_id, session.grandparent_rating_key, unresolved)
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
        fired.add(key)

    # Sesiones casi completas que desaparecen entre sondeos = vistas (ver behavior.md "Disparo").
    for key, snapshot in prev.items():
        if key in current or key in fired:
            continue
        if snapshot.progress < constants.NEAR_COMPLETE_PROGRESS:
            continue
        tvdb_id = await _resolve(uri, token, client_id, snapshot.grandparent_rating_key, unresolved)
        if tvdb_id is None:
            logger.warning("sin tvdb para %s; se omite", snapshot.grandparent_title)
            continue
        logger.info(
            "completado S%02dE%02d de %s (sesión cerrada, tvdb=%s)",
            snapshot.season,
            snapshot.episode,
            snapshot.grandparent_title,
            tvdb_id,
        )
        await process_watch(tvdb_id, snapshot.season, snapshot.episode)

    fired.intersection_update(current.keys())
    unresolved.intersection_update(active_rating_keys)
    prev.clear()
    prev.update(current)


async def _resolve(
    uri: str, token: str, client_id: str, rating_key: str, unresolved: set[str]
) -> int | None:
    """Resuelve el tvdb del show, evitando reintentar (y re-avisar) los ya conocidos sin tvdb."""
    if rating_key in unresolved:
        return None
    tvdb_id = await resolve_tvdb_id(uri, token, client_id, rating_key)
    if tvdb_id is None:
        unresolved.add(rating_key)
    return tvdb_id


async def poll_loop(interval: int) -> None:
    fired: set[WatchKey] = set()
    unresolved: set[str] = set()
    prev: dict[WatchKey, _PrevSession] = {}
    while True:
        try:
            await _poll_once(fired, unresolved, prev)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("error en el ciclo de polling de Plex")
        await asyncio.sleep(interval)
