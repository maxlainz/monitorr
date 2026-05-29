"""Escrituras a Sonarr guardadas por dry-run. Compartido por window.py, grace.py y sync.py.

Con `dry_run=True` no se llama a Sonarr: solo se registra/loguea la intención. El borrado
además queda en `deletion_log` (dry_run=1) para la lista de pendientes de la UI.
"""

import logging

from monitorr import store
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

logger = logging.getLogger(__name__)


async def monitor_episodes(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] monitorizaría %d episodios", len(episode_ids))
        return
    await sonarr.set_monitored(base_url, api_key, episode_ids, True)


async def search_episodes(
    base_url: str, api_key: str, episode_ids: list[int], dry_run: bool
) -> None:
    if not episode_ids:
        return
    if dry_run:
        logger.info("[dry-run] buscaría %d episodios", len(episode_ids))
        return
    await sonarr.search_episodes(base_url, api_key, episode_ids)


async def normalize_to_pilot(
    base_url: str,
    api_key: str,
    series: SonarrSeries,
    episodes: list[SonarrEpisode],
    dry_run: bool,
) -> None:
    """Desmonitoriza todo y deja solo el piloto (S01E01); busca el piloto si le falta fichero."""
    pilot = next((e for e in episodes if e.season_number == 1 and e.episode_number == 1), None)
    if dry_run:
        logger.info("[dry-run] normalizaría a Pilot: %s (tvdb=%s)", series.title, series.tvdb_id)
        return
    all_ids = [e.id for e in episodes]
    if all_ids:
        await sonarr.set_monitored(base_url, api_key, all_ids, False)
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
