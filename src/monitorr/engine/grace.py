"""Grace-period sweep (watched / unwatched / dormant). See .claude/behavior.md.

Periodic task that deletes by temporal inactivity, respecting Always-Have and dry-run.
"""

import logging

from monitorr import store
from monitorr.engine.actions import delete_episode
from monitorr.engine.policy import age_days, effective_policy, get_dry_run, matches_always_have
from monitorr.engine.window import keep_protected_keys
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode

logger = logging.getLogger(__name__)


def _is_caught_up(watched_keys: set[tuple[int, int]], all_eps: list[SonarrEpisode]) -> bool:
    """True if the last aired episode (airing order) has been watched: nothing aired is left
    unseen. Future (unaired) episodes don't count — they aren't downloadable yet."""
    aired = [(e.season_number, e.episode_number) for e in all_eps if e.has_aired()]
    if not aired or not watched_keys:
        return False
    return max(watched_keys) >= max(aired)


async def _sweep_series(
    base_url: str, api_key: str, tvdb_id: int, last_activity: str, dry_run: bool
) -> None:
    policy, enabled = await effective_policy(tvdb_id)
    if not enabled:
        return
    if (
        policy.grace_watched_days is None
        and policy.grace_unwatched_days is None
        and policy.dormant_days is None
        and policy.grace_completed_days is None
    ):
        return

    series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
    if series is None:
        return
    all_eps = [
        e for e in await sonarr.get_episodes(base_url, api_key, series.id) if e.season_number >= 1
    ]
    episodes = [e for e in all_eps if e.has_file]
    if not episodes:
        return

    watches = {(w.season, w.episode): w.watched_at for w in await store.get_watches(tvdb_id)}
    activity_age = age_days(last_activity)

    # completed: the show has nothing aired left to watch and has been inactive too long → purge
    # everything deletable (dormant + the "caught up" filter, so it never deletes aired-but-unseen
    # episodes). GET re-arms via sync's apply_window when a new season airs.
    if (
        policy.grace_completed_days is not None
        and activity_age > policy.grace_completed_days
        and _is_caught_up(set(watches), all_eps)
    ):
        for episode in episodes:
            if matches_always_have(
                policy.always_have, episode.season_number, episode.episode_number
            ):
                continue
            await delete_episode(base_url, api_key, tvdb_id, episode, "completed", dry_run)
        return

    # dormant: show inactive too long → delete everything deletable.
    if policy.dormant_days is not None and activity_age > policy.dormant_days:
        for episode in episodes:
            if matches_always_have(
                policy.always_have, episode.season_number, episode.episode_number
            ):
                continue
            await delete_episode(base_url, api_key, tvdb_id, episode, "dormant", dry_run)
        return

    # KEEP floor: the ongoing trims (watched/unwatched) must not delete what the KEEP window
    # guarantees on disk, relative to the latest watched episode (the viewing point). KEEP is the
    # spatial retention guarantee; grace only trims what already falls outside it. (The bulk purges
    # completed/dormant above intentionally ignore KEEP — the show is finished/abandoned.)
    # The anchor is clamped to a key Sonarr actually lists — same clamp as apply_window's floor:
    # a recorded watch Sonarr doesn't know (Plex numbering mismatch, removed episode) must pick
    # the furthest *real* watched episode, not silently dissolve the whole KEEP floor.
    real = sorted(all_eps, key=lambda e: (e.season_number, e.episode_number))
    real_keys = {(e.season_number, e.episode_number) for e in real}
    kept_keys: set[tuple[int, int]] = set()
    anchor = max((key for key in watches if key in real_keys), default=None)
    if anchor is not None:
        anchor_idx = next(
            i for i, e in enumerate(real) if (e.season_number, e.episode_number) == anchor
        )
        kept_keys = keep_protected_keys(real, anchor_idx, policy)

    # watched: watched more than X days ago, keeping the most recent one as a marker.
    if policy.grace_watched_days is not None:
        watched: list[tuple[SonarrEpisode, str]] = []
        for episode in episodes:
            seen_at = watches.get((episode.season_number, episode.episode_number))
            if seen_at is not None:
                watched.append((episode, seen_at))
        if watched:
            marker = max(watched, key=lambda item: item[1])[0]
            for episode, seen_at in watched:
                if episode is marker or age_days(seen_at) <= policy.grace_watched_days:
                    continue
                if (episode.season_number, episode.episode_number) in kept_keys:
                    continue
                if matches_always_have(
                    policy.always_have, episode.season_number, episode.episode_number
                ):
                    continue
                await delete_episode(base_url, api_key, tvdb_id, episode, "grace_watched", dry_run)

    # unwatched: never watched and show inactive > X days, keeping the first one as a marker.
    if policy.grace_unwatched_days is not None and activity_age > policy.grace_unwatched_days:
        unwatched = sorted(
            (e for e in episodes if (e.season_number, e.episode_number) not in watches),
            key=lambda e: (e.season_number, e.episode_number),
        )
        for episode in unwatched[1:]:
            if (episode.season_number, episode.episode_number) in kept_keys:
                continue
            if matches_always_have(
                policy.always_have, episode.season_number, episode.episode_number
            ):
                continue
            await delete_episode(base_url, api_key, tvdb_id, episode, "grace_unwatched", dry_run)


async def sweep() -> None:
    cfg = await sonarr.get_config()
    if cfg is None:
        return
    base_url, api_key = cfg
    dry_run = await get_dry_run()
    for tvdb_id, last_activity in await store.list_activity():
        try:
            await _sweep_series(base_url, api_key, tvdb_id, last_activity, dry_run)
        except Exception:
            logger.exception("error in grace sweep for tvdb=%s", tvdb_id)
