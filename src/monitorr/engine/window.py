"""Window logic: GET (N ahead) / KEEP (N behind) / Always-Have.

See .claude/behavior.md. Triggered when detecting "show watched up to episode E".
"""

import logging

from monitorr import store
from monitorr.engine import actions
from monitorr.engine.policy import (
    Policy,
    age_days,
    effective_policy,
    get_dry_run,
    matches_always_have,
)
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

logger = logging.getLogger(__name__)


def _desired_seasons(
    series: SonarrSeries, anchor_season: int, policy: Policy, armed: bool
) -> dict[int, bool]:
    """Season-level monitoring enforced by monitorr: by episodes, all off (100%
    per-episode control); by seasons, only those of the GET window on. A gated (un-armed)
    window leaves every season off — new episodes must not inherit monitoring on a show
    whose grace already deletes what GET downloads."""
    if armed and policy.get_unit == "seasons":
        return {
            s.season_number: anchor_season <= s.season_number <= anchor_season + policy.get_count
            for s in series.seasons
        }
    return {s.season_number: False for s in series.seasons}


async def _is_armed(tvdb_id: int, policy: Policy) -> bool:
    """Re-arm gate: once a show has been inactive longer than a grace that deletes what GET
    downloads (`dormant` purges everything; `unwatched` trims the downloaded-ahead episodes),
    arming the window would only feed the next sweep — an endless download/delete oscillation
    re-triggered by every full sync. The gate shares the graces' inactivity clock; a live watch
    records activity before applying the window, so resuming the show re-arms GET naturally.
    `completed` intentionally does not gate: a caught-up show must re-arm when a new season airs
    (see behavior.md "GET re-arms when the show returns")."""
    thresholds = [d for d in (policy.dormant_days, policy.grace_unwatched_days) if d is not None]
    if not thresholds:
        return True
    last_activity = await store.get_activity(tvdb_id)
    return last_activity is None or age_days(last_activity) <= min(thresholds)


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


