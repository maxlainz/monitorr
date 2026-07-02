#!/usr/bin/env python3
"""check_invariants.py — offline sanity checks over a monitorr SQLite database.

Prints one PASS/FAIL line per invariant and exits nonzero when anything fails.
STDLIB ONLY — runs against any monitorr instance without the project venv.
Opens the DB in SQLite read-only URI mode; never writes.

Checks (letters match the skill doc):
  (a) every episode_watch row's show has a series_activity row, and that row's
      last_watch_at >= MAX(watched_at) of the show (record_watch in store.py
      keeps activity as the MAX of watch timestamps).
  (b) at most one pending preview (dry_run=1) per (tvdb_id, season, episode) —
      the promise of the deletion_pending_unique partial index (migration 3).
  (c) if the dry_run setting is "0" (real mode), no dry_run=1 rows exist in
      deletion_log (set_dry_run clears all previews when disabling dry-run).
  (d) history_watermark (and last_full_sync) parse as ISO-8601 timestamps and
      are not in the future (> now + 1h = FAIL). A future watermark buries new
      plays below the incremental floor forever — the stale-watermark incident
      fixed in commit beb757c (v1.6.1).
  (e) PRAGMA user_version equals EXPECTED_USER_VERSION (the number of entries
      in the MIGRATIONS list of src/monitorr/db.py).

Usage:
    python3 check_invariants.py [--db /config/monitorr.db]

Exit codes: 0 = all checks pass, 1 = at least one FAIL, 2 = DB could not be opened.
"""

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

DEFAULT_DB = "/config/monitorr.db"

# (e) Expected schema version = len(MIGRATIONS) in src/monitorr/db.py.
# 4 as of v1.6.1 (2026-07-02). HOW TO UPDATE: when a migration is appended to
# MIGRATIONS (they are append-only — never edited), bump this constant by 1 and
# extend the checks if the new migration adds tables/indexes with invariants.
# Re-verify with:  grep -n 'CREATE TABLE\|user_version' src/monitorr/db.py
EXPECTED_USER_VERSION = 4

FUTURE_TOLERANCE = timedelta(hours=1)


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


class Checker:
    def __init__(self):
        self.failures = 0

    def report(self, name, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        suffix = f" — {detail}" if detail else ""
        print(f"{status}: {name}{suffix}")


def check_a_activity(db, checker):
    name = "(a) activity covers and bounds every watch"
    try:
        orphans = db.execute(
            "SELECT DISTINCT w.tvdb_id FROM episode_watch w "
            "LEFT JOIN series_activity a ON a.tvdb_id = w.tvdb_id WHERE a.tvdb_id IS NULL"
        ).fetchall()
        per_show = db.execute(
            "SELECT tvdb_id, MAX(watched_at) FROM episode_watch GROUP BY tvdb_id"
        ).fetchall()
        activity = dict(db.execute("SELECT tvdb_id, last_watch_at FROM series_activity"))
    except sqlite3.OperationalError as exc:
        checker.report(name, False, f"table missing: {exc}")
        return
    problems = [f"tvdb={row[0]} has watches but no series_activity row" for row in orphans]
    for tvdb_id, max_watched in per_show:
        if tvdb_id not in activity:
            continue  # already reported as an orphan
        act = parse_iso(activity[tvdb_id])
        top = parse_iso(max_watched)
        if act is None or top is None:
            problems.append(f"tvdb={tvdb_id} has an unparseable timestamp")
        elif act < top:
            problems.append(
                f"tvdb={tvdb_id} activity {activity[tvdb_id]} < max watched_at {max_watched}"
            )
    checker.report(name, not problems, "; ".join(problems))


def check_b_unique_previews(db, checker):
    name = "(b) at most one pending preview per episode"
    try:
        dupes = db.execute(
            "SELECT tvdb_id, season, episode, COUNT(*) FROM deletion_log WHERE dry_run = 1 "
            "GROUP BY tvdb_id, season, episode HAVING COUNT(*) > 1"
        ).fetchall()
        indexes = [row[1] for row in db.execute("PRAGMA index_list(deletion_log)")]
    except sqlite3.OperationalError as exc:
        checker.report(name, False, f"table missing: {exc}")
        return
    problems = [f"tvdb={t} S{s:02d}E{e:02d} has {n} preview rows" for t, s, e, n in dupes]
    if "deletion_pending_unique" not in indexes:
        problems.append("partial unique index deletion_pending_unique is missing (migration 3)")
    checker.report(name, not problems, "; ".join(problems))


def check_c_no_previews_in_real_mode(db, checker):
    name = '(c) real mode (dry_run="0") has no pending previews'
    try:
        row = db.execute("SELECT value FROM setting WHERE key = 'dry_run'").fetchone()
        pending = db.execute("SELECT COUNT(*) FROM deletion_log WHERE dry_run = 1").fetchone()[0]
    except sqlite3.OperationalError as exc:
        checker.report(name, False, f"table missing: {exc}")
        return
    raw = row[0] if row else None
    if raw != "0":
        checker.report(name, True, f"dry-run is ON (value={raw!r}); check not applicable")
        return
    checker.report(
        name,
        pending == 0,
        ""
        if pending == 0
        else f"{pending} preview rows remain "
        "(set_dry_run should have cleared them when dry-run was disabled)",
    )


def check_d_watermark(db, checker):
    name = "(d) sync timestamps parse and are not in the future"
    try:
        settings = dict(
            db.execute(
                "SELECT key, value FROM setting"
                " WHERE key IN ('history_watermark', 'last_full_sync')"
            )
        )
    except sqlite3.OperationalError as exc:
        checker.report(name, False, f"table missing: {exc}")
        return
    limit = datetime.now(UTC) + FUTURE_TOLERANCE
    problems = []
    for key in ("history_watermark", "last_full_sync"):
        raw = settings.get(key)
        if raw is None:
            continue  # not set = next sync is FULL; a valid state
        moment = parse_iso(raw)
        if moment is None:
            problems.append(f"{key}={raw!r} does not parse as ISO-8601")
        elif moment > limit:
            problems.append(
                f"{key}={raw} is in the future (> now+1h): stale-future-watermark, "
                "see incident beb757c — delete the key so the next sync is FULL"
            )
    checker.report(name, not problems, "; ".join(problems))


def check_e_user_version(db, checker):
    name = f"(e) schema user_version == {EXPECTED_USER_VERSION}"
    version = db.execute("PRAGMA user_version;").fetchone()[0]
    if version == EXPECTED_USER_VERSION:
        checker.report(name, True)
    elif version > EXPECTED_USER_VERSION:
        checker.report(
            name,
            False,
            f"user_version={version}: MIGRATIONS grew — update EXPECTED_USER_VERSION in "
            "this script to len(MIGRATIONS) from src/monitorr/db.py",
        )
    else:
        checker.report(
            name,
            False,
            f"user_version={version}: DB predates the current schema (app never migrated it?)",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB, help=f"database path (default {DEFAULT_DB})")
    args = parser.parse_args()

    try:
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        db.execute("PRAGMA user_version;")
    except sqlite3.Error as exc:
        print(f"ERROR: cannot open {args.db} read-only: {exc}", file=sys.stderr)
        return 2

    print(f"monitorr invariant checks — {args.db}")
    checker = Checker()
    check_a_activity(db, checker)
    check_b_unique_previews(db, checker)
    check_c_no_previews_in_real_mode(db, checker)
    check_d_watermark(db, checker)
    check_e_user_version(db, checker)
    db.close()

    if checker.failures:
        print(f"\n{checker.failures} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
