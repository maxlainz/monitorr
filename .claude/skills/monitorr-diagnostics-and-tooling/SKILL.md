---
name: monitorr-diagnostics-and-tooling
description: Runnable read-only diagnostic scripts for a live or stopped monitorr instance plus safe DB access rules. Use to inspect /config/monitorr.db (settings, policy, dry-run, watermark, last_sync, watches, anchor floor, deletion log), run offline invariant checks, deep-dive one show, snapshot a WAL database, or turn on DEBUG log tracing. NOT for symptom-to-fix runbooks (use monitorr-debugging-playbook), formula definitions (monitorr-window-engine-reference), or config semantics (monitorr-config-and-flags).
---

# monitorr diagnostics and tooling

Hands-on tooling to inspect the persisted state of any monitorr instance — running or stopped —
without the project venv and without ever writing to the database. Three stdlib-only Python 3
scripts live in `scripts/` next to this file.

> Scripts verified against the schema in `src/monitorr/db.py` and syntax-checked
> (`python3 -m py_compile`); **not executed against a live DB**. Schema/keys verified at v1.6.1,
> 2026-07-02.

Terms used below: the **anchor** is the last-watched episode the GET/KEEP window is applied
around; the **anchor floor** is the furthest `(season, episode)` ever recorded in the
`episode_watch` table — the window never anchors below it. The **watermark**
(`history_watermark` setting) is the newest Plex play timestamp already scanned; an
**incremental sync** only sweeps history newer than it, a **full sync** rescans every show.
**Dry-run** (ON by default) makes every Sonarr write a no-op and records would-be deletions as
**pending previews** (`deletion_log.dry_run=1`). **WAL** is SQLite's write-ahead-log journal
mode (the DB is `monitorr.db` plus sidecar `-wal`/`-shm` files).

## The scripts

All three: stdlib only (no repo imports), `--db` flag defaulting to `/config/monitorr.db`,
read-only (SQLite URI `mode=ro`), exit `2` when the DB cannot be opened.

