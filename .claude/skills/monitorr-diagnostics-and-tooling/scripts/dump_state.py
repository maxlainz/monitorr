#!/usr/bin/env python3
"""dump_state.py — read-only overview of a monitorr SQLite database.

Prints: schema version, all `setting` keys (secrets redacted), the policy JSON,
dry-run state, sync state (history_watermark / last_full_sync / last_sync with
human ages), per-show activity + watch counts, per-series overrides, and the
recent deletion_log split into pending previews (dry_run=1) vs real deletions.

STDLIB ONLY — runs against any monitorr instance without the project venv.
Opens the DB in SQLite read-only URI mode; never writes.

Usage:
    python3 dump_state.py [--db /config/monitorr.db]

Exit codes: 0 = dumped, 2 = database could not be opened.

Schema source of truth: src/monitorr/db.py (MIGRATIONS). Setting keys:
src/monitorr/constants.py plus the module-private "last_sync" key written by
src/monitorr/sync.py. Verified against v1.6.1 (2026-07-02).
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime

DEFAULT_DB = "/config/monitorr.db"

# Any setting key containing one of these markers has its value redacted.
SENSITIVE_MARKERS = ("token", "api_key", "secret")

# Keys handled in dedicated sections (still listed in the settings table).
POLICY_KEY = "policy"          # constants.POLICY
DRY_RUN_KEY = "dry_run"        # constants.DRY_RUN ("1"/"0"; absent = dry-run ON)
WATERMARK_KEY = "history_watermark"  # constants.HISTORY_WATERMARK (ISO-8601 UTC)
LAST_FULL_KEY = "last_full_sync"     # constants.LAST_FULL_SYNC (ISO-8601 UTC)
LAST_SYNC_KEY = "last_sync"          # sync.py _LAST_SYNC (JSON summary)

DELETION_LOG_LIMIT = 20


def parse_iso(value):
    """ISO-8601 string -> aware datetime, or None. Naive timestamps are assumed UTC
    (mirrors monitorr.engine.policy.age_days)."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def age_str(value):
    """Human age of an ISO timestamp relative to now, e.g. '3.2 d' or '5.1 h'."""
    moment = parse_iso(value)
    if moment is None:
        return "unparseable"
    seconds = (datetime.now(UTC) - moment).total_seconds()
    if seconds < 0:
        return f"IN THE FUTURE by {-seconds / 3600:.1f} h"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def redact(value):
    return (value[:4] + "…") if len(value) > 4 else "…"


