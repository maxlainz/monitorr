"""Poller de sesiones (mecanismo de detección principal). Ver .claude/plex.md.

Bucle asyncio lanzado en el lifespan: consulta /status/sessions cada `interval` s, detecta
"visto" (viewOffset/duration ≥ ~0.9) con debounce por sesión y dispara el motor de ventana.
"""


async def poll_loop(interval: int) -> None:
    raise NotImplementedError  # TODO(plex-poller)