| Script | What it prints | Exit codes |
|---|---|---|
| `scripts/dump_state.py` | Full overview: `PRAGMA user_version`; every `setting` key (token/api-key/secret values redacted to 4 chars + `…`); policy JSON pretty-printed; dry-run state decoded; `history_watermark` + `last_full_sync` + `last_sync` with human ages; per-show activity with age and watch counts; overrides; last 20 pending previews and last 20 real deletions | 0 always (informational) |
| `scripts/check_invariants.py` | PASS/FAIL per invariant: (a) activity row exists and `>=` max watch per show; (b) one pending preview per episode + `deletion_pending_unique` index present; (c) real mode implies zero previews; (d) watermark/last_full_sync parse and are not `> now+1h` (the beb757c stale-future-watermark incident); (e) `user_version == 4` (see the constant's comment for how to bump it) | 0 all pass, 1 any FAIL |
| `scripts/show_state.py` | One show by `--tvdb-id N`: override/enabled state, activity age, all watches sorted by `(season, episode)` with the max marked ANCHOR FLOOR, its deletion_log rows split preview/real | 0 found, 1 tvdb_id unknown |

Run examples (from the repo root, host-side against a compose bind mount `./config:/config`):

```bash
python3 .claude/skills/monitorr-diagnostics-and-tooling/scripts/dump_state.py --db ./config/monitorr.db
python3 .claude/skills/monitorr-diagnostics-and-tooling/scripts/check_invariants.py --db ./config/monitorr.db
python3 .claude/skills/monitorr-diagnostics-and-tooling/scripts/show_state.py --tvdb-id 12345 --db ./config/monitorr.db
```

## Running inside the container

The image is `python:3.12-slim`-based (`Dockerfile`), so `python3` is on PATH inside the
container (compose service/container name: `monitorr`). The scripts are not baked into the
image; pipe them in over stdin — `python3 -` reads the program from stdin and everything after
`-` becomes `sys.argv`:

```bash
docker exec -i monitorr python3 - --db /config/monitorr.db \
  < .claude/skills/monitorr-diagnostics-and-tooling/scripts/dump_state.py
docker exec -i monitorr python3 - --db /config/monitorr.db \
  < .claude/skills/monitorr-diagnostics-and-tooling/scripts/check_invariants.py
docker exec -i monitorr python3 - --tvdb-id 12345 --db /config/monitorr.db \
  < .claude/skills/monitorr-diagnostics-and-tooling/scripts/show_state.py
```

Alternatively `docker cp scripts/dump_state.py monitorr:/tmp/` then
`docker exec monitorr python3 /tmp/dump_state.py`.

## Safe DB access rules (read before touching monitorr.db)

The app opens the DB in WAL mode (`db.py: PRAGMA journal_mode=WAL`). Rules:

1. **Read-only always.** Open with the URI form the scripts use:
   `sqlite3.connect("file:/config/monitorr.db?mode=ro", uri=True)`, or from the sqlite3 CLI:
   `sqlite3 "file:/config/monitorr.db?mode=ro"`. Never write while the app runs — the app
   assumes it is the only writer, and a stray write can corrupt sync state in ways that trigger
   destructive behavior (see the incident catalog in `monitorr-failure-archaeology`).
2. **Snapshots must copy all three files together**: `monitorr.db`, `monitorr.db-wal`,
   `monitorr.db-shm`. Copying only `monitorr.db` while the app runs silently loses every
   transaction still in the WAL. Safe snapshot of a live instance:
   ```bash
   docker exec monitorr sh -c 'cd /config && tar cf - monitorr.db monitorr.db-wal monitorr.db-shm 2>/dev/null || tar cf - monitorr.db' > /tmp/monitorr-db-snapshot.tar
   ```
   (the `-wal`/`-shm` files may not exist after a clean shutdown — that is fine).
3. **Reading a live DB in `mode=ro` is safe** (WAL allows concurrent readers); results reflect
   the last committed transaction.
4. If a fix requires *changing* DB state (e.g. deleting a stale `history_watermark`), that is a
   behavior-changing action: stop the container first, back up per rule 2, and follow
   `monitorr-change-control` before scripting any write.

## Schema quick reference (source of truth: `src/monitorr/db.py`)

4 migrations as of v1.6.1; `PRAGMA user_version` = number of applied migrations. Tables:
`setting(key, value)` · `series_override(tvdb_id, enabled, policy_json)` ·
`episode_watch(tvdb_id, season, episode, watched_at)` (PK on the id/season/episode triple) ·
`series_activity(tvdb_id, last_watch_at)` ·
`deletion_log(id, tvdb_id, season, episode, title, episode_file_id, reason, dry_run, created_at)`
with partial unique index `deletion_pending_unique ON (tvdb_id, season, episode) WHERE dry_run=1`.
Migration 4 is a data migration (`DELETE FROM setting WHERE key = 'last_full_sync'`).

Setting keys (`src/monitorr/constants.py`): `plex_client_id`, `plex_account_token`,
`plex_server_uri`, `plex_server_token`, `plex_server_name`, `plex_server_id`, `sonarr_url`,
`sonarr_api_key`, `policy`, `dry_run`, `watched_threshold`, `plex_user_filter`,
`webhook_secret`, `history_watermark`, `last_full_sync` — plus `last_sync`, written by
`src/monitorr/sync.py` (module-private `_LAST_SYNC`). All timestamps are ISO-8601 UTC strings.

Value encodings worth knowing when reading raw rows:

- `dry_run`: `"1"` / `"0"`; **key absent = dry-run ON** (`get_dry_run` in `engine/policy.py`
  returns `True` when the row is missing, `raw == "1"` otherwise).
- `last_sync` JSON shape (what `sync._run` persists):
  `{"at": "<ISO>", "mode": "full"|"incremental", "shows": N, "matched": N, "normalized": N,
  "searched": N}`.
- `deletion_log.reason` values: `ahead` / `keep` (window trims, `engine/window.py`) and
  `completed` / `dormant` / `grace_watched` / `grace_unwatched` (grace sweep,
  `engine/grace.py`).
- `series_override.policy_json` is a **partial** policy: fields merge over the global policy
  (`effective_policy`). Field semantics: `monitorr-config-and-flags`.

## Log-based diagnosis

Set `MONITORR_LOG_LEVEL=DEBUG` (env var, e.g. in compose) and restart. Log line format is
`%(asctime)s %(levelname)s %(name)s: %(message)s` (`src/monitorr/logging.py`); logger names are
module paths, so grep by `monitorr.engine.window`, `monitorr.sync`, `monitorr.plex.client`.

What DEBUG unlocks (verified message formats, quoted from the code):

- `monitorr.engine.window` (`engine/window.py`) — the deep window trace, two DEBUG lines per
  `apply_window` call:
  - `"apply_window tvdb=%s anchor=S%02dE%02d idx=%d get=%d%s keep=%d%s dry_run=%s ahead=%s search_ahead=%s"`
    — `search_ahead` is "the most important line when chasing the re-import bug": what gets
    searched and can drag in a season pack.
  - `"apply_window tvdb=%s unmonitor=%d in_window=%d queued=%d surplus=%s"` — the season-pack
    guard's view: `surplus` is what it cancels from the queue.
- `monitorr.sync` (`sync.py`) — the single most useful per-show line is at **INFO** (no DEBUG
  needed): `"sync show tvdb=%s title=%s rating_key=%s allLeaves=%d history=%d merged_keys=%s
  anchor=S%02dE%02d"` — how each source contributed and where the window anchors; a
  back-catalog miss shows as an anchor below the real max. Also INFO:
  `"sync completed (%s): %s"`. DEBUG adds `"normalize tvdb=%s (%s) to pilot-only"`.
- `monitorr.plex.client` (`plex/client.py`) — DEBUG:
  `"history sweep: pages=%d episode_rows=%d shows=%d per_show=%s"` (what the history endpoint
  actually returned per show).
- Re-anchoring is logged at INFO by the window:
  `"re-anchored tvdb=%s from S%02dE%02d to S%02dE%02d (persisted furthest watch)"`.

Grab logs with `docker logs monitorr --since 1h` (add `2>&1 | grep 'tvdb=12345'` to follow one
show through a whole sync).

## Which script for which symptom

Diagnosis-to-fix runbooks live in `monitorr-debugging-playbook`; this table maps its symptoms to
the evidence each script produces.

| Symptom | Script | What to look at |
|---|---|---|
| Already-watched episodes re-downloaded (anchor regression) | `show_state.py --tvdb-id N` | Is the ANCHOR FLOOR at the real furthest watch? Missing watches → the history sweep never recorded them |
| Deletions not happening | `dump_state.py` | Dry-run section (ON = previews only); pending previews list shows what real mode would delete |
| Sync appears stuck / never incremental / never full | `dump_state.py` | `history_watermark` / `last_full_sync` / `last_sync` ages and mode |
| New plays ignored forever after a server switch | `check_invariants.py` | Check (d): future or stale watermark (incident beb757c) |
| Show downloads/deletes in a loop (oscillation) | `show_state.py` + `dump_state.py` | Activity age vs `dormant_days` / `grace_unwatched_days` (the `_is_armed` gate); repeated deletion_log rows with the same episode |
| Wrong episodes previewed for deletion | `dump_state.py` + `show_state.py` | `reason` column (`ahead`/`keep`/grace reasons) tells which mechanism chose each episode |
| DB suspected inconsistent after crash/restore | `check_invariants.py` | Any FAIL line; (e) catches a DB from a different app version |
| Show ignored by monitorr | `show_state.py` | Override `enabled: NO`, or no watches + auto-normalize (pilot-only is expected) |

## When NOT to use

- Symptom → diagnosis → fix runbooks: `monitorr-debugging-playbook` (this skill only produces
  the evidence those runbooks consume).
- Window/grace/policy formula definitions: `monitorr-window-engine-reference`.
- Meaning, defaults and validation of env vars / policy fields / setting keys:
  `monitorr-config-and-flags`.
- Full incident stories behind the checks (e.g. beb757c): `monitorr-failure-archaeology`.
- Starting/operating the container, log levels in normal operation: `monitorr-run-and-operate`.
- Anything that WRITES to the DB or changes behavior: `monitorr-change-control` first.

## Provenance and maintenance

Everything above was read from the code at v1.6.1 (2026-07-02). Re-verify before trusting:

| Fact | Re-verification command (repo root) |
|---|---|
| Tables/columns and migration count (scripts must be updated when `MIGRATIONS` grows) | `grep -n "CREATE TABLE" src/monitorr/db.py` and count the entries of `MIGRATIONS` in `src/monitorr/db.py` |
| `EXPECTED_USER_VERSION` in `check_invariants.py` (currently 4) | `grep -n "user_version" src/monitorr/db.py .claude/skills/monitorr-diagnostics-and-tooling/scripts/check_invariants.py` |
| Setting keys | `grep -n '= "' src/monitorr/constants.py` |
| `last_sync` key + JSON shape | `grep -n "_LAST_SYNC\|set_setting(\s*_LAST_SYNC" src/monitorr/sync.py` |
| Dry-run encoding (`"1"`/`"0"`, absent = ON) | `grep -n "def get_dry_run\|def set_dry_run" -A 3 src/monitorr/engine/policy.py` |
| Deletion reasons | `grep -rn '"ahead"\|"keep"\|"completed"\|"dormant"\|"grace_' src/monitorr/engine/` |
| Default DB path (`/config/monitorr.db`) | `grep -n "config_dir\|db_path" src/monitorr/config.py` |
| WAL mode | `grep -n "journal_mode" src/monitorr/db.py` |
| Quoted log message formats | `grep -n "logger.debug\|logger.info" src/monitorr/engine/window.py src/monitorr/sync.py src/monitorr/plex/client.py` |
| Container name / bind mount | `grep -n "container_name\|/config" docker-compose.yml` |
| `python3` in the image | `grep -n "FROM python" Dockerfile` |

Maintenance rule: whenever a migration is appended to `MIGRATIONS` in `src/monitorr/db.py`,
update `EXPECTED_USER_VERSION` in `scripts/check_invariants.py` in the same commit, and extend
the scripts if the migration adds tables, columns or indexes. Migrations are append-only —
published entries are never edited.
