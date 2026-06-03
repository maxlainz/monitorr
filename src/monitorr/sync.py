"""Sync of the watched state from Plex (reconciliation). See .claude/behavior.md.

Reads which episodes have been watched in each Plex show and applies the window for the last
watched, without waiting for a live playback. Inherits the engine's dry-run (with dry-run ON it
only previews). Covers shows already started at install, manual marks and offline viewing.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from monitorr import constants, store
from monitorr.config import get_settings
from monitorr.engine import actions
from monitorr.engine.policy import effective_policy, get_dry_run
from monitorr.engine.window import apply_window
from monitorr.plex import client as plex
from monitorr.sonarr import client as sonarr

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()
_LAST_SYNC = "last_sync"
# Re-read a few minutes before the stored watermark so a play landing right at the previous sweep's
# boundary isn't missed; re-recording an already-seen play is idempotent.
_WATERMARK_OVERLAP = timedelta(minutes=5)


def is_running() -> bool:
    return _LOCK.locked()


async def get_last_sync() -> dict[str, Any] | None:
    raw = await store.get_setting(_LAST_SYNC)
    if not raw:
        return None
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


def _floor_elapsed(last_full: str | None) -> bool:
    """Whether the rolling full-scrape floor (`full_sync_interval` since the last FULL) has elapsed.
    Never run → due. `full_sync_interval <= 0` → no floor (full only by cause/manual)."""
    if last_full is None:
        return True
    interval = get_settings().full_sync_interval
    if interval <= 0:
        return False
    age = (datetime.now(UTC) - datetime.fromisoformat(last_full)).total_seconds()
    return age >= interval


async def full_sync_due() -> bool:
    """For the startup check: True when both deps are configured and a FULL is overdue (never ran or
    the rolling floor elapsed). The floor is a *rolling* comparison, not a fixed instant, so a
    downtime that crosses it triggers a full on the next boot — the window is never missed."""
    if await plex.get_server() is None or await sonarr.get_config() is None:
        return False
    return _floor_elapsed(await store.get_setting(constants.LAST_FULL_SYNC))


async def run_sync(force_full: bool = False) -> dict[str, int]:
    if _LOCK.locked():
        logger.info("sync already in progress; skipping")
        return {"shows": 0, "matched": 0}
    async with _LOCK:
        return await _run(force_full)


async def _run(force_full: bool) -> dict[str, int]:
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

    # Full vs incremental. FULL scans every show (allLeaves each managed one); INCREMENTAL only the
    # shows with new plays since the watermark. Full is entered by cause: forced (manual / dep
    # connection), no watermark yet (first ready), or the rolling floor elapsed (_floor_elapsed).
    watermark = await store.get_setting(constants.HISTORY_WATERMARK)
    full = (
        force_full
        or watermark is None
        or _floor_elapsed(await store.get_setting(constants.LAST_FULL_SYNC))
    )

    since = None if full or watermark is None else _since(watermark)
    history = await _watch_history(uri, token, client_id, since)
    if history is None:  # endpoint failed
        if not full:
            # Can't trust an empty delta when the source is down → reconcile fully this cycle.
            logger.info("Plex history unavailable; promoting incremental sync to full")
            full = True
        history = {}

    shows, matched = await _apply_watches(uri, token, client_id, history, managed, full)

    normalized = await _normalize_unwatched(base_url, api_key, managed_series, dry_run)
    searched = await _search_missing(base_url, api_key, managed_series, dry_run)

    await _persist_watermark(full, watermark, _history_max(history))

    summary = {
        "shows": shows,
        "matched": matched,
        "normalized": normalized,
        "searched": searched,
    }
    mode = "full" if full else "incremental"
    await store.set_setting(
        _LAST_SYNC, json.dumps({"at": datetime.now(UTC).isoformat(), "mode": mode, **summary})
    )
    logger.info("sync completed (%s): %s", mode, summary)
    return summary


async def _apply_watches(
    uri: str,
    token: str,
    client_id: str,
    history: dict[str, list[plex.WatchedEpisode]],
    managed: set[int],
    full: bool,
) -> tuple[int, int]:
    """Records the watched state and applies the window per show. Returns (shows_considered,
    matched). On an incremental cycle with no new plays there is nothing to do — skip listing the
    libraries entirely (the allLeaves-per-show scan is the cost we're avoiding)."""
    if not full and not history:
        return 0, 0
    refs: list[plex.ShowRef] = []
    for section in await plex.list_show_libraries(uri, token, client_id):
        refs.extend(await plex.list_shows(uri, token, client_id, section))
    targets = refs if full else [r for r in refs if plex.normalize_title(r.title) in history]

    shows = 0
    matched = 0
    for show in targets:
        shows += 1
        if show.tvdb_id is None or show.tvdb_id not in managed:
            continue
        # A show with an error (404, timeout, nonexistent episode) must not abort the sync.
        try:
            library = await plex.get_watched_episodes(uri, token, client_id, show.rating_key)
            episodes = history.get(plex.normalize_title(show.title), [])
            watched = _merge_watches(library, episodes)
            if not watched:
                continue
            for episode in watched:
                await store.record_watch(
                    show.tvdb_id, episode.season, episode.episode, episode.viewed_at
                )
            anchor = max(watched, key=lambda e: (e.season, e.episode))
            # The single most useful debug line: how each source contributed and where the window
            # anchors. A back-catalog miss shows here as an anchor below the real max.
            logger.info(
                "sync show tvdb=%s title=%s rating_key=%s allLeaves=%d history=%d "
                "merged_keys=%s anchor=S%02dE%02d",
                show.tvdb_id,
                show.title,
                show.rating_key,
                len(library),
                len(episodes),
                sorted((e.season, e.episode) for e in watched),
                anchor.season,
                anchor.episode,
            )
            await apply_window(show.tvdb_id, anchor.season, anchor.episode)
            matched += 1
        except Exception:
            logger.exception("error syncing tvdb=%s", show.tvdb_id)
    return shows, matched


def _since(watermark: str) -> str:
    return (datetime.fromisoformat(watermark) - _WATERMARK_OVERLAP).isoformat()


def _history_max(history: dict[str, list[plex.WatchedEpisode]]) -> str | None:
    """Newest viewed_at across the whole sweep (any show), to advance the watermark past everything
    seen — even plays of unmanaged shows, so the next incremental never re-fetches them."""
    newest: str | None = None
    for episodes in history.values():
        for episode in episodes:
            newest = episode.viewed_at if newest is None else max(newest, episode.viewed_at)
    return newest


async def _persist_watermark(full: bool, prev: str | None, new_max: str | None) -> None:
    if full:
        # After a full reconciliation everything up to now is captured: the incremental floor is the
        # newest play seen, or now if the history is empty. Stamp the full timestamp too.
        base = new_max or datetime.now(UTC).isoformat()
        watermark = base if prev is None else max(prev, base)
        await store.set_setting(constants.HISTORY_WATERMARK, watermark)
        await store.set_setting(constants.LAST_FULL_SYNC, datetime.now(UTC).isoformat())
    elif new_max is not None:
        await store.set_setting(constants.HISTORY_WATERMARK, max(prev or new_max, new_max))


async def _watch_history(
    uri: str, token: str, client_id: str, since: str | None
) -> dict[str, list[plex.WatchedEpisode]] | None:
    """Global Plex play history grouped by normalized show title (incremental when `since` is set).
    Returns None when the history endpoint fails (old PMS, history disabled): the caller degrades a
    full sweep to library-only detection, or promotes an incremental sweep to full (an empty delta
    from a dead endpoint must not be mistaken for "nothing new")."""
    try:
        return await plex.get_watch_history_by_show(uri, token, client_id, since)
    except Exception:
        logger.warning("could not read Plex play history; back-catalog degraded", exc_info=True)
        return None


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
