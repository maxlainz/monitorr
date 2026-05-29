"""Sincronización del estado visto de Plex (reconciliación). Ver .claude/behavior.md.

Lee qué episodios se han visto en cada serie de Plex y aplica la ventana para el último visto,
sin esperar a una reproducción en vivo. Hereda el dry-run del motor (con dry-run ON solo
previsualiza). Cubre series ya empezadas al instalar, marcados a mano y visionados offline.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from monitorr import constants, store
from monitorr.engine.window import apply_window
from monitorr.plex import client as plex
from monitorr.sonarr import client as sonarr

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()
_LAST_SYNC = "last_sync"


def is_running() -> bool:
    return _LOCK.locked()


async def get_last_sync() -> dict[str, Any] | None:
    raw = await store.get_setting(_LAST_SYNC)
    if not raw:
        return None
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


async def run_sync() -> dict[str, int]:
    if _LOCK.locked():
        logger.info("sync ya en curso; se omite")
        return {"shows": 0, "matched": 0}
    async with _LOCK:
        return await _run()


async def _run() -> dict[str, int]:
    server = await plex.get_server()
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    cfg = await sonarr.get_config()
    if server is None or client_id is None or cfg is None:
        logger.warning("sync: Plex o Sonarr no están listos")
        return {"shows": 0, "matched": 0}
    uri, token = server
    base_url, api_key = cfg

    managed = {s.tvdb_id for s in await sonarr.list_series(base_url, api_key)}

    shows = 0
    matched = 0
    for section in await plex.list_show_libraries(uri, token, client_id):
        for show in await plex.list_shows(uri, token, client_id, section):
            shows += 1
            if show.tvdb_id is None or show.tvdb_id not in managed:
                continue
            # Una serie con error (404, timeout, episodio inexistente) no debe abortar la sync.
            try:
                watched = await plex.get_watched_episodes(uri, token, client_id, show.rating_key)
                if not watched:
                    continue
                for episode in watched:
                    await store.record_watch(
                        show.tvdb_id, episode.season, episode.episode, episode.viewed_at
                    )
                anchor = max(watched, key=lambda e: (e.season, e.episode))
                await apply_window(show.tvdb_id, anchor.season, anchor.episode)
                matched += 1
            except Exception:
                logger.exception("error sincronizando tvdb=%s", show.tvdb_id)

    summary = {"shows": shows, "matched": matched}
    await store.set_setting(
        _LAST_SYNC, json.dumps({"at": datetime.now(UTC).isoformat(), **summary})
    )
    logger.info("sync completada: %s", summary)
    return summary
