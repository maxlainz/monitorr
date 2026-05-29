"""Acceso a SQLite. Helpers async; cada llamada abre su propia conexión (WAL, carga baja)."""

from datetime import UTC, datetime

import aiosqlite
from pydantic import BaseModel

from monitorr.config import get_settings


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_path() -> str:
    return str(get_settings().db_path)


# --- settings (key/value) ---


async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT value FROM setting WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return str(row[0]) if row else None


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def delete_setting(key: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM setting WHERE key = ?", (key,))
        await db.commit()


# --- overrides por serie ---


class Override(BaseModel):
    tvdb_id: int
    enabled: bool
    policy_json: str | None


async def get_override(tvdb_id: int) -> Override | None:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT tvdb_id, enabled, policy_json FROM series_override WHERE tvdb_id = ?",
            (tvdb_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Override(tvdb_id=int(row[0]), enabled=bool(row[1]), policy_json=row[2])


async def list_overrides() -> list[Override]:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT tvdb_id, enabled, policy_json FROM series_override ORDER BY tvdb_id"
        )
        rows = await cursor.fetchall()
        return [Override(tvdb_id=int(r[0]), enabled=bool(r[1]), policy_json=r[2]) for r in rows]


async def set_override(tvdb_id: int, enabled: bool, policy_json: str | None) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO series_override (tvdb_id, enabled, policy_json) VALUES (?, ?, ?) "
            "ON CONFLICT(tvdb_id) DO UPDATE SET enabled = excluded.enabled, "
            "policy_json = excluded.policy_json",
            (tvdb_id, int(enabled), policy_json),
        )
        await db.commit()


async def delete_override(tvdb_id: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM series_override WHERE tvdb_id = ?", (tvdb_id,))
        await db.commit()


# --- visionado / actividad ---


class EpisodeWatch(BaseModel):
    season: int
    episode: int
    watched_at: str


async def record_watch(
    tvdb_id: int, season: int, episode: int, watched_at: str | None = None
) -> None:
    timestamp = watched_at or _now()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO episode_watch (tvdb_id, season, episode, watched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tvdb_id, season, episode) DO UPDATE SET watched_at = excluded.watched_at",
            (tvdb_id, season, episode, timestamp),
        )
        # MAX para que el orden de registro no degrade la última actividad (importa en backfill).
        await db.execute(
            "INSERT INTO series_activity (tvdb_id, last_watch_at) VALUES (?, ?) "
            "ON CONFLICT(tvdb_id) DO UPDATE SET "
            "last_watch_at = MAX(last_watch_at, excluded.last_watch_at)",
            (tvdb_id, timestamp),
        )
        await db.commit()


async def get_watches(tvdb_id: int) -> list[EpisodeWatch]:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT season, episode, watched_at FROM episode_watch WHERE tvdb_id = ?",
            (tvdb_id,),
        )
        rows = await cursor.fetchall()
        return [
            EpisodeWatch(season=int(r[0]), episode=int(r[1]), watched_at=str(r[2])) for r in rows
        ]


async def get_activity(tvdb_id: int) -> str | None:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT last_watch_at FROM series_activity WHERE tvdb_id = ?", (tvdb_id,)
        )
        row = await cursor.fetchone()
        return str(row[0]) if row else None


async def list_activity() -> list[tuple[int, str]]:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT tvdb_id, last_watch_at FROM series_activity")
        rows = await cursor.fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]


# --- log de borrados ---


class Deletion(BaseModel):
    tvdb_id: int
    season: int
    episode: int
    title: str | None
    reason: str
    dry_run: bool
    created_at: str


async def record_deletion(
    tvdb_id: int,
    season: int,
    episode: int,
    title: str | None,
    episode_file_id: int | None,
    reason: str,
    dry_run: bool,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO deletion_log "
            "(tvdb_id, season, episode, title, episode_file_id, reason, dry_run, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tvdb_id, season, episode, title, episode_file_id, reason, int(dry_run), _now()),
        )
        await db.commit()


async def list_deletions(limit: int = 50, dry_run: bool | None = None) -> list[Deletion]:
    query = "SELECT tvdb_id, season, episode, title, reason, dry_run, created_at FROM deletion_log"
    params: list[object] = []
    if dry_run is not None:
        query += " WHERE dry_run = ?"
        params.append(int(dry_run))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            Deletion(
                tvdb_id=int(r[0]),
                season=int(r[1]),
                episode=int(r[2]),
                title=r[3],
                reason=str(r[4]),
                dry_run=bool(r[5]),
                created_at=str(r[6]),
            )
            for r in rows
        ]
