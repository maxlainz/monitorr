---
name: monitorr-run-and-operate
description: >-
  Run and operate monitorr: local dev run (uv run monitorr, MONITORR_CONFIG_DIR), docker run /
  docker compose, the /config volume, PUID/PGID permission remap, the first-run setup sequence
  (link Plex, choose server, Sonarr, policy, webhook, disabling dry-run), the background loops and
  what each interval env var controls, /health, /version, dashboard, deletions page, "Sync now",
  log levels, TZ, backups before upgrades. NOT for building images/CI/toolchain → use
  monitorr-build-and-env; something is broken → monitorr-debugging-playbook; the full env-var and
  Policy reference → monitorr-config-and-flags.
---

# monitorr — run and operate

How to start monitorr (dev box and Docker), take a fresh install to a working state, and read its
operational surfaces. monitorr is a FastAPI app serving a Web UI on port 8080; all durable state
lives in one SQLite file. Verified against v1.6.1 source on 2026-07-02.

Terms used below, defined once: **PMS** = Plex Media Server. **GET** = the window of episodes
AHEAD of the viewing point that monitorr keeps monitored/downloaded; **KEEP** = the window BEHIND
it that is retained (the rest is deleted via Sonarr). **Always-Have** = patterns (default
`S01E01`) protecting key episodes from deletion. **Grace** = deferred-deletion periods swept
periodically. **Dry-run** = master switch (ON by default) that turns every Sonarr write into a
no-op preview. **Full sync** = reconcile all shows from the complete Plex history; **incremental
sync** = only plays newer than the stored **watermark** (newest play timestamp already scanned).

## When NOT to use

| You want | Go to |
|---|---|
| Build the Docker image, CI jobs, uv/ruff/mypy/pytest toolchain | `monitorr-build-and-env` |
| Diagnose a malfunction (no deletions, re-downloads, sync stuck, webhook silent, crashes) | `monitorr-debugging-playbook` |
| The complete env-var / Policy / setting-key reference with validation rules | `monitorr-config-and-flags` |
| Change behavior, release, merge to main | `monitorr-change-control` |
| Inspect the DB / run diagnostics | `monitorr-diagnostics-and-tooling` |

## 1. Local dev run

```bash
uv sync                                      # create/update .venv from uv.lock
MONITORR_CONFIG_DIR=./config uv run monitorr # Web UI at http://localhost:8080
```

- `monitorr` is a console script: `pyproject.toml` `[project.scripts]` maps `monitorr` to
  `monitorr.main:run`, which calls `uvicorn.run("monitorr.main:app", host, port)`.
- `MONITORR_CONFIG_DIR=./config` is REQUIRED on a dev box: the default is `/config`
  (`src/monitorr/config.py`), and startup runs `init_db`, which does
  `path.parent.mkdir(parents=True, exist_ok=True)` on `<config_dir>/monitorr.db` — creating
  `/config` on a normal machine fails with `PermissionError`. Any writable directory works.
- Settings also load from a `.env` file in the working directory
  (`SettingsConfigDict(env_prefix="MONITORR_", env_file=".env")`), so you can put
  `MONITORR_CONFIG_DIR=./config` in `.env` once. Template: `.env.example`.
- `MONITORR_HOST` (default `0.0.0.0`) and `MONITORR_PORT` (default `8080`) control the bind.
- Expect on stdout: `monitorr started (db=config/monitorr.db)`. If you get
  `unable to open database file` or `PermissionError`, the env var is missing or the dir is not
  writable.

## 2. Docker run and compose

Images: Docker Hub `maxlainz/monitorr` and GHCR `ghcr.io/maxlainz/monitorr`, multi-arch
(amd64/arm64), tags `:X.Y.Z`, `:X.Y`, `:X`, `:latest`.

```bash
docker run -d \
  --name monitorr \
  -p 8080:8080 \
  -v "$(pwd)/config:/config" \
  -e TZ=Europe/Madrid \
  --restart unless-stopped \
  maxlainz/monitorr:latest
```

