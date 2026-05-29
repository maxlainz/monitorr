"""Borrado de un episodio, respetando dry-run. Compartido por window.py y grace.py."""

import logging

from monitorr import store
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode

logger = logging.getLogger(__name__)


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
            "[dry-run] borraría S%02dE%02d (%s)",
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
    logger.info("borrado S%02dE%02d (%s)", episode.season_number, episode.episode_number, reason)
