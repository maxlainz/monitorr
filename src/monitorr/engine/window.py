"""Lógica de ventana: GET (N por delante) / KEEP (N por detrás) / Always-Have.

Ver .claude/behavior.md. Se dispara al detectar "serie vista hasta el episodio E".
"""

import logging

from monitorr.engine.actions import delete_episode
from monitorr.engine.policy import Policy, effective_policy, get_dry_run, matches_always_have
from monitorr.sonarr import client as sonarr
from monitorr.sonarr.client import SonarrEpisode

logger = logging.getLogger(__name__)


def _real_episodes(episodes: list[SonarrEpisode]) -> list[SonarrEpisode]:
    """Episodios "reales" (excluye especiales S00) en orden de emisión."""
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


async def apply_window(tvdb_id: int, season: int, episode: int) -> None:
    policy, enabled = await effective_policy(tvdb_id)
    if not enabled:
        return
    cfg = await sonarr.get_config()
    if cfg is None:
        logger.warning("Sonarr no configurado; se omite la ventana")
        return
    base_url, api_key = cfg

    series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
    if series is None:
        logger.warning("serie tvdb=%s no está en Sonarr", tvdb_id)
        return
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
        logger.warning("S%02dE%02d no encontrado en Sonarr (tvdb=%s)", season, episode, tvdb_id)
        return

    # GET: monitorizar (y buscar) por delante.
    ahead = _select_ahead(real, idx, policy)
    if ahead:
        await sonarr.set_monitored(base_url, api_key, [e.id for e in ahead], True)
        if policy.search_on_get:
            await sonarr.search_episodes(base_url, api_key, [e.id for e in ahead if not e.has_file])

    # KEEP: borrar por detrás lo que cae fuera de la ventana, salvo Always-Have.
    dry_run = await get_dry_run()
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
        await delete_episode(base_url, api_key, tvdb_id, candidate, "keep", dry_run)