Compose (the repo's `docker-compose.yml` is the reference):

```yaml
services:
  monitorr:
    image: maxlainz/monitorr:latest   # or ghcr.io/maxlainz/monitorr:latest, or build: .
    container_name: monitorr
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
    environment:
      - TZ=Europe/Madrid
      # - PUID=1000
      # - PGID=1000
    restart: unless-stopped
```

```bash
docker compose up -d
```

Runtime facts baked into the image (`Dockerfile`): `MONITORR_CONFIG_DIR=/config`, `EXPOSE 8080`,
`VOLUME /config`, and a `HEALTHCHECK` that polls `http://localhost:8080/health` every 30 s
(5 s timeout, 10 s start period, 3 retries) — `docker ps` shows `healthy`/`unhealthy`.

### The /config volume

`/config` is the ONLY writable state. It holds `monitorr.db` (SQLite, WAL mode — so also the
`monitorr.db-wal` / `monitorr.db-shm` sidecars). EVERYTHING durable lives inside that DB: the
Plex client identity (`plex_client_id`) and account/server tokens, the Sonarr URL/API key, the
policy, dry-run flag, webhook secret, the watch history store, and the deletion log. Losing
`/config` means relinking Plex and reconfiguring from scratch; nothing else in the container is
worth preserving.

### PUID/PGID (entrypoint remap)

The container starts as root; `docker-entrypoint.sh` then:

1. Reads `PUID`/`PGID`, defaulting both to `1000` (the common desktop/NAS user).
2. If already running as non-root (e.g. compose `user:` override), it SKIPS the remap entirely
   and `exec`s the app as that user — your UID then owns the permission problem.
3. Otherwise remaps the baked-in `app` user in place: `groupmod -o -g "$PGID" app` and
   `usermod -o -u "$PUID" app` (`-o` allows reusing an id that already exists in the image).
4. `chown -R app:app /config` so the bind mount is writable whatever the host ownership was.
5. Drops privileges with `exec gosu app "$@"` and logs
   `monitorr: starting as app (uid=... gid=...)`.

Set `PUID`/`PGID` to the host user's `id -u` / `id -g` so files created in `./config` stay owned
by you. These two are container-only: the entrypoint reads them, the app does not. This design
exists because a fixed non-root image UID crashed at startup on host-owned bind mounts
(`unable to open database file`) — fixed in ee94c28 → v1.1.0; story in
`monitorr-failure-archaeology`.

## 3. First-run sequence (checklist)

Open `http://<host>:8080`. Dry-run is ON by default, so nothing below touches Sonarr until the
final step — safe to do in any order, but this order avoids surprises:

1. **Link Plex** — Settings → Login with Plex. This is a PIN/OAuth flow: monitorr creates a PIN,
   opens `plex.tv` in a popup for you to approve, and the page polls until the token arrives.
2. **Choose the Plex server.** With exactly ONE server on the account it is auto-selected —
   and that auto-select path also registers the webhook and triggers the first FULL sync (it did
   not before 76b97c3 → v1.6.1; see `monitorr-failure-archaeology`). With several servers, pick
   one from the list. Note: the first full sync fires only once BOTH Plex and Sonarr are
   configured (`_maybe_trigger_full_sync` in `src/monitorr/web/routes.py`), so whichever of the
   two you connect second triggers it.
3. **Configure Sonarr** — Settings → Sonarr URL + API key. Use the **Test** button first: it
   calls Sonarr's system/status and shows the detected version; save only after it succeeds.
4. **Set the policy** — GET/KEEP counts and unit (episodes/seasons), Always-Have patterns,
   grace periods, watched threshold, optional Plex user filter. Field semantics:
   `monitorr-config-and-flags`.
5. **Optional: Plex webhook** (Plex Pass accounts only; the always-on poller makes it strictly
   optional). monitorr auto-registers it on link, best-effort; the Settings page shows the
   registration status read back from Plex. The URL field is EDITABLE on purpose: it reflects how
   YOUR BROWSER reached the page, but the URL must be reachable FROM the PMS — edit it (LAN IP,
   reverse-proxy host) before pressing "Register / Update in Plex" if those differ. "Regenerate"
   rotates the secret (disabled when pinned by `MONITORR_WEBHOOK_SECRET`).
6. **LAST: disable dry-run** — the checkbox lives in the policy form on Settings. Before
   flipping it, review `/deletions`: the pending list is exactly what monitorr wants to delete.
   Disabling dry-run CLEARS all pending previews (`set_dry_run` in
   `src/monitorr/engine/policy.py` calls `store.clear_pending_deletions()`); from then on writes
   are real.

## 4. Background machinery at runtime

`lifespan` in `src/monitorr/main.py` starts, after `init_db` (migrations):

| Task | Runs when | Interval env var (default) |
|---|---|---|
| Plex session poller (`poll_loop`) | ALWAYS | `MONITORR_PLEX_POLL_INTERVAL` (30 s, min 1) |
| Grace sweep loop (`_grace_loop`) | ALWAYS | `MONITORR_GRACE_SWEEP_INTERVAL` (3600 s, min 1) |
| Periodic sync loop (`_sync_loop`) | only if `sync_interval > 0` | `MONITORR_SYNC_INTERVAL` (21600 s; 0 disables) |
| One-shot startup sync | only if `sync_on_startup` AND `sync.full_sync_due()` | `MONITORR_SYNC_ON_STARTUP` (true) |

What each interval means (full reference: `monitorr-config-and-flags`):

- `MONITORR_PLEX_POLL_INTERVAL` — seconds the poller sleeps between reads of the PMS active
  sessions; this is the live watched-episode detector. Lower = faster reaction, more Plex load.
- `MONITORR_GRACE_SWEEP_INTERVAL` — seconds between grace-period sweeps, which apply expired
  deferred deletions. Deletions therefore land up to one interval after their deadline.
- `MONITORR_SYNC_INTERVAL` — seconds between periodic watch-history syncs (normally incremental);
  `0` disables the loop entirely. Manual "Sync now" still works with it disabled.
- `MONITORR_FULL_SYNC_INTERVAL` — rolling floor in seconds since the last FULL sync that promotes
  the next sync to full (default 2592000 = 30 days; `0` disables the floor).
- `MONITORR_SYNC_ON_STARTUP` — at boot, run one sync only when a FULL is overdue (never ran, or
  the rolling floor elapsed while the app was down). A normal restart with a fresh full sync on
  record starts NOTHING extra. (README's older wording "if it never ran" is stale — see
  `monitorr-docs-and-writing`.)

Interval validators fail fast at startup (`ge=1` / `ge=0` in `config.py`) because a zero/negative
sleep would hot-loop against Plex/Sonarr. Loop exceptions are logged and the loop continues; the
tasks are cancelled cleanly on shutdown.

## 5. Operational surfaces

| Surface | What it tells you |
|---|---|
| `GET /health` | `{"status":"ok"}` — liveness only; used by the Docker HEALTHCHECK. |
| `GET /version` | `{"version", "build_sha", "build_date"}` — build metadata injected at image build (empty strings in dev/local builds). |
| Dashboard `/` | Plex/Sonarr status, dry-run banner with pending-preview count, last sync line: timestamp, mode (`full`/`incremental`), shows scanned/matched, normalized-to-pilot and re-searched counts. |
| **Sync now** button (`POST /sync`) | Forces a FULL sync (the deterministic "reconcile everything now" affordance); no-op and disabled while a sync is already running. |
| `/deletions` | Two lists: **pending** = dry-run previews (deduped, one row per episode) and **done** = real deletions executed through Sonarr. |
| `/series` and `/series/{tvdb_id}` | Per-show enable/disable and per-series policy overrides. |
| Logs (stdout) | `MONITORR_LOG_LEVEL` = `DEBUG`/`INFO`/`WARNING`/`ERROR`; `docker logs monitorr`. Every sync logs `sync completed (full|incremental): {...}`. |

## 6. Security posture

- monitorr has NO built-in authentication. It assumes a TRUSTED LAN. Do not expose port 8080 to
  the internet directly.
- Remote access → put it behind a reverse proxy WITH auth (Authelia, Authentik, basic-auth).
  If the proxy changes the public hostname, remember to edit the webhook URL field accordingly
  (section 3, step 5).
- The only endpoint designed to be exposed is the Plex webhook, `POST /webhook/plex/{secret}`.
  Its sole protection is the secret embedded in the URL path (compared with
  `secrets.compare_digest`); the secret is auto-generated and stored in the DB on first use, or
  pinned via `MONITORR_WEBHOOK_SECRET` (env wins, UI rotation disabled). Treat the URL itself as
  a credential.

## 7. Upgrades, downgrades, backups

- Schema migrations run automatically at startup (`init_db` in `src/monitorr/db.py`): an ordered,
  APPEND-ONLY list keyed to `PRAGMA user_version`; only pending ones are applied. Published
  migrations are never edited (rule in the file header; changing that goes through
  `monitorr-change-control`).
- Migration 4 (shipped with the v1.5.1 anchor-floor fix) deletes the `last_full_sync` setting,
  deliberately forcing ONE full sync on the first start after upgrading. Expect a full sync after
  such upgrades — that is intended, not a bug.
- Before any major upgrade, back up with the container STOPPED: copy `/config/monitorr.db` AND
  its WAL sidecars `monitorr.db-wal` / `monitorr.db-shm` if present (WAL mode means recent writes
  may live in the sidecar). Rollback = restore all three files and start the older image.
- Downgrading the image does not un-apply migrations; `user_version` simply stays ahead. Newer
  schema objects (e.g. the pending-deletions unique index) remain — restore the backup instead of
  trusting a downgrade against an upgraded DB.

## 8. Time zone and clocks

- `TZ` (compose example `Europe/Madrid`) sets the container's local time zone. Its practical
  effect is LOG TIMESTAMP display (Python logging's `%(asctime)s` uses local time).
- Grace-period day math itself is TZ-independent in code: `age_days` in
  `src/monitorr/engine/policy.py` computes elapsed seconds against `datetime.now(UTC)` over
  UTC-stored timestamps, i.e. continuous fractional days, not calendar days. (README and
  `.env.example` say TZ "affects grace periods" — that is stale relative to the code; see
  `monitorr-docs-and-writing`.)
- The rolling full-sync floor and the history watermark are likewise UTC-based; host clock skew,
  not TZ, is what would distort them.

## Provenance and maintenance

Verified on 2026-07-02 at v1.6.1 (commit c580f65). Re-verification one-liners:

| Fact | Re-verify with |
|---|---|
| Entry point `monitorr = monitorr.main:run` | `grep -A2 'project.scripts' pyproject.toml` |
| Default config dir `/config`, host/port defaults | `grep -n 'config_dir\|host\|port' src/monitorr/config.py` |
| Interval defaults + validators | `grep -n 'Field(default' src/monitorr/config.py` |
| Loop start conditions (poller/grace always, sync if >0, startup one-shot) | `grep -n 'create_task\|sync_on_startup' src/monitorr/main.py` |
| `/health` and `/version` payloads | `grep -n -A6 '@app.get' src/monitorr/main.py` |
| Entrypoint remap (PUID/PGID, -o, chown, gosu, non-root skip) | `cat docker-entrypoint.sh` |
| Image runtime bits (ENV, VOLUME, HEALTHCHECK, ENTRYPOINT/CMD) | `grep -n 'ENV\|VOLUME\|HEALTHCHECK\|ENTRYPOINT\|CMD\|EXPOSE' Dockerfile` |
| Compose reference | `cat docker-compose.yml` |
| Single-server auto-select triggers first full sync | `grep -n -B2 -A4 'len(servers) == 1' src/monitorr/web/routes.py` |
| "Sync now" forces full | `grep -n -A5 'def trigger_sync' src/monitorr/web/routes.py` |
| Disabling dry-run clears previews | `grep -n -A5 'def set_dry_run' src/monitorr/engine/policy.py` |
| Webhook secret env-pin + digest compare | `grep -n 'webhook_secret\|compare_digest' src/monitorr/plex/webhook.py src/monitorr/web/routes.py` |
| Migrations append-only + migration 4 | `sed -n '1,60p' src/monitorr/db.py` |
| WAL mode (backup must include sidecars) | `grep -n 'journal_mode' src/monitorr/db.py` |
| Grace math is UTC-based (TZ claim) | `grep -n -A7 'def age_days' src/monitorr/engine/policy.py` |
| Current version | `grep -n '^version' pyproject.toml` |
