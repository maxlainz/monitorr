from pathlib import Path

import aiosqlite

# Migraciones idempotentes ordenadas. El índice + 1 es la versión de esquema (PRAGMA
# user_version); al arrancar se aplican solo las pendientes. Añadir nuevas al final, nunca
# editar las ya publicadas.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE setting (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE series_override (
        tvdb_id     INTEGER PRIMARY KEY,
        enabled     INTEGER NOT NULL DEFAULT 1,
        policy_json TEXT
    );
    CREATE TABLE episode_watch (
        tvdb_id    INTEGER NOT NULL,
        season     INTEGER NOT NULL,
        episode    INTEGER NOT NULL,
        watched_at TEXT NOT NULL,
        PRIMARY KEY (tvdb_id, season, episode)
    );
    CREATE TABLE series_activity (
        tvdb_id       INTEGER PRIMARY KEY,
        last_watch_at TEXT NOT NULL
    );
    CREATE TABLE deletion_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tvdb_id         INTEGER NOT NULL,
        season          INTEGER NOT NULL,
        episode         INTEGER NOT NULL,
        title           TEXT,
        episode_file_id INTEGER,
        reason          TEXT NOT NULL,
        dry_run         INTEGER NOT NULL,
        created_at      TEXT NOT NULL
    );
    """,
    # Deduplica los previews (dry_run=1) a una fila por episodio: borra los antiguos y crea un
    # índice único parcial. El historial de borrados reales (dry_run=0) sigue siendo append-only.
    """
    DELETE FROM deletion_log WHERE dry_run = 1 AND id NOT IN (
        SELECT MAX(id) FROM deletion_log WHERE dry_run = 1
        GROUP BY tvdb_id, season, episode
    );
    CREATE UNIQUE INDEX deletion_pending_unique
        ON deletion_log (tvdb_id, season, episode) WHERE dry_run = 1;
    """,
]


async def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        cursor = await db.execute("PRAGMA user_version;")
        row = await cursor.fetchone()
        current = int(row[0]) if row else 0
        for version in range(current, len(MIGRATIONS)):
            await db.executescript(MIGRATIONS[version])
            await db.execute(f"PRAGMA user_version = {version + 1};")
        await db.commit()
