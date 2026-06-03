"""Window logic: GET (N ahead) / KEEP (N behind) / Always-Have.

See .claude/behavior.md. Triggered when detecting "show watched up to episode E".
"""

import logging

from monitorr import store
from monitorr.engine import actions
from monitorr.engine.policy import Policy, effective_policy, get_dry_run, matches_always_have
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

logger = logging.getLogger(__name__)


def _desired_seasons(series: SonarrSeries, anchor_season: int, policy: Policy) -> dict[int, bool]:
    """Season-level monitoring enforced by monitorr: by episodes, all off (100%
    per-episode control); by seasons, only those of the GET window on."""
    if policy.get_unit == "seasons":
        return {
            s.season_number: anchor_season <= s.season_number <= anchor_season + policy.get_count
            for s in series.seasons
        }
    return {s.season_number: False for s in series.seasons}


def _real_episodes(episodes: list[SonarrEpisode]) -> list[SonarrEpisode]:
    """ "Real" episodes (excludes S00 specials) in airing order."""
    real = [e for e in episodes if e.season_number >= 1]
    return sorted(real, key=lambda e: (e.season_number, e.episode_number))


def _select_ahead(real: list[SonarrEpisode], idx: int, policy: Policy) -> list[SonarrEpisode]:
    if policy.get_unit == "seasons":
        max_season = real[idx].season_number + policy.get_count
        return [e for e in real[idx + 1 :] if e.season_number <= max_season]
    return real[idx + 1 : idx + 1 + policy.get_count]


def _should_delete_behind(real: list[SonarrEpisode], idx: int, policy: Policy, i: int) -> bool:
    if policy.keep_unit == "seasons":
        min_keep_season = real[idx].season_number - (policy.keep_count - 1)
        return real[i].season_number < min_keep_season
    return i < idx - policy.keep_count + 1


def keep_protected_keys(
    real: list[SonarrEpisode], anchor_idx: int, policy: Policy
) -> set[tuple[int, int]]:
    """(season, episode) keys the KEEP window guarantees on disk: the anchor plus the episodes
    behind it that fall inside KEEP. Shared with the grace sweep so KEEP acts as a retention
    floor — the time-based trims only delete what already falls outside KEEP."""
    keys = {(real[anchor_idx].season_number, real[anchor_idx].episode_number)}
    for i in range(anchor_idx):
        if not _should_delete_behind(real, anchor_idx, policy, i):
            keys.add((real[i].season_number, real[i].episode_number))
    return keys


