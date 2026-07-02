---
name: monitorr-debugging-playbook
description: Symptom-to-fix runbooks for a live monitorr instance — re-downloading already-watched episodes, nothing being deleted, sync stuck/always-full, silent Plex webhook, startup/DB permission crashes, torrents piling up, wrong grace deletions, show reduced to pilot. Use when diagnosing misbehavior. NOT for incident history (monitorr-failure-archaeology), window formulas (monitorr-window-engine-reference), the season-pack fix campaign (monitorr-season-pack-campaign), or config reference (monitorr-config-and-flags).
---

# monitorr debugging playbook

Symptom → diagnosis → fix runbooks for a running monitorr instance. Every log string, route,
SQL table and setting key below was verified against the code at v1.6.1 (2026-07-02); the
"Provenance and maintenance" section tells you how to re-verify each one.

Terms used throughout (defined once):

- **anchor** — the furthest-watched episode; the window is computed around it.
- **GET** — the N episodes/seasons AHEAD of the anchor that monitorr keeps monitored+downloaded.
- **KEEP** — the N behind the anchor whose files are retained; everything further back is deleted.
- **Always-Have** — per-policy patterns (default `S01E01`) whose episodes are never deleted.
- **grace** — time-based deletion sweeps (watched / unwatched / dormant / completed).
- **dry-run** — master switch (ON by default) that turns every Sonarr write into a logged no-op.
- **watermark / full / incremental sync** — incremental syncs only scan Plex plays newer than the
  stored watermark; a full sync rescans every managed show.
- **PMS** — Plex Media Server. **ratingKey** — Plex's per-item id (volatile across re-adds).
- **allLeaves** — Plex endpoint listing a show's episodes currently in the library (on disk).
- **scrobble** — Plex's "playback finished" event (`media.scrobble`), used by the webhook.
- **season pack** — a single torrent containing a whole season; one download, many queue rows.

## Getting at the evidence (used by every runbook)

All commands are copy-pasteable from the directory holding `docker-compose.yml` (bind mount
`./config:/config`, container name `monitorr` — see `docker-compose.yml`).

```bash
# Logs (format: "TIMESTAMP LEVEL logger.name: message" — src/monitorr/logging.py)
docker logs monitorr --since 24h 2>&1 | grep -F "<string from the runbook>"

# Turn on DEBUG tracing (window decisions only log at DEBUG), then restart:
#   environment: - MONITORR_LOG_LEVEL=DEBUG   → docker compose up -d

# The SQLite DB, read-only, from the host (safe while the app runs — WAL mode):
sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT key, value FROM setting;"

# The image has NO sqlite3 CLI; inside the container use python instead:
docker exec monitorr python -c "import sqlite3
for r in sqlite3.connect('file:/config/monitorr.db?mode=ro', uri=True).execute(
    'SELECT key, value FROM setting'): print(r)"

# Liveness + exact running version:
curl -s http://localhost:8080/health     # {"status":"ok"}
curl -s http://localhost:8080/version    # {"version":..., "build_sha":..., "build_date":...}

# Sonarr's view. One-time setup — read URL/key from the setting table:
SONARR_URL=$(sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT value FROM setting WHERE key='sonarr_url';")
KEY=$(sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT value FROM setting WHERE key='sonarr_api_key';")
curl -s -H "X-Api-Key: $KEY" "$SONARR_URL/api/v3/series"                    # find tvdbId/id
curl -s -H "X-Api-Key: $KEY" "$SONARR_URL/api/v3/episode?seriesId=<id>"     # episode list
curl -s -H "X-Api-Key: $KEY" "$SONARR_URL/api/v3/queue?pageSize=1000"       # download queue
```

`tvdb_id` is the cross-system show key. Find it in the UI at `/series` (each row links to
`/series/{tvdb_id}`) or as `tvdbId` in Sonarr's `/api/v3/series` response.

For richer prebuilt state dumps and invariant checks, see `monitorr-diagnostics-and-tooling` —
it also owns the complete verified log-format inventory; the runbooks below quote only the
strings they grep for.

## Symptom index

