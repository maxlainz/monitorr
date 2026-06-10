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
from monitorr.engine.policy import effective_policy, get_dry_run, get_user_filter
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

    # One pooled connection per side for the whole cycle (avoids a TCP/TLS handshake per call). The
    # ContextVar propagates to every nested Plex/Sonarr call, including apply_window/actions.
    async with plex.pooled_session(), sonarr.pooled_session(base_url, api_key):
        managed_series = await sonarr.list_series(base_url, api_key)
        # Index once; reused as the "managed" set and to hand the SonarrSeries to apply_window (no
        # per-show find_series_by_tvdb).
        by_tvdb = {s.tvdb_id: s for s in managed_series}

        # Full vs incremental. FULL scans every show (allLeaves each managed one); INCREMENTAL only
        # the shows with new plays since the watermark. Full is entered by cause: forced (manual /
        # dep connection), no watermark yet (first ready), or the rolling floor elapsed.
        watermark = await store.get_setting(constants.HISTORY_WATERMARK)
        full = (
            force_full
            or watermark is None
            or _floor_elapsed(await store.get_setting(constants.LAST_FULL_SYNC))
        )

        # Sync-side user filter: history rows are filtered by accountID (resolved from the
        # filter's names); allLeaves only contributes when the owner is included, since its
        # viewCount reflects the server token's account (the owner).
        account_ids = await _account_filter(uri, token, client_id)
        include_library = account_ids is None or plex.OWNER_ACCOUNT_ID in account_ids

        since = None if full or watermark is None else _since(watermark)
        sweep = await _watch_history(uri, token, client_id, since, account_ids)
        history_failed = sweep is None
        if sweep is None:
            if not full:
                # Can't trust an empty delta when the source is down → reconcile fully this cycle.
                logger.info("Plex history unavailable; promoting incremental sync to full")
                full = True
            sweep = plex.HistorySweep({}, None)
        history, newest_seen = sweep

        shows, matched, searched_by_windows = await _apply_watches(
            uri, token, client_id, history, by_tvdb, full, include_library
        )

        normalized, searched = await _reconcile_managed(
            base_url, api_key, managed_series, dry_run, searched_by_windows
        )

        # A blind sweep must advance nothing: stamping the watermark at "now" would bury the plays
        # missed during the outage below the incremental floor, and stamping last_full would stop
        # the promote-to-full degradation for a whole floor interval. Leaving both untouched keeps
        # every cycle full (library-only reconciliation) until the history endpoint recovers.
        if not history_failed:
            await _persist_watermark(full, watermark, newest_seen)

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


async def _account_filter(uri: str, token: str, client_id: str) -> set[int] | None:
    """Local accountIDs matching the user filter, or None when no filtering applies: filter empty,
    or its names can't be resolved against the server's `/accounts` — then degrade to the historic
    unfiltered behavior (with a warning) rather than silently dropping every play."""
    names = await get_user_filter()
    if not names:
        return None
    try:
        accounts = await plex.get_accounts(uri, token, client_id)
    except Exception:
        logger.warning("could not list Plex accounts; user filter not applied to sync")
        return None
    wanted = {plex.normalize_title(name) for name in names}
    ids = {account_id for name, account_id in accounts.items() if name in wanted}
    unmatched = wanted - set(accounts)
    if unmatched:
        logger.warning("user filter names not found among Plex accounts: %s", sorted(unmatched))
    if not ids:
        logger.warning("no user filter name matched a Plex account; sync stays unfiltered")
        return None
    return ids


