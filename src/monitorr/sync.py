"""Sync of the watched state from Plex (reconciliation). See .claude/behavior.md.

Reads which episodes have been watched in each Plex show and applies the window for the last
watched, without waiting for a live playback. Inherits the engine's dry-run (with dry-run ON it
only previews). Covers shows already started at install, manual marks and offline viewing.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from monitorr import constants, store
from monitorr.engine import actions
from monitorr.engine.policy import effective_policy, get_dry_run
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
        logger.info("sync already in progress; skipping")
        return {"shows": 0, "matched": 0}
    async with _LOCK:
        return await _run()


async def _run() -> dict[str, int]:
    server = await plex.get_server()
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    cfg = await sonarr.get_config()
    if server is None or client_id is None or cfg is None:
        logger.warning("sync: Plex or Sonarr are not ready")
        return {"shows": 0, "matched": 0, "normalized": 0, "searched": 0}
    uri, token = server
    base_url, api_key = cfg
    dry_run = await get_dry_run()

    managed_series = await sonarr.list_series(base_url, api_key)
    managed = {s.tvdb_id for s in managed_series}

    # One global play-history sweep, correlated per show by title (see _watch_history): re-add-proof
    # and one call instead of one per show.
    history_by_show = await _watch_history(uri, token, client_id)

    shows = 0
    matched = 0
    for section in await plex.list_show_libraries(uri, token, client_id):
        for show in await plex.list_shows(uri, token, client_id, section):
            shows += 1
            if show.tvdb_id is None or show.tvdb_id not in managed:
                continue
            # A show with an error (404, timeout, nonexistent episode) must not abort the sync.
            try:
                library = await plex.get_watched_episodes(uri, token, client_id, show.rating_key)
                history = history_by_show.get(plex.normalize_title(show.title), [])
                watched = _merge_watches(library, history)
                if not watched:
                    continue
                for episode in watched:
                    await store.record_watch(
                        show.tvdb_id, episode.season, episode.episode, episode.viewed_at
                    )
                anchor = max(watched, key=lambda e: (e.season, e.episode))
                # The single most useful debug line: how each source contributed and where the
                # window anchors. A back-catalog miss shows here as an anchor below the real max.
                logger.info(
                    "sync show tvdb=%s title=%s rating_key=%s allLeaves=%d history=%d "
                    "merged_keys=%s anchor=S%02dE%02d",
                    show.tvdb_id,
                    show.title,
                    show.rating_key,
                    len(library),
                    len(history),
                    sorted((e.season, e.episode) for e in watched),
                    anchor.season,
                    anchor.episode,
                )
                await apply_window(show.tvdb_id, anchor.season, anchor.episode)
                matched += 1
            except Exception:
                logger.exception("error syncing tvdb=%s", show.tvdb_id)

    normalized = await _normalize_unwatched(base_url, api_key, managed_series, dry_run)
    searched = await _search_missing(base_url, api_key, managed_series, dry_run)

    summary = {
        "shows": shows,
        "matched": matched,
        "normalized": normalized,
        "searched": searched,
    }
    await store.set_setting(
        _LAST_SYNC, json.dumps({"at": datetime.now(UTC).isoformat(), **summary})
    )
    logger.info("sync completed: %s", summary)
    return summary


async def _watch_history(
    uri: str, token: str, client_id: str
) -> dict[str, list[plex.WatchedEpisode]]:
    """Global Plex play history grouped by normalized show title, resilient: a history-endpoint
    error (e.g. an old PMS without it) must not drop the library-based detection (`allLeaves`); it
    only degrades the back-catalog (re-add) recovery."""
    try:
        return await plex.get_watch_history_by_show(uri, token, client_id)
    except Exception:
        logger.warning("could not read Plex play history; back-catalog degraded", exc_info=True)
        return {}


def _merge_watches(*sources: list[plex.WatchedEpisode]) -> list[plex.WatchedEpisode]:
    """Union of watched episodes by (season, episode), keeping the most recent viewed_at.
    `allLeaves` catches manual marks still in the library; the play history catches viewing whose
    files were deleted (so the anchor is the real last watched, not just what is still on disk)."""
    latest: dict[tuple[int, int], str] = {}
    for source in sources:
        for ep in source:
            key = (ep.season, ep.episode)
            if key not in latest or ep.viewed_at > latest[key]:
                latest[key] = ep.viewed_at
    return [plex.WatchedEpisode(season=s, episode=e, viewed_at=v) for (s, e), v in latest.items()]


async def _normalize_unwatched(
    base_url: str, api_key: str, managed_series: list[sonarr.SonarrSeries], dry_run: bool
) -> int:
    """Set-and-forget: every managed show WITHOUT recorded viewing is reduced to pilot-only,
    preventing Sonarr from accumulating downloads of newly added shows. Those that do have viewing
    are handled by the window (not touched here). Respects override (enabled / auto_normalize) and
    dry-run."""
    normalized = 0
    for series in managed_series:
        policy, enabled = await effective_policy(series.tvdb_id)
        if not enabled or not policy.auto_normalize:
            continue
        if await store.get_watches(series.tvdb_id):
            # A show that should be window-managed must NOT land here; if it does, the watch
            # detection above missed it (back-catalog bug).
            logger.debug(
                "normalize skip tvdb=%s (%s): has recorded watches", series.tvdb_id, series.title
            )
            continue
        try:
            episodes = await sonarr.get_episodes(base_url, api_key, series.id)
            logger.debug("normalize tvdb=%s (%s) to pilot-only", series.tvdb_id, series.title)
            await actions.normalize_to_pilot(
                base_url, api_key, series.tvdb_id, series, episodes, policy.always_have, dry_run
            )
            normalized += 1
        except Exception:
            logger.exception("error normalizing tvdb=%s", series.tvdb_id)
    return normalized


async def _search_missing(
    base_url: str, api_key: str, managed_series: list[sonarr.SonarrSeries], dry_run: bool
) -> int:
    """Re-search Sonarr's Wanted/Missing on every sync: episodes that stay monitored, have
    already aired and still lack a file, excluding the ones already downloading (in the queue).
    Recovers from transient indexer outages where the search at monitor-time found nothing.
    Respects override (enabled / search_on_get) and dry-run."""
    queued = {item.episode_id for item in await sonarr.get_queue(base_url, api_key)}
    searched = 0
    for series in managed_series:
        policy, enabled = await effective_policy(series.tvdb_id)
        if not enabled or not policy.search_on_get:
            continue
        try:
            missing = [
                e.id
                for e in await sonarr.get_episodes(base_url, api_key, series.id)
                if e.monitored and not e.has_file and e.has_aired() and e.id not in queued
            ]
            if missing:
                await actions.search_episodes(base_url, api_key, missing, dry_run)
                searched += len(missing)
        except Exception:
            logger.exception("error re-searching missing tvdb=%s", series.tvdb_id)
    return searched