async def apply_window(tvdb_id: int, season: int, episode: int) -> None:
    policy, enabled = await effective_policy(tvdb_id)
    if not enabled:
        return
    cfg = await sonarr.get_config()
    if cfg is None:
        logger.warning("Sonarr not configured; skipping the window")
        return
    base_url, api_key = cfg
    dry_run = await get_dry_run()

    series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
    if series is None:
        logger.warning("show tvdb=%s is not in Sonarr", tvdb_id)
        return

    # Enforce season monitoring before touching episodes (cascade-proof order):
    # if Sonarr propagated the change to the episodes, the GET below re-monitors them.
    await actions.set_seasons_monitored(
        base_url, api_key, series.id, _desired_seasons(series, season, policy), dry_run
    )

    real = _real_episodes(await sonarr.get_episodes(base_url, api_key, series.id))
    idx = next(
        (
            i
            for i, e in enumerate(real)
            if e.season_number == season and e.episode_number == episode
        ),
        None,
    )
    if idx is None:
        logger.warning("S%02dE%02d not found in Sonarr (tvdb=%s)", season, episode, tvdb_id)
        return

    # Anchor-regression guard (diagnostics): the window must never apply below the furthest
    # recorded watch, or it slides backward and re-downloads already-seen episodes. Gated so the
    # extra read only happens under DEBUG.
    if logger.isEnabledFor(logging.DEBUG):
        recorded = await store.get_watches(tvdb_id)
        max_watch = max(((w.season, w.episode) for w in recorded), default=(season, episode))
        if (season, episode) < max_watch:
            logger.warning(
                "anchor regression tvdb=%s: applying S%02dE%02d below recorded max S%02dE%02d",
                tvdb_id,
                season,
                episode,
                max_watch[0],
                max_watch[1],
            )

    # GET: monitor (and search) ahead.
    ahead = _select_ahead(real, idx, policy)
    # search_ahead = the GET window without a file = what gets searched (and can drag in a season
    # pack of already-watched episodes). The most important line when chasing the re-import bug.
    search_ahead = [e for e in ahead if not e.has_file]
    logger.debug(
        "apply_window tvdb=%s anchor=S%02dE%02d idx=%d get=%d%s keep=%d%s dry_run=%s "
        "ahead=%s search_ahead=%s",
        tvdb_id,
        season,
        episode,
        idx,
        policy.get_count,
        policy.get_unit[0],
        policy.keep_count,
        policy.keep_unit[0],
        dry_run,
        [(e.season_number, e.episode_number) for e in ahead],
        [(e.season_number, e.episode_number) for e in search_ahead],
    )
    if ahead:
        await actions.monitor_episodes(base_url, api_key, [e.id for e in ahead], dry_run)
        if policy.search_on_get:
            await actions.search_episodes(base_url, api_key, [e.id for e in search_ahead], dry_run)

    ahead_ids = {e.id for e in ahead}
    deleted_ids: set[int] = set()

    # Trim ahead: on-disk episodes beyond the GET window are deleted (symmetric to KEEP),
    # except Always-Have. Their unmonitoring is handled by the batch below.
    for candidate in real[idx + 1 :]:
        if candidate.id in ahead_ids:
            continue
        if matches_always_have(
            policy.always_have, candidate.season_number, candidate.episode_number
        ):
            continue
        if candidate.has_file:
            await actions.delete_episode(base_url, api_key, tvdb_id, candidate, "ahead", dry_run)
            deleted_ids.add(candidate.id)

    # KEEP: delete behind whatever falls outside the window, except Always-Have.
    for i in range(idx):
        candidate = real[i]
        if not candidate.has_file:
            continue
        if not _should_delete_behind(real, idx, policy, i):
            continue
        if matches_always_have(
            policy.always_have, candidate.season_number, candidate.episode_number
        ):
            continue
        await actions.delete_episode(base_url, api_key, tvdb_id, candidate, "keep", dry_run)
        deleted_ids.add(candidate.id)

    # Monitoring is decoupled from deletion: monitorr keeps monitored ONLY the GET window (what
    # it actively wants Sonarr to fetch/upgrade). Everything else still monitored — the watched
    # anchor, kept-behind episodes and on-disk episodes protected by Always-Have — is unmonitored
    # while keeping its file, so Sonarr never sees a fully-monitored season and grabs a
    # season-pack "upgrade" of episodes that won't be re-watched. delete_episode already
    # unmonitors what it removes, so exclude deleted_ids.
    to_unmonitor = [
        e.id for e in real if e.id not in ahead_ids and e.monitored and e.id not in deleted_ids
    ]
    await actions.unmonitor_episodes(base_url, api_key, to_unmonitor, dry_run)

    # Season-pack guard: a search for the GET window can make Sonarr grab a full season pack and
    # import the whole season (episodes the user already watched). Pull from the queue
    # (removeFromClient=false → the client keeps seeding) any episode being downloaded that falls
    # outside the window — GET ∪ KEEP ∪ Always-Have — so only the window is imported. Already-
    # imported surplus is removed by the trims above; this stops it before it lands on disk.
    in_window_ids = set(ahead_ids)
    protected = keep_protected_keys(real, idx, policy)
    in_window_ids |= {e.id for e in real if (e.season_number, e.episode_number) in protected}
    in_window_ids |= {
        e.id
        for e in real
        if matches_always_have(policy.always_have, e.season_number, e.episode_number)
    }
    queued = {item.episode_id for item in await sonarr.get_queue(base_url, api_key)}
    surplus = [e.id for e in real if e.id in queued and e.id not in in_window_ids]
    logger.debug(
        "apply_window tvdb=%s unmonitor=%d in_window=%d queued=%d surplus=%s",
        tvdb_id,
        len(to_unmonitor),
        len(in_window_ids),
        len(queued),
        [(e.season_number, e.episode_number) for e in real if e.id in surplus],
    )
    await actions.cancel_downloads(base_url, api_key, surplus, dry_run)
