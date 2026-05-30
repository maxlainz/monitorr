"""Writes to Sonarr guarded by dry-run. Shared by window.py, grace.py and sync.py.

With `dry_run=True` Sonarr is not called: the intent is only recorded/logged. The deletion
also stays in `deletion_log` (dry_run=1) for the UI's pending list.
"""

import logging

from monitorr import store
from monitorr.engine.policy import matches_always_have
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

logger = logging.getLogger(__name__)


async def monitor_episodes(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] would monitor %d episodes", len(episode_ids))
        return
    await sonarr.set_monitored(base_url, api_key, episode_ids, True)


async def set_seasons_monitored(
    base_url: str, api_key: str, series_id: int, desired: dict[int, bool], dry_run: bool
) -> None:
    """Enforces season-level monitoring so monitorr is the authority there too
    (new episodes inherit their season's flag). Guarded by dry-run."""
    if not desired:
        return
    if dry_run:
        on = sorted(n for n, m in desired.items() if m)
        off = sorted(n for n, m in desired.items() if not m)
        logger.info("[dry-run] would set monitored seasons on=%s off=%s", on, off)
        return
    await sonarr.set_seasons_monitored(base_url, api_key, series_id, desired)


async def unmonitor_episodes(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    """Unmonitors fileless episodes that fall outside the GET window (there's nothing to
    delete, just preventing Sonarr from downloading them). Guarded by dry-run."""
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] would unmonitor %d episodes", len(episode_ids))
        return
    await sonarr.set_monitored(base_url, api_key, episode_ids, False)


async def search_episodes(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] would search %d episodes", len(episode_ids))
        return
    await sonarr.search_episodes(base_url, api_key, episode_ids)


async def cancel_downloads(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    """Pulls these episodes' in-progress downloads from Sonarr's queue (so they aren't imported).
    The torrent stays in the client seeding until its ratio. Guarded by dry-run."""
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] would cancel queued downloads of %d episodes", len(episode_ids))
        return
    wanted = set(episode_ids)
    for item in await sonarr.get_queue(base_url, api_key):
        if item.episode_id in wanted:
            await sonarr.delete_queue_item(
                base_url, api_key, item.id, remove_from_client=False, blocklist=False
            )
            logger.info("queued download cancelled (episodeId=%s); still seeding", item.episode_id)


async def normalize_to_pilot(
    base_url: str,
    api_key: str,
    tvdb_id: int,
    series: SonarrSeries,
    episodes: list[SonarrEpisode],
    always_have: list[str],
    dry_run: bool,
) -> None:
    """Leaves only the pilot (S01E01) monitored. Already-downloaded episodes that get
    unmonitored are **deleted** (except Always-Have); the pilot is searched if it lacks a file."""
    # Seasons to off first (cascade-proof order): this way Sonarr doesn't re-monitor by
    # season the episodes it discovers; the pilot is re-monitored at the end.
    await set_seasons_monitored(
        base_url, api_key, series.id, {s.season_number: False for s in series.seasons}, dry_run
    )
    pilot = next((e for e in episodes if e.season_number == 1 and e.episode_number == 1), None)
    pilot_id = pilot.id if pilot else None

    # Downloaded (not pilot, not protected) that stop being monitored → deleted.
    to_delete = [
        e
        for e in episodes
        if e.id != pilot_id
        and e.has_file
        and not matches_always_have(always_have, e.season_number, e.episode_number)
    ]
    delete_ids = {e.id for e in to_delete}
    to_unmonitor = [e.id for e in episodes if e.id != pilot_id and e.id not in delete_ids]

    for episode in to_delete:
        await delete_episode(base_url, api_key, tvdb_id, episode, "normalize", dry_run)

    if not dry_run and to_unmonitor:
        await sonarr.set_monitored(base_url, api_key, to_unmonitor, False)

    # The unmonitored ones still downloading: pull them from the queue (don't import them).
    await cancel_downloads(base_url, api_key, to_unmonitor, dry_run)

    if dry_run:
        logger.info("[dry-run] would leave only the pilot monitored in %s", series.title)
        return

    if pilot is not None:
        await sonarr.set_monitored(base_url, api_key, [pilot.id], True)
        if not pilot.has_file:
            await sonarr.search_episodes(base_url, api_key, [pilot.id])


async def delete_episode(
    base_url: str,
    api_key: str,
    tvdb_id: int,
    episode: SonarrEpisode,
    reason: str,
    dry_run: bool,
) -> None:
    if dry_run:
        await store.record_deletion(
            tvdb_id,
            episode.season_number,
            episode.episode_number,
            episode.title,
            episode.episode_file_id,
            reason,
            True,
        )
        logger.info(
            "[dry-run] would delete S%02dE%02d (%s)",
            episode.season_number,
            episode.episode_number,
            reason,
        )
        return

    if episode.episode_file_id:
        await sonarr.delete_episode_file(base_url, api_key, episode.episode_file_id)
    await sonarr.set_monitored(base_url, api_key, [episode.id], False)
    await store.record_deletion(
        tvdb_id,
        episode.season_number,
        episode.episode_number,
        episode.title,
        episode.episode_file_id,
        reason,
        False,
    )
    logger.info("deleted S%02dE%02d (%s)", episode.season_number, episode.episode_number, reason)
