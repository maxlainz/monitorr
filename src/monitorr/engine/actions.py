"""Escrituras a Sonarr guardadas por dry-run. Compartido por window.py, grace.py y sync.py.

Con `dry_run=True` no se llama a Sonarr: solo se registra/loguea la intención. El borrado
además queda en `deletion_log` (dry_run=1) para la lista de pendientes de la UI.
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
    tvdb_id: int,
    series: SonarrSeries,
    episodes: list[SonarrEpisode],
    always_have: list[str],
    dry_run: bool,
) -> None:
    """Deja monitorizado solo el piloto (S01E01). Los episodios ya descargados que se
    desmonitorizan se **borran** (salvo Always-Have); el piloto se busca si le falta fichero."""
    pilot = next((e for e in episodes if e.season_number == 1 and e.episode_number == 1), None)
    pilot_id = pilot.id if pilot else None

    # Descargados (no piloto, no protegidos) que dejan de monitorizarse → se borran.
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

    if dry_run:
        logger.info("[dry-run] dejaría solo el piloto monitorizado en %s", series.title)
        return

    if to_unmonitor:
        await sonarr.set_monitored(base_url, api_key, to_unmonitor, False)
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