async def _apply_watches(
    uri: str,
    token: str,
    client_id: str,
    history: dict[str, list[plex.WatchedEpisode]],
    by_tvdb: dict[int, sonarr.SonarrSeries],
    full: bool,
    include_library: bool,
) -> tuple[int, int, set[int]]:
    """Records the watched state and applies the window per show. Returns (shows_considered,
    matched, episode ids the windows searched — so the re-search pass can skip them this cycle).
    On an incremental cycle with no new plays there is nothing to do — skip listing the
    libraries entirely (the allLeaves-per-show scan is the cost we're avoiding)."""
    searched_ids: set[int] = set()
    if not full and not history:
        return 0, 0, searched_ids
    refs: list[plex.ShowRef] = []
    for section in await plex.list_show_libraries(uri, token, client_id):
        refs.extend(await plex.list_shows(uri, token, client_id, section))
    targets = refs if full else [r for r in refs if plex.normalize_title(r.title) in history]

    shows = 0
    matched = 0
    for show in targets:
        shows += 1
        series = by_tvdb.get(show.tvdb_id) if show.tvdb_id is not None else None
        if series is None:
            continue
        # A show with an error (404, timeout, nonexistent episode) must not abort the sync.
        try:
            library = (
                await plex.get_watched_episodes(uri, token, client_id, show.rating_key)
                if include_library
                else []
            )
            episodes = history.get(plex.normalize_title(show.title), [])
            watched = _merge_watches(library, episodes)
            if not watched:
                continue
            for episode in watched:
                await store.record_watch(
                    series.tvdb_id, episode.season, episode.episode, episode.viewed_at
                )
            anchor = max(watched, key=lambda e: (e.season, e.episode))
            # The single most useful debug line: how each source contributed and where the window
            # anchors. A back-catalog miss shows here as an anchor below the real max.
            logger.info(
                "sync show tvdb=%s title=%s rating_key=%s allLeaves=%d history=%d "
                "merged_keys=%s anchor=S%02dE%02d",
                series.tvdb_id,
                show.title,
                show.rating_key,
                len(library),
                len(episodes),
                sorted((e.season, e.episode) for e in watched),
                anchor.season,
                anchor.episode,
            )
            # Pass the already-fetched series so the window skips its own find_series_by_tvdb.
            searched_ids |= await apply_window(
                series.tvdb_id, anchor.season, anchor.episode, series=series
            )
            matched += 1
        except Exception:
            logger.exception("error syncing tvdb=%s", show.tvdb_id)
    return shows, matched, searched_ids


def _since(watermark: str) -> str:
    return (datetime.fromisoformat(watermark) - _WATERMARK_OVERLAP).isoformat()


async def _persist_watermark(full: bool, prev: str | None, new_max: str | None) -> None:
    """`new_max` is the sweep's newest *scanned* viewedAt (pre-filter, any show — see
    HistorySweep), so the next incremental never re-fetches plays already seen."""
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
    uri: str, token: str, client_id: str, since: str | None, account_ids: set[int] | None
) -> plex.HistorySweep | None:
    """Global Plex play history grouped by normalized show title (incremental when `since` is set,
    filtered by accountID when the user filter resolves). Returns None when the history endpoint
    fails (old PMS, history disabled): the caller degrades a full sweep to library-only detection,
    or promotes an incremental sweep to full (an empty delta from a dead endpoint must not be
    mistaken for "nothing new")."""
    try:
        return await plex.get_watch_history_by_show(uri, token, client_id, since, account_ids)
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


async def _reconcile_managed(
    base_url: str,
    api_key: str,
    managed_series: list[sonarr.SonarrSeries],
    dry_run: bool,
    already_searched: set[int],
) -> tuple[int, int]:
    """One pass over the managed shows with a **single `get_episodes` each** (was two passes:
    normalize + re-search) and one shared `queue`. Returns (normalized, searched).

    - **Normalize** (set-and-forget): a show WITHOUT recorded viewing is reduced to pilot-only,
      preventing Sonarr from accumulating downloads of newly added shows. `normalize_to_pilot`
      already searches a missing pilot and leaves only it monitored, so a re-search pass on a
      just-normalized show would only (redundantly) hit that pilot — hence we skip it.
    - **Re-search** (the rest: watched shows, or normalize disabled): Sonarr's Wanted/Missing —
      episodes still monitored, already aired and without a file — searched again, excluding the
      ones already downloading **and the ones a window searched this same cycle**
      (`already_searched`: the queue snapshot below predates those grabs, so without the exclusion
      every missing GET-window episode got two EpisodeSearch commands per sync).
      Recovers from a transient indexer outage at monitor-time.

    Respects per-series override (enabled / auto_normalize / search_on_get) and dry-run."""
    queued = {item.episode_id for item in await sonarr.get_queue(base_url, api_key)}
    normalized = 0
    searched = 0
    for series in managed_series:
        policy, enabled = await effective_policy(series.tvdb_id)
        if not enabled:
            continue
        try:
            episodes = await sonarr.get_episodes(base_url, api_key, series.id)
            if policy.auto_normalize and not await store.get_watches(series.tvdb_id):
                logger.debug("normalize tvdb=%s (%s) to pilot-only", series.tvdb_id, series.title)
                await actions.normalize_to_pilot(
                    base_url, api_key, series.tvdb_id, series, episodes, policy.always_have, dry_run
                )
                normalized += 1
                continue
            if policy.search_on_get:
                missing = [
                    e.id
                    for e in episodes
                    if e.monitored
                    and not e.has_file
                    and e.has_aired()
                    and e.id not in queued
                    and e.id not in already_searched
                ]
                if missing:
                    await actions.search_episodes(base_url, api_key, missing, dry_run)
                    searched += len(missing)
        except Exception:
            logger.exception("error reconciling tvdb=%s", series.tvdb_id)
    return normalized, searched