def print_table(headers, rows):
    if not rows:
        print("  (none)")
        return
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    print("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in cells:
        print("  " + "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def section(title):
    print(f"\n== {title} ==")


def fetch_all(db, query, params=()):
    """Run a query, returning [] when the table does not exist yet (early schema version)."""
    try:
        return db.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"  (query failed: {exc})")
        return []


def dump_settings(settings):
    section("settings (secrets redacted)")
    rows = []
    for key in sorted(settings):
        value = settings[key]
        if any(marker in key for marker in SENSITIVE_MARKERS):
            value = redact(value)
        elif len(value) > 80:
            value = value[:77] + "..."
        rows.append((key, value))
    print_table(("key", "value"), rows)


def dump_policy(settings):
    section("global policy (setting key 'policy')")
    raw = settings.get(POLICY_KEY)
    if raw is None:
        print("  (not set — built-in Policy() defaults apply; see engine/policy.py)")
        return
    try:
        print(json.dumps(json.loads(raw), indent=2))
    except ValueError:
        print(f"  (NOT VALID JSON: {raw!r})")


def dump_dry_run(settings):
    section("dry-run")
    raw = settings.get(DRY_RUN_KEY)
    if raw is None:
        print("  ON (key absent — the default; all Sonarr writes are no-ops)")
    elif raw == "1":
        print("  ON (\"1\" — all Sonarr writes are no-ops; deletions recorded as previews)")
    elif raw == "0":
        print("  OFF (\"0\" — REAL MODE: deletions and Sonarr writes are live)")
    else:
        print(f"  UNEXPECTED VALUE {raw!r} (code treats anything but \"1\" as OFF... "
              "get_dry_run: raw == \"1\")")


def dump_sync_state(settings):
    section("sync state")
    for key in (WATERMARK_KEY, LAST_FULL_KEY):
        raw = settings.get(key)
        if raw is None:
            print(f"  {key}: (not set — next sync will be FULL)")
        else:
            print(f"  {key}: {raw}  (age: {age_str(raw)})")
    raw = settings.get(LAST_SYNC_KEY)
    if raw is None:
        print("  last_sync: (never completed)")
        return
    try:
        info = json.loads(raw)
    except ValueError:
        print(f"  last_sync: NOT VALID JSON: {raw!r}")
        return
    at = info.get("at", "?")
    print(f"  last_sync: at={at} (age: {age_str(at)}) mode={info.get('mode', '?')}")
    print(f"             shows={info.get('shows', '?')} matched={info.get('matched', '?')} "
          f"normalized={info.get('normalized', '?')} searched={info.get('searched', '?')}")


def dump_activity(db):
    section("per-show activity + watch counts (newest activity first)")
    rows = fetch_all(
        db,
        "SELECT a.tvdb_id, a.last_watch_at, COUNT(w.tvdb_id) "
        "FROM series_activity a LEFT JOIN episode_watch w ON w.tvdb_id = a.tvdb_id "
        "GROUP BY a.tvdb_id, a.last_watch_at ORDER BY a.last_watch_at DESC",
    )
    print_table(
        ("tvdb_id", "last_watch_at", "age", "watches"),
        [(t, ts, age_str(ts), n) for t, ts, n in rows],
    )
    orphans = fetch_all(
        db,
        "SELECT DISTINCT w.tvdb_id FROM episode_watch w "
        "LEFT JOIN series_activity a ON a.tvdb_id = w.tvdb_id WHERE a.tvdb_id IS NULL",
    )
    if orphans:
        ids = ", ".join(str(r[0]) for r in orphans)
        print(f"  WARNING: watches without a series_activity row (run check_invariants.py): {ids}")


def dump_overrides(db):
    section("series overrides")
    rows = fetch_all(
        db, "SELECT tvdb_id, enabled, policy_json FROM series_override ORDER BY tvdb_id"
    )
    print_table(
        ("tvdb_id", "enabled", "policy_json (partial, merged over global)"),
        [(t, "yes" if e else "NO (unmanaged)", pj or "(none)") for t, e, pj in rows],
    )


def dump_deletions(db):
    for dry, label in ((1, "pending previews (dry_run=1)"), (0, "real deletions (dry_run=0)")):
        section(f"deletion_log — last {DELETION_LOG_LIMIT} {label}")
        rows = fetch_all(
            db,
            "SELECT id, tvdb_id, season, episode, reason, created_at, title "
            "FROM deletion_log WHERE dry_run = ? ORDER BY id DESC LIMIT ?",
            (dry, DELETION_LOG_LIMIT),
        )
        print_table(
            ("id", "tvdb_id", "episode", "reason", "created_at", "title"),
            [
                (i, t, f"S{s:02d}E{e:02d}", reason, created, title or "")
                for i, t, s, e, reason, created, title in rows
            ],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = parser.parse_args()

    try:
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        user_version = db.execute("PRAGMA user_version;").fetchone()[0]
    except sqlite3.Error as exc:
        print(f"ERROR: cannot open {args.db} read-only: {exc}", file=sys.stderr)
        return 2

    print(f"monitorr DB state dump — {args.db}")
    print(f"now (UTC): {datetime.now(UTC).isoformat()}")
    print(f"schema user_version: {user_version}")

    settings = dict(fetch_all(db, "SELECT key, value FROM setting"))
    dump_settings(settings)
    dump_policy(settings)
    dump_dry_run(settings)
    dump_sync_state(settings)
    dump_activity(db)
    dump_overrides(db)
    dump_deletions(db)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
