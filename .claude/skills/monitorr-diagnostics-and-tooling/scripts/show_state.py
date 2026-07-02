#!/usr/bin/env python3
"""show_state.py — deep-dive the persisted state of ONE show by TVDB id.

Prints: override/enabled state (with its partial policy JSON), activity age,
every recorded watch sorted by (season, episode) with the maximum marked as the
ANCHOR FLOOR, and the show's deletion_log rows (pending previews vs real).

The anchor floor is the furthest (season, episode) ever recorded as watched;
apply_window (src/monitorr/engine/window.py) never anchors below it. NOTE: at
runtime the floor is additionally clamped to episodes Sonarr currently lists —
an offline script cannot apply that clamp, so the value shown here is the raw
persisted maximum.

STDLIB ONLY — runs against any monitorr instance without the project venv.
Opens the DB in SQLite read-only URI mode; never writes.

Usage:
    python3 show_state.py --tvdb-id 12345 [--db /config/monitorr.db]

Exit codes: 0 = show found in at least one table, 1 = tvdb_id unknown to this
DB (no watches, activity, override or deletions), 2 = DB could not be opened.
"""

import argparse
import sqlite3
import sys
from datetime import UTC, datetime

DEFAULT_DB = "/config/monitorr.db"


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


def age_days(value):
    moment = parse_iso(value)
    if moment is None:
        return None
    return (datetime.now(UTC) - moment).total_seconds() / 86400


def fetch_all(db, query, params=()):
    try:
        return db.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"  (query failed: {exc})")
        return []


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    parser.add_argument("--tvdb-id", type=int, required=True, help="TVDB id of the show")
    args = parser.parse_args()

    try:
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        db.execute("PRAGMA user_version;")
    except sqlite3.Error as exc:
        print(f"ERROR: cannot open {args.db} read-only: {exc}", file=sys.stderr)
        return 2

    tvdb_id = args.tvdb_id
    found = False
    print(f"show state for tvdb_id={tvdb_id} — {args.db}")
    print(f"now (UTC): {datetime.now(UTC).isoformat()}")

    # Override / enabled state.
    print("\n== override ==")
    rows = fetch_all(
        db, "SELECT enabled, policy_json FROM series_override WHERE tvdb_id = ?", (tvdb_id,)
    )
    if rows:
        found = True
        enabled, policy_json = rows[0]
        print(f"  enabled: {'yes' if enabled else 'NO — monitorr does not manage this show'}")
        print(f"  policy_json (partial, merged over the global policy): {policy_json or '(none)'}")
    else:
        print("  no override row — global policy applies, show is enabled")

    # Activity.
    print("\n== activity ==")
    rows = fetch_all(db, "SELECT last_watch_at FROM series_activity WHERE tvdb_id = ?", (tvdb_id,))
    if rows:
        found = True
        last = rows[0][0]
        age = age_days(last)
        age_text = f"{age:.1f} days ago" if age is not None else "unparseable timestamp"
        print(f"  last_watch_at: {last}  ({age_text})")
        print("  (this clock gates GET re-arming via _is_armed and the grace sweeps)")
    else:
        print(
            "  no series_activity row — the show has no recorded viewing "
            "(auto-normalize reduces such shows to pilot-only)"
        )

    # Watches, sorted by (season, episode); the max is the anchor floor.
    print("\n== recorded watches (episode_watch) ==")
    rows = fetch_all(
        db,
        "SELECT season, episode, watched_at FROM episode_watch WHERE tvdb_id = ? "
        "ORDER BY season, episode",
        (tvdb_id,),
    )
    if rows:
        found = True
        floor = max((s, e) for s, e, _ in rows)
        for season, episode, watched_at in rows:
            marker = (
                "   <- ANCHOR FLOOR (max watch; runtime clamps to Sonarr-listed episodes)"
                if (season, episode) == floor
                else ""
            )
            print(f"  S{season:02d}E{episode:02d}  watched_at={watched_at}{marker}")
        print(f"  total: {len(rows)} watches; floor = S{floor[0]:02d}E{floor[1]:02d}")
    else:
        print("  (none)")

    # Deletion log for this show.
    print("\n== deletion_log ==")
    rows = fetch_all(
        db,
        "SELECT id, season, episode, reason, dry_run, created_at, title "
        "FROM deletion_log WHERE tvdb_id = ? ORDER BY id DESC",
        (tvdb_id,),
    )
    if rows:
        found = True
        for kind, label in ((1, "pending previews (dry_run=1)"), (0, "real deletions (dry_run=0)")):
            subset = [r for r in rows if r[4] == kind]
            print(f"  {label}: {len(subset)}")
            for row_id, season, episode, reason, _, created_at, title in subset:
                title_text = f"  {title}" if title else ""
                print(
                    f"    id={row_id}  S{season:02d}E{episode:02d}  reason={reason}  "
                    f"created_at={created_at}{title_text}"
                )
    else:
        print("  (none)")

    db.close()
    if not found:
        print(f"\ntvdb_id={tvdb_id} does not appear in any table of this DB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
