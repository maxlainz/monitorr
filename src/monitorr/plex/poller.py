"""Session poller (primary detection mechanism). See .claude/plex.md.

Asyncio loop launched in the lifespan: queries /status/sessions every `interval` s, detects
"watched" (viewOffset/duration ≥ threshold, or an almost-complete session that disappears) with
per-episode debounce and triggers the engine.
"""

import asyncio
import logging
from dataclasses import dataclass

from monitorr import constants, store
from monitorr.engine.policy import get_user_filter, get_watched_threshold
from monitorr.engine.window import apply_window
from monitorr.plex.client import get_server, get_sessions, resolve_tvdb_id

logger = logging.getLogger(__name__)

# Debounce key. Plex can keep the same sessionKey when auto-playing the next
# episode of a binge, so the viewing identity includes (season, episode).
WatchKey = tuple[str, int, int]


@dataclass
class _PrevSession:
    """The minimum needed to trigger if an almost-complete session disappears between polls."""

    grandparent_rating_key: str
    grandparent_title: str
    season: int
    episode: int
    progress: float


async def process_watch(tvdb_id: int, season: int, episode: int) -> None:
    """Records the viewing and recomputes the window around the **furthest-watched** episode.

    The window anchors on the maximum recorded watch in airing order (including the one just
    played), not on the episode itself, so re-watching or filling an earlier gap of an already-
    watched show never slides the window backward and re-downloads episodes already seen.
    Consistent with the sync and the grace sweep. Shared by poller and webhook.
    """
    await store.record_watch(tvdb_id, season, episode)
    watches = await store.get_watches(tvdb_id)
    anchor = max(((w.season, w.episode) for w in watches), default=(season, episode))
    await apply_window(tvdb_id, *anchor)


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
            logger.warning("no tvdb for %s; skipping", session.grandparent_title)
            continue
        logger.info(
            "watched S%02dE%02d of %s (tvdb=%s)",
            session.season,
            session.episode,
            session.grandparent_title,
            tvdb_id,
        )
        await process_watch(tvdb_id, session.season, session.episode)
        fired.add(key)

    # Almost-complete sessions that disappear between polls = watched (see behavior.md "Trigger").
    for key, snapshot in prev.items():
        if key in current or key in fired:
            continue
        if snapshot.progress < constants.NEAR_COMPLETE_PROGRESS:
            continue
        tvdb_id = await _resolve(uri, token, client_id, snapshot.grandparent_rating_key, unresolved)
        if tvdb_id is None:
            logger.warning("no tvdb for %s; skipping", snapshot.grandparent_title)
            continue
        logger.info(
            "completed S%02dE%02d of %s (session closed, tvdb=%s)",
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
    """Resolves the show's tvdb, avoiding retrying (and re-warning) those already known
    without a tvdb."""
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
            logger.exception("error in the Plex polling loop")
        await asyncio.sleep(interval)
