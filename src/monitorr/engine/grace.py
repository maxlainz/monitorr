"""Barrido de grace periods (watched / unwatched / dormant). Ver .claude/behavior.md.

Tarea periódica que borra por inactividad temporal, respetando Always-Have y dry-run.
"""

import logging
from datetime import UTC, datetime

from monitorr import store
from monitorr.engine.actions import delete_episode
from monitorr.engine.policy import effective_policy, get_dry_run, matches_always_have
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode

logger = logging.getLogger(__name__)


def _age_days(iso_timestamp: str) -> float:
    moment = datetime.fromisoformat(iso_timestamp)
    return (datetime.now(UTC) - moment).total_seconds() / 86400


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
    ):
        return

    series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
    if series is None:
        return
    episodes = [
        e
        for e in await sonarr.get_episodes(base_url, api_key, series.id)
        if e.season_number >= 1 and e.has_file
    ]
    if not episodes:
        return

    watches = {(w.season, w.episode): w.watched_at for w in await store.get_watches(tvdb_id)}
    activity_age = _age_days(last_activity)

    # dormant: serie inactiva demasiado tiempo → borrar todo lo borrable.
    if policy.dormant_days is not None and activity_age > policy.dormant_days:
        for episode in episodes:
            if matches_always_have(
                policy.always_have, episode.season_number, episode.episode_number
            ):
                continue
            await delete_episode(base_url, api_key, tvdb_id, episode, "dormant", dry_run)
        return

    # watched: vistos hace más de X días, conservando el más reciente como marcador.
    if policy.grace_watched_days is not None:
        watched: list[tuple[SonarrEpisode, str]] = []
        for episode in episodes:
            seen_at = watches.get((episode.season_number, episode.episode_number))
            if seen_at is not None:
                watched.append((episode, seen_at))
        if watched:
            marker = max(watched, key=lambda item: item[1])[0]
            for episode, seen_at in watched:
                if episode is marker or _age_days(seen_at) <= policy.grace_watched_days:
                    continue
                if matches_always_have(
                    policy.always_have, episode.season_number, episode.episode_number
                ):
                    continue
                await delete_episode(base_url, api_key, tvdb_id, episode, "grace_watched", dry_run)

    # unwatched: nunca vistos y serie inactiva > X días, conservando el primero como marcador.
    if policy.grace_unwatched_days is not None and activity_age > policy.grace_unwatched_days:
        unwatched = sorted(
            (e for e in episodes if (e.season_number, e.episode_number) not in watches),
            key=lambda e: (e.season_number, e.episode_number),
        )
        for episode in unwatched[1:]:
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
            logger.exception("error en grace sweep de tvdb=%s", tvdb_id)