async def apply_window(
    tvdb_id: int,
    season: int,
    episode: int,
    *,
    series: SonarrSeries | None = None,
    episodes: list[SonarrEpisode] | None = None,
) -> set[int]:
    """`series`/`episodes` let the sync pass already-fetched data to avoid the per-show
    `find_series_by_tvdb` + `get_episodes`; the poller/webhook leave them None → fetched here.
    Equivalent: `series` is only read for `.id`/`.seasons` (season monitoring re-GETs the series
    itself), and `episodes` is the same start-of-cycle snapshot it would otherwise have fetched.

    Returns the episode ids this window searched, so the sync's same-cycle re-search of
    Wanted/Missing can skip them (the queue snapshot it filters on predates these grabs)."""
    policy, enabled = await effective_policy(tvdb_id)
    if not enabled:
        return set()
    cfg = await sonarr.get_config()
    if cfg is None:
        logger.warning("Sonarr not configured; skipping the window")
        return set()
    base_url, api_key = cfg
    dry_run = await get_dry_run()

    if series is None:
        series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
    if series is None:
        logger.warning("show tvdb=%s is not in Sonarr", tvdb_id)
        return set()

    if episodes is None:
        episodes = await sonarr.get_episodes(base_url, api_key, series.id)
    real = _real_episodes(episodes)

    # Anchor floor: the window must never apply below the furthest episode ever recorded as
    # watched, or it slides backward and re-downloads already-seen episodes. The persisted store is
    # the authority — monotonic, and filled with the complete Plex history on every full sync —
    # whereas the live sync path under-reports the furthest watch when its file was already trimmed
    # AND its play predates the incremental watermark (the anchor would then fall back to the
    # furthest on-disk watch and creep forward N episodes per cycle through an already-seen series).
    # Enforced here so every caller (sync, poller, webhook) is protected. Clamp to an episode
    # Sonarr actually lists so a persisted max pointing at a deleted/absolute/special episode can't
    # no-op the lookup below; this picks the furthest *real* watched episode.
    real_keys = {(e.season_number, e.episode_number) for e in real}
    recorded = [w for w in await store.get_watches(tvdb_id) if (w.season, w.episode) in real_keys]
    floor = max(((w.season, w.episode) for w in recorded), default=(season, episode))
    if floor > (season, episode):
        logger.info(
            "re-anchored tvdb=%s from S%02dE%02d to S%02dE%02d (persisted furthest watch)",
            tvdb_id,
            season,
            episode,
            floor[0],
            floor[1],
        )
        season, episode = floor

    def _anchor_idx(ordered: list[SonarrEpisode]) -> int | None:
        return next(
            (
                i
                for i, e in enumerate(ordered)
                if e.season_number == season and e.episode_number == episode
            ),
            None,
        )

    # Resolve the anchor before any write: an anchor Sonarr doesn't list must abort with no
    # half-applied season flags.
    idx = _anchor_idx(real)
    if idx is None:
        logger.warning("S%02dE%02d not found in Sonarr (tvdb=%s)", season, episode, tvdb_id)
        return set()

    armed = await _is_armed(tvdb_id, policy)
    if not armed:
        logger.info(
            "GET window not armed for tvdb=%s (inactive past dormant/unwatched grace)", tvdb_id
        )

    # Enforce season monitoring (from the floored anchor season) before touching the per-episode
    # flags (cascade-proof order). Sonarr propagates a season flag to its episodes, so a change
    # invalidates the episode snapshot: the stale `monitored` values would make the unmonitor
    # batch below skip watched episodes the cascade just re-monitored (which the sync's re-search
    # would then re-download). Re-fetch so every set is computed from post-cascade flags.
    if await actions.set_seasons_monitored(
        base_url, api_key, series.id, _desired_seasons(series, season, policy, armed), dry_run
    ):
        real = _real_episodes(await sonarr.get_episodes(base_url, api_key, series.id))
        idx = _anchor_idx(real)
        if idx is None:  # the episode list changed under us mid-flight
            logger.warning("S%02dE%02d vanished from Sonarr (tvdb=%s)", season, episode, tvdb_id)
            return set()

    # GET: monitor (and search) ahead.
    ahead = _select_ahead(real, idx, policy)
    ahead_ids = {e.id for e in ahead}
    # Always-Have episodes stay monitored too, so Sonarr can upgrade them in place (and re-fetch
    # them if their file is missing — consistent with "always have this episode"). Computed once
    # and reused by the unmonitor batch and the season-pack guard below.
    always_have_ids = {
        e.id
        for e in real
        if matches_always_have(policy.always_have, e.season_number, e.episode_number)
    }
    # search_ahead = the GET window without a file AND already aired = what gets searched (and can
    # drag in a season pack of already-watched episodes). The most important line when chasing the
    # re-import bug. Unaired episodes are only monitored — searching them is a guaranteed-empty
    # indexer query on every trigger of a weekly show; Sonarr grabs them on air via RSS.
    search_ahead = [e for e in ahead if not e.has_file and e.has_aired()]
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
    # Gated: only Always-Have keeps its monitoring; the GET window is neither monitored nor
    # searched (the inactive show's grace would delete whatever it downloads).
    monitor_ids = (ahead_ids if armed else set()) | always_have_ids
    if monitor_ids:
        await actions.monitor_episodes(base_url, api_key, sorted(monitor_ids), dry_run)
    searched_ids: set[int] = set()
    if armed and policy.search_on_get and search_ahead:
        searched_ids = {e.id for e in search_ahead}
        await actions.search_episodes(base_url, api_key, sorted(searched_ids), dry_run)

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

    # Monitoring is decoupled from deletion: monitorr keeps monitored the GET window plus the
    # Always-Have episodes (what it actively wants Sonarr to fetch/upgrade). Everything else still
    # monitored — the watched anchor and the kept-behind episodes (KEEP) — is unmonitored while
    # keeping its file, so a season of merely-kept episodes never reaches the all-monitored state
    # that lets Sonarr grab a season-pack "upgrade" of episodes that won't be re-watched.
    # delete_episode already unmonitors what it removes, so exclude deleted_ids. monitor_ids is
    # the authority on what stays monitored — when the window is gated it excludes the GET ahead.
    to_unmonitor = [
        e.id for e in real if e.id not in monitor_ids and e.monitored and e.id not in deleted_ids
    ]
    await actions.unmonitor_episodes(base_url, api_key, to_unmonitor, dry_run)

    # Season-pack guard: a search for the GET window can make Sonarr grab a full season pack and
    # import the whole season (episodes the user already watched). Pull from the queue
    # (removeFromClient=false → the client keeps seeding) any episode being downloaded that falls
    # outside the window — GET ∪ KEEP ∪ Always-Have — so only the window is imported. Already-
    # imported surplus is removed by the trims above; this stops it before it lands on disk.
    in_window_ids = set(ahead_ids) if armed else set()
    protected = keep_protected_keys(real, idx, policy)
    in_window_ids |= {e.id for e in real if (e.season_number, e.episode_number) in protected}
    in_window_ids |= always_have_ids
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
    return searched_ids
