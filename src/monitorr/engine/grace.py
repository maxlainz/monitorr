"""Barrido de grace periods (watched / unwatched / dormant). Ver .claude/behavior.md.

Tarea periódica que borra por inactividad temporal, respetando Always-Have y dry-run.
"""


async def sweep() -> None:
    raise NotImplementedError  # TODO(engine-grace)