| Symptom | Runbook |
|---|---|
| Already-watched episodes are re-downloading | [R1](#r1-already-watched-episodes-are-re-downloading) |
| Nothing is being deleted / monitorr seems inert | [R2](#r2-nothing-is-being-deleted--monitorr-seems-inert) |
| Sync never runs / always full / seems stuck | [R3](#r3-sync-never-runs--always-full--seems-stuck) |
| Webhook doesn't fire | [R4](#r4-webhook-doesnt-fire) |
| Startup crash / "unable to open database file" | [R5](#r5-startup-crash--unable-to-open-database-file) |
| Torrents piling up / same pack grabbed repeatedly | [R6](#r6-torrents-piling-up--same-pack-grabbed-repeatedly) |
| Grace deleted something it shouldn't / kept something it should delete | [R7](#r7-grace-deleted-something-it-shouldnt--kept-something-it-should-delete) |
| A show was reduced to just the pilot unexpectedly | [R8](#r8-a-show-was-reduced-to-just-the-pilot-unexpectedly) |

---

## R1: Already-watched episodes are re-downloading

THE historical failure shape of this project: the anchor slides backward when derived from
volatile state (on-disk `allLeaves`, a stale ratingKey, a pre-cascade Sonarr snapshot) instead of
the persisted watch store, and the loop is SELF-AMPLIFYING (each wrong re-download re-enters the
library and re-advances the false anchor). Full incident stories with commit hashes:
`monitorr-failure-archaeology`. Canonical window/floor formulas: `monitorr-window-engine-reference`.

1. **Confirm the running version.** `curl -s http://localhost:8080/version`. Anchor-floor fixes
   landed in v1.5.1 and v1.6.1; anything older than 1.6.1 → upgrade first, this bug class is
   likely already fixed.
2. **Check what monitorr believes was watched** (the only authority for destructive decisions —
   table `episode_watch`, `src/monitorr/db.py`):
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT season, episode, watched_at FROM episode_watch
      WHERE tvdb_id = <TVDB_ID> ORDER BY season, episode;"
   ```
   - Furthest row matches the real viewing point → the store is fine; go to step 4.
   - Furthest row is BEHIND the real viewing point → plays are not being recorded. Check the
     user filter (step 6), a Plex server relink (R3 step 6), or a failing history endpoint
     (grep `could not read Plex play history; back-catalog degraded`, WARNING, `sync.py`).
     Then force a full sync: `curl -s -X POST http://localhost:8080/sync` and re-query.
3. **Look for the anchor floor firing.** `engine/window.py` logs at INFO when it corrects a
   too-low anchor:
   ```bash
   docker logs monitorr 2>&1 | grep -F "re-anchored tvdb="
   # → "re-anchored tvdb=X from SxxEyy to SxxEyy (persisted furthest watch)"
   ```
   - Seen with the correct target → the floor works; the re-download must come from elsewhere
     (Sonarr itself: RSS on episodes monitorr never monitored, a season pack — see R6, or
     another tool writing to Sonarr).
   - Never seen AND the store (step 2) is ahead of the observed anchor → floor not engaging;
     go to step 5 (numbering mismatch).
4. **Trace the per-show sync line** (INFO, `sync.py`, "the single most useful debug line"):
   ```bash
   docker logs monitorr 2>&1 | grep -F "sync show tvdb=<TVDB_ID>"
   # → "sync show tvdb=.. title=.. rating_key=.. allLeaves=N history=M merged_keys=[..] anchor=SxxEyy"
   ```
   `anchor=` below the real max with `history=0` → the Plex history contributed nothing for this
   show (history disabled on the PMS, title-correlation failure, or user filter).
5. **DEBUG window trace.** Set `MONITORR_LOG_LEVEL=DEBUG`, restart, wait for a sync, then:
   ```bash
   docker logs monitorr 2>&1 | grep -F "apply_window tvdb=<TVDB_ID>"
   # line 1: "apply_window tvdb=.. anchor=SxxEyy idx=N get=Ne keep=Ne dry_run=..
   #          ahead=[(s,e),..] search_ahead=[(s,e),..]"
   # line 2: "apply_window tvdb=.. unmonitor=N in_window=N queued=N surplus=[(s,e),..]"
   ```
   `search_ahead` containing already-watched episodes is the smoking gun: the window is anchored
   too low and is actively searching seen content.
6. **Check for a Plex/Sonarr numbering disagreement.** The floor only counts watches whose
   `(season, episode)` key exists in Sonarr's episode list (the clamp in `apply_window`,
   `engine/window.py`). If Plex numbers the show differently (absolute order, different TVDB
   season mapping), the persisted watches never match and the floor silently uses only the
   matching subset. Compare:
   ```bash
   curl -s -H "X-Api-Key: $KEY" "$SONARR_URL/api/v3/episode?seriesId=<id>" \
     | python3 -c "import json,sys; print(sorted((e['seasonNumber'],e['episodeNumber']) for e in json.load(sys.stdin)))"
   ```
   against the `episode_watch` keys from step 2. Keys present in the DB but absent from Sonarr →
   numbering mismatch; fix the series' season mapping in Sonarr or the Plex agent, then re-sync.
7. **Also grep the poller side** — a live watch logs
   `watched SxxEyy of <title> (tvdb=..)` or `completed SxxEyy of <title> (session closed, ...)`
   (INFO, `plex/poller.py`); their absence while someone is watching points at the user filter
   or `watched_threshold` (setting keys `plex_user_filter`, `watched_threshold`).
8. **If you conclude a code change is needed**: anchor/sync/window changes require adversarial
   review against the incident list plus a regression test — see `monitorr-change-control`
   before touching anything.

## R2: Nothing is being deleted / monitorr seems inert

1. **Dry-run is ON by default** and blocks every Sonarr write (`engine/policy.py::get_dry_run`
   returns True when the key is absent):
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT value FROM setting WHERE key='dry_run';"
   ```
   - No row, or `1` → dry-run ON. This is the answer in the vast majority of "inert" reports.
     Deletions still show up as pending previews: the `/deletions` page ("pending" list) and
     `deletion_log` rows with `dry_run=1`:
     ```bash
     sqlite3 "file:./config/monitorr.db?mode=ro" \
       "SELECT tvdb_id, season, episode, reason, created_at
        FROM deletion_log WHERE dry_run=1 ORDER BY id DESC LIMIT 30;"
     ```
     Previews present and correct → disable dry-run in Settings (this clears all previews —
     intentional, `set_dry_run` in `engine/policy.py`). Logs in dry-run say
     `[dry-run] would delete SxxEyy (reason)`; real mode says `deleted SxxEyy (reason)`.
   - `0` → dry-run OFF; continue.
2. **Are both dependencies configured?** Sync aborts early otherwise
   (WARNING `sync: Plex or Sonarr are not ready`, `sync.py`):
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT key FROM setting WHERE key IN
      ('sonarr_url','sonarr_api_key','plex_server_uri','plex_server_token','plex_client_id');"
   ```
   All five rows must exist. Missing Plex rows → relink on `/settings`; missing Sonarr rows →
   save Sonarr URL + API key there.
3. **Is the series disabled or overridden?**
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT tvdb_id, enabled, policy_json FROM series_override WHERE tvdb_id=<TVDB_ID>;"
   ```
   `enabled=0` → monitorr skips the show entirely (window AND grace). `policy_json` non-NULL →
   a per-series policy overrides the global one; read it before reasoning about defaults.
4. **Grace not yet expired.** Grace fires on inactivity age vs the policy days. Defaults
   (`engine/policy.py::Policy`): `grace_watched_days=7`, `grace_unwatched_days=365`,
   `dormant_days=None` (disabled), `grace_completed_days=30`. Check the clock:
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT tvdb_id, last_watch_at FROM series_activity WHERE tvdb_id=<TVDB_ID>;"
   ```
   Age below the relevant grace → nothing to delete yet; this is correct behavior.
   NOTE: the grace sweep iterates only shows WITH a `series_activity` row (`grace.py::sweep`),
   i.e. shows with at least one recorded watch. A never-watched show is never grace-swept —
   its trimming comes from auto-normalize (R8) and the window, not from grace.
5. **KEEP / Always-Have protection.** Episodes inside the KEEP window (anchor plus
   `keep_count - 1` behind), and anything matching an `always_have` pattern
   (default `["S01E01"]`; grammar `S01E01`, `S*E01`, `S01`, `S*`) are deliberately retained.
   Effective policy lives in `SELECT value FROM setting WHERE key='policy';` (global JSON) merged
   with the per-series `policy_json` from step 3. Full semantics:
   `monitorr-window-engine-reference`.
6. **Window trims only run when a window runs.** The ahead/behind trims happen inside
   `apply_window`, triggered by a watch (poller/webhook) or by sync. If no sync ever runs, see
   R3. Grace runs on its own loop every `MONITORR_GRACE_SWEEP_INTERVAL` s (default 3600).

## R3: Sync never runs / always full / seems stuck

Sync state lives in three `setting` keys: `history_watermark` (newest Plex play scanned),
`last_full_sync` (timestamp of the last FULL reconciliation) and `last_sync` (JSON summary of
the last run of either kind). Full is entered iff forced, or `history_watermark` is absent, or
`full_sync_interval` elapsed since `last_full_sync` (`sync.py::_run`, `_floor_elapsed`).

1. **Read the sync state:**
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT key, value FROM setting
      WHERE key IN ('last_sync','history_watermark','last_full_sync');"
   ```
   `last_sync` is JSON: `{"at": .., "mode": "full"|"incremental", "shows": N, "matched": N,
   "normalized": N, "searched": N}`. It also renders on the index page `/`.
2. **Never runs at all?** `MONITORR_SYNC_INTERVAL=0` disables the periodic loop (`main.py`
   starts `_sync_loop` only when `sync_interval > 0`); `MONITORR_SYNC_ON_STARTUP` (default true)
   only fires when a FULL is overdue (`sync.full_sync_due()`), so a normal restart does nothing.
   Check the container env, then trigger manually (always forces full):
   ```bash
   curl -s -X POST http://localhost:8080/sync
   docker logs monitorr --since 10m 2>&1 | grep -F "sync completed"
   # → "sync completed (full): {'shows': .., 'matched': .., 'normalized': .., 'searched': ..}"
   ```
   No `sync completed` line → grep `sync: Plex or Sonarr are not ready` (deps missing, R2.2)
   and `error in the periodic sync` / `error syncing tvdb=` (per-show exceptions, with traceback).
3. **Always full?** Two normal causes, one degraded state:
   - `history_watermark` row absent → every cycle resolves to full until one full sync SUCCEEDS
     with a working history endpoint (only then is the watermark stamped).
   - The rolling floor elapsed: `full_sync_interval` (env `MONITORR_FULL_SYNC_INTERVAL`, default
     2592000 s = 30 d, `0` disables the floor) since `last_full_sync` → a periodic full is
     expected and correct.
   - **Blind-sweep degradation (by design):** when the Plex history endpoint fails, an
     incremental sync is PROMOTED to full, and a blind sweep advances NOTHING (neither
     `history_watermark` nor `last_full_sync`), so every cycle stays full until the endpoint
     recovers. Confirm:
     ```bash
     docker logs monitorr 2>&1 | grep -F "Plex history unavailable; promoting incremental sync to full"
     docker logs monitorr 2>&1 | grep -F "could not read Plex play history; back-catalog degraded"
     ```
     Seen repeatedly → fix the Plex side (old PMS, history disabled) rather than monitorr.
4. **Seems stuck / "skipping"?** One sync at a time via an in-process `asyncio.Lock`
   (`sync.py::_LOCK`, `is_running()`); an overlapping trigger logs
   `sync already in progress; skipping` (INFO). The lock is not persisted, so a restart always
   clears a wedged sync. A genuinely long full sync on a big library is normal — watch the
   per-show `sync show tvdb=` lines progress.
5. **Full expected but not happening after connecting both deps?** Saving Sonarr settings and
   completing a Plex link both enqueue a forced full (`web/routes.py::_maybe_trigger_full_sync`)
   — but only when BOTH sides are already configured and no sync is running.
6. **After relinking a different PMS**, `history_watermark` and `last_full_sync` are reset on
   purpose (`web/routes.py::_store_server`, per-server state keyed on setting `plex_server_id`),
   so the next cycle is full. If you relinked and they were NOT reset, you are on a pre-1.6.1
   version — upgrade.

## R4: Webhook doesn't fire

Context: the webhook is OPTIONAL; the session poller is the primary detection path, so watches
often flow fine with a dead webhook. Webhooks require **Plex Pass** on the Plex account
(`plex/webhook.py` module docstring). The route is `POST /webhook/plex/{secret}`
(`web/routes.py::plex_webhook`).

1. **Know the pitfall first: a secret mismatch returns HTTP 200 with body
   `{"status":"forbidden"}` — NOT a 403.** Every outcome of this route is HTTP 200 (so Plex
   never retries); the body statuses are: `forbidden` (bad secret), `ignored` (not a
   `media.scrobble` of an episode / no payload), `filtered` (user filter), `unlinked` (Plex not
   linked), `no-tvdb` (show has no TVDB id), `error` (exception, logged), `ok`. Consequence:
   neither Plex nor a curl status code will ever show you an auth failure — read the body.
2. **Find the effective secret.** Env `MONITORR_WEBHOOK_SECRET` wins when set (pinned, not
   persisted); otherwise it is the `setting` row:
   ```bash
   docker exec monitorr printenv MONITORR_WEBHOOK_SECRET   # empty → DB secret applies
   sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT value FROM setting WHERE key='webhook_secret';"
   ```
3. **Probe the route from where the PMS lives** (substitute the PMS-reachable host):
   ```bash
   curl -s -X POST "http://<monitorr-host>:8080/webhook/plex/<SECRET>"      # → {"status":"ignored"}
   curl -s -X POST "http://<monitorr-host>:8080/webhook/plex/WRONG"         # → {"status":"forbidden"}
   ```
   `ignored` with the real secret proves auth + reachability; anything network-level (timeout,
   refused) → the PMS cannot reach monitorr (Docker network, firewall) — fix routing, not code.
4. **Check registration state.** The `/settings` page shows whether monitorr's URL is present in
   the Plex account's webhook list (`webhook_registered`). The registered URL is built from the
   browser's address (`request.base_url`) — **if you completed the Plex link while browsing via
   `localhost`, Plex was given a `localhost` URL it can never deliver to.** Re-register with a
   reachable URL from `/settings` (form posts to `/webhook/register`). Registration failures
   log `could not register Plex webhook` (`web/routes.py`). You can also inspect the account's
   webhook list at app.plex.tv → Settings → Webhooks.
5. **User filter mismatch → `{"status":"filtered"}`.** The payload's `Account.title` must be in
   the filter (`SELECT value FROM setting WHERE key='plex_user_filter';`, JSON list; empty list =
   no filtering). Filter names are matched as-is on the webhook path — a display-name vs
   username discrepancy silently filters everything.
6. **Regenerated secret invalidates the old URL.** `/webhook/regenerate` rotates the secret and
   re-registers in Plex only if monitorr was already registered (`resync_webhook`). A stale URL
   pasted manually elsewhere returns `forbidden` forever after a rotation.
7. **Processing errors** (Sonarr down, etc.) return `{"status":"error"}` and log
   `error processing webhook for <ratingKey>` with a traceback.

## R5: Startup crash / "unable to open database file"

The DB lives at `<config_dir>/monitorr.db` (`config.py::Settings.db_path`); `config_dir`
defaults to `/config` (env `MONITORR_CONFIG_DIR`, baked into the image as `/config`).

1. **Docker: check the entrypoint privilege drop.** `docker-entrypoint.sh` starts as root,
   remaps user `app` to `PUID`/`PGID` (default 1000:1000), `chown -R app:app /config`, then
   drops via gosu. Its startup line:
   ```bash
   docker logs monitorr 2>&1 | grep -F "monitorr: starting as app (uid="
   ```
   - Line present with the expected uid/gid, but sqlite still fails → the bind-mount source is
     on a filesystem where chown is ineffective (NFS root-squash, some NAS shares). Set
     `PUID`/`PGID` to the mount's actual owner (`id -u`/`id -g` on the host) and ensure the
     directory exists and is writable by that id.
   - Line ABSENT → the container did not start as root (compose `user:` override): the
     entrypoint skips remap+chown entirely (`if [ "$(id -u)" -ne 0 ]; then exec "$@"; fi`).
     Either remove `user:` and use PUID/PGID, or chown the host directory to that uid yourself.
2. **Confirm the crash is the DB.** `sqlite3.OperationalError: unable to open database file` in
   `docker logs monitorr` means `/config` (or `monitorr.db` itself) is not writable/creatable by
   the running uid. Success looks like INFO `monitorr started (db=/config/monitorr.db)`
   (`main.py::lifespan`).
3. **Local dev (no Docker):** the default `config_dir=/config` almost never exists/writes on a
   dev machine. Set it explicitly:
   ```bash
   MONITORR_CONFIG_DIR=./config uv run monitorr
   ```
   (`init_db` creates the directory; `.env` in the CWD is also read — `config.py`.)
4. **Instant crash with a pydantic ValidationError** → an interval env var failed validation
   (fail-fast by design, `config.py`): `MONITORR_PLEX_POLL_INTERVAL` and
   `MONITORR_GRACE_SWEEP_INTERVAL` must be >= 1; `MONITORR_SYNC_INTERVAL` and
   `MONITORR_FULL_SYNC_INTERVAL` must be >= 0 (0 = disabled). Fix the env value.
5. **Verify recovery:** `curl -s http://localhost:8080/health` → `{"status":"ok"}`.

## R6: Torrents piling up / same pack grabbed repeatedly

Shape: each sync searches the missing GET episodes → Sonarr grabs a full season pack → monitorr's
season-pack guard cancels the surplus queue rows with `removeFromClient=false` — and since all
queue rows of a pack map to ONE torrent, cancelling surplus cancels the whole download while the
torrent stays (paused/seeding) in the client → next sync repeats. Nothing surplus is ever
imported (the guard always wins), but grabs and dead torrents accumulate. Confirm the signature:

```bash
docker logs monitorr 2>&1 | grep -cF "queued download cancelled (episodeId="   # high & climbing
docker logs monitorr 2>&1 | grep -F "surplus=" | grep -vF "surplus=[]"   # DEBUG level; non-empty
                                                                         # surplus = guard firing
```

This is a documented accepted trade-off, and the subject of a dedicated, decision-gated campaign
with measurements, a ranked solution menu and fenced-off wrong paths. **Do not improvise a fix
here** — go to `monitorr-season-pack-campaign` (and `monitorr-change-control` for any change).
Immediate operational relief: clean the dead torrents in the download client, and/or prefer
single-episode releases via Sonarr release profiles for the affected show.

## R7: Grace deleted something it shouldn't / kept something it should delete

1. **Identify what fired from the deletion log** — the `reason` column distinguishes every
   deletion source (`engine/grace.py`, `engine/window.py`, `engine/actions.py`):
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT season, episode, reason, dry_run, created_at FROM deletion_log
      WHERE tvdb_id=<TVDB_ID> ORDER BY id DESC LIMIT 50;"
   ```
   Reasons: `completed`, `dormant`, `grace_watched`, `grace_unwatched` (grace sweep),
   `ahead`, `keep` (window trims), `normalize` (auto-normalize — see R8).
2. **Know the evaluation order** (`engine/grace.py::_sweep_series`): `completed` → `dormant`
   (both are BULK purges that intentionally IGNORE the KEEP floor but respect Always-Have; each
   returns immediately, skipping the later checks) → KEEP floor computed → `grace_watched` →
   `grace_unwatched`. Formulas and rationale: `monitorr-window-engine-reference`.
3. **"It deleted a season I was mid-way through" → usually `completed`.** Caught-up test:
   `max(watched_keys) >= max(aired_keys)` — lexicographic furthest-watch vs last-aired. It
   assumes LINEAR viewing: a skipped mid-run episode does NOT block the purge (accepted
   trade-off). `grace_completed_days=30` is ENABLED by default — the riskiest default once
   dry-run is off. If this bit you: raise/disable `grace_completed_days` (globally in Settings
   or per-series), and remember the purge still respected Always-Have.
4. **`dormant` deletions with default config are impossible** — `dormant_days` defaults to
   `None` (disabled). If you see `reason='dormant'`, someone set it; check the global policy JSON
   and the per-series override:
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT value FROM setting WHERE key='policy';"
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT policy_json FROM series_override WHERE tvdb_id=<TVDB_ID>;"
   ```
5. **KEEP floor holds only for the ongoing trims.** `grace_watched`/`grace_unwatched` never
   delete inside the KEEP window around the furthest watch; the anchor for that floor is clamped
   to episodes Sonarr actually lists (a Plex-numbered watch Sonarr doesn't know cannot dissolve
   the floor — v1.6.1 behavior). If a KEEP-window episode was deleted by `grace_watched` on an
   older version, upgrade (fix 9a189ef, see `monitorr-failure-archaeology`).
6. **Markers are kept on purpose:** `grace_watched` always keeps the most-recently-watched
   episode; `grace_unwatched` always keeps the FIRST unwatched episode — both as "where was I"
   markers. "It should have deleted everything" reports usually observed a marker.
7. **"It kept something it should delete":** check, in order — Always-Have match (grammar
   `S01E01` / `S*E01` / `S01` / `S*`, case-insensitive, invalid patterns silently ignored);
   inside KEEP floor; is the marker (point 6); grace days not yet elapsed vs
   `series_activity.last_watch_at`; show has NO activity row at all (never watched → grace sweep
   skips it entirely, R2.4 note); series disabled/overridden.
8. **Verify inactivity age yourself:**
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT last_watch_at, CAST(julianday('now') - julianday(last_watch_at) AS INT) AS age_days
      FROM series_activity WHERE tvdb_id=<TVDB_ID>;"
   ```
   Grace fires when `age_days > <grace>_days` (strict).

## R8: A show was reduced to just the pilot unexpectedly

Cause: **auto-normalize**. Every sync, every managed+enabled show with `auto_normalize` on
(default True) and ZERO rows in `episode_watch` is reduced to pilot-only — files beyond the
pilot and Always-Have are DELETED, everything else unmonitored (`sync.py::_reconcile_managed` →
`engine/actions.py::normalize_to_pilot`). This is AUTOMATIC; there is no manual button or route
(`.claude/architecture.md` was stale here until 2026-07-02 — fixed, it now says automatic; see
the retired errata table in `monitorr-docs-and-writing`).

When it bites: a show you watched BEFORE installing monitorr, whose plays never made it into the
watch store — Plex history unavailable/disabled, user filter excluding the watcher, or the show's
title failing history correlation. With no recorded watches, monitorr treats it as "never
watched" and normalizes it.

1. **Confirm the mechanism:**
   ```bash
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT COUNT(*) FROM episode_watch WHERE tvdb_id=<TVDB_ID>;"          # → 0 confirms
   sqlite3 "file:./config/monitorr.db?mode=ro" \
     "SELECT season, episode, dry_run, created_at FROM deletion_log
      WHERE tvdb_id=<TVDB_ID> AND reason='normalize' ORDER BY id DESC LIMIT 20;"
   docker logs monitorr 2>&1 | grep -F "normalize tvdb=<TVDB_ID>"   # DEBUG level only
   ```
2. **Find why the watches are missing:** grep
   `could not read Plex play history; back-catalog degraded` (history endpoint down/disabled) and
   `user filter names not found among Plex accounts` / `no user filter name matched a Plex
   account; sync stays unfiltered` (filter resolution, `sync.py`). Fix the cause, then force a
   full sync (`curl -s -X POST http://localhost:8080/sync`) and confirm `episode_watch` fills.
3. **Or opt the show (or everything) out:** untick `auto_normalize` in the per-series policy at
   `/series/{tvdb_id}` (or globally in `/settings`), or disable the series. Config semantics:
   `monitorr-config-and-flags`.
4. **Recover the deleted episodes:** once watches are recorded (or normalize is off), the next
   watch/sync re-arms the GET window from the real anchor and re-searches. Note: with dry-run ON
   nothing was actually deleted — the `deletion_log` rows will show `dry_run=1`.
5. Normalize behavior details (pilot searched if missing, Always-Have kept monitored, queued
   downloads of unmonitored episodes cancelled): `engine/actions.py::normalize_to_pilot`.

---

## When NOT to use

- Full incident histories, commit archaeology, "why is the code shaped like this" →
  `monitorr-failure-archaeology`.
- Canonical window/grace/policy formulas and their code anchors →
  `monitorr-window-engine-reference`; the invariants and what must never change →
  `monitorr-architecture-contract`.
- Working the season-pack grab/cancel problem beyond diagnosis →
  `monitorr-season-pack-campaign`.
- Env-var / policy / setting-key reference beyond what a runbook step needs →
  `monitorr-config-and-flags`.
- Plex/Sonarr endpoint and auth details → `monitorr-plex-sonarr-reference`.
- Prebuilt diagnostic scripts and safe-DB-access patterns → `monitorr-diagnostics-and-tooling`.
- Normal operation (first run, compose, log levels) → `monitorr-run-and-operate`.
- Making ANY code/behavior change a runbook concludes is needed → `monitorr-change-control`
  first (anchor/sync/window changes need adversarial review + a regression test).

## Provenance and maintenance

All facts verified against the working tree at v1.6.1 (2026-07-02). Re-verify before trusting:

| Fact | Re-verify with |
|---|---|
| Log strings quoted in R1–R8 | `grep -rn "re-anchored tvdb=\|apply_window tvdb=\|sync show tvdb=\|sync completed\|would delete S\|queued download cancelled\|monitorr started\|normalize tvdb=" src/monitorr/` |
| Log line format | `cat src/monitorr/logging.py` |
| Setting keys (`dry_run`, `history_watermark`, `last_full_sync`, …) | `cat src/monitorr/constants.py` (plus `grep -n "_LAST_SYNC" src/monitorr/sync.py` — `last_sync` is defined there, not in constants) |
| DB schema (tables `setting`, `episode_watch`, `series_activity`, `series_override`, `deletion_log`) | `cat src/monitorr/db.py` |
| Dry-run default True; disabling clears previews | `grep -n -A3 "def get_dry_run\|def set_dry_run" src/monitorr/engine/policy.py` |
| Policy defaults (grace 7/365/None/30, always_have S01E01, auto_normalize True) | `grep -n -A15 "class Policy" src/monitorr/engine/policy.py` |
| Deletion reasons (`ahead`, `keep`, `completed`, `dormant`, `grace_watched`, `grace_unwatched`, `normalize`) | `grep -rn "delete_episode(base_url" src/monitorr/engine/ src/monitorr/` |
| Grace evaluation order and markers | `cat src/monitorr/engine/grace.py` |
| Anchor floor + clamp, DEBUG window traces | `grep -n -B2 -A15 "Anchor floor\|logger.debug" src/monitorr/engine/window.py` |
| Sync full-vs-incremental rule, blind-sweep no-advance, promote-to-full | `grep -n -B2 -A6 "history_failed\|full = (" src/monitorr/sync.py` |
| Auto-normalize condition (no recorded watches) | `grep -n -B2 -A6 "auto_normalize" src/monitorr/sync.py` |
| Webhook returns 200 `{"status":"forbidden"}` on bad secret; all body statuses | `grep -n -A8 "def plex_webhook" src/monitorr/web/routes.py` |
| Webhook secret precedence (env pins over DB) | `grep -n -A10 "def get_or_create_webhook_secret" src/monitorr/plex/webhook.py` |
| Routes (`/sync`, `/deletions`, `/series/{tvdb_id}`, `/webhook/plex/{secret}`, `/health`, `/version`) | `grep -n "@router\|@app.get" src/monitorr/web/routes.py src/monitorr/main.py` |
| Env vars and defaults (intervals, `MONITORR_CONFIG_DIR`, log level) | `cat src/monitorr/config.py` |
| Entrypoint PUID/PGID behavior + startup echo | `cat docker-entrypoint.sh` |
| Image lacks sqlite3 CLI (use python in-container) | `grep -n "apt-get install" Dockerfile` |
| Container name / bind mount in examples | `cat docker-compose.yml` |
| Sonarr API paths (`/api/v3/series`, `/episode?seriesId=`, `/queue`) | `grep -n "api/v3\|client.get(" src/monitorr/sonarr/client.py` |
| Watermark/full-sync reset on server switch or unlink | `grep -n -B2 -A8 "_store_server\|plex_unlink" src/monitorr/web/routes.py` |
| Current version | `grep -n "^version" pyproject.toml` |
