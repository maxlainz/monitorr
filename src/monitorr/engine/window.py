"""Lógica de ventana: GET (N por delante) / KEEP (N por detrás) / Always-Have.

Ver .claude/behavior.md. Se dispara al detectar "serie vista hasta el episodio E".
"""


async def apply_window(series_tvdb_id: int, season: int, episode: int) -> None:
    """Recalcula la ventana alrededor de (season, episode): monitoriza+busca adelante,
    borra+desmonitoriza atrás salvo Always-Have. Respeta dry-run."""
    raise NotImplementedError  # TODO(engine-window)
