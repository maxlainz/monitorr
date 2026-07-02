---
name: monitorr-config-and-flags
description: >-
  Owner of monitorr's complete configuration surface: every MONITORR_* env var (defaults, ge
  validation, restart-required lru_cache), all 11 Policy fields, dry-run / watched-threshold /
  user-filter, every SQLite setting-table key (writer/reader/reset rules), per-series overrides,
  web-form clamping (_opt_int, threshold clamp), PUID/PGID/TZ. Use when adding, changing, or
  looking up any setting, default, flag, or "where is X configured / why didn't my env var take
  effect". NOT for what the window/grace policy DOES -> monitorr-window-engine-reference; NOT for
  running/deploying -> monitorr-run-and-operate.
---

# monitorr configuration and flags

Canonical inventory of everything configurable in monitorr, verified against the code at v1.6.1
(2026-07-02). Code is ground truth; the README and `.env.example` are stale in a few spots noted
below (errata live in `monitorr-docs-and-writing`).

Jargon used here, defined once: **dry-run** = master switch (ON by default) that turns every
Sonarr write into a logged no-op; **GET / KEEP** = the counts of episodes kept ahead of / behind
the viewing point; **Always-Have** = patterns of episodes never deleted/unmonitored (e.g. the
pilot); **grace** = a per-category delay (days) before a deletion becomes eligible; **watermark**
= newest Plex play timestamp already scanned, floor for the next incremental history sweep;
**full vs incremental sync** = reconcile every managed show vs only shows with new plays;
**PMS** = Plex Media Server.

## 1. The three configuration layers

| Layer | Storage | Edited via | Takes effect |
|---|---|---|---|
| Infrastructure | `MONITORR_*` env vars (+ optional `.env` file) | Container env / `.env` | **Process restart required** — `get_settings()` is `@lru_cache` (`src/monitorr/config.py:43`) |
| App config | SQLite `setting` table (key/value) in `$MONITORR_CONFIG_DIR/monitorr.db` | Web UI (`/settings`) | Immediately (read per use, not cached) |
| Per-series | SQLite `series_override` table | Web UI (`/series/{tvdb_id}`) | Immediately |

Layer boundaries (from `src/monitorr/config.py:8-13`): env vars hold ONLY infrastructure (paths,
ports, loop intervals, log level, build metadata, one optional secret pin). Everything about
Sonarr connection, window policy, grace, dry-run, threshold, user filter, webhook secret and sync
state lives in SQLite and is edited from the Web UI — never via env.

Env loading details (`config.py:15`): prefix `MONITORR_`, optional `.env` file in the working
directory, unknown vars ignored (`extra="ignore"`). Values come from pydantic-settings, so bools
accept `true/false/1/0` and validation errors abort startup (fail fast, by design — see the
comment at `config.py:21-23`).

## 2. Env vars — full table

Source of truth: `src/monitorr/config.py` (class `Settings`). "README?" = present in the README
env table (`README.md` around lines 118-126) — the code column wins where they disagree.

| Var | Default | Validation | Effect | README? |
|---|---|---|---|---|
| `MONITORR_CONFIG_DIR` | `/config` | Path | Data dir; DB is `<dir>/monitorr.db` (`db_path` property). Docker image pins it to `/config` via `ENV` in the Dockerfile | yes |
| `MONITORR_HOST` | `0.0.0.0` | str | Uvicorn bind address (`main.py` `run()`) | **no — undocumented** |
| `MONITORR_PORT` | `8080` | int | Uvicorn listen port | yes |
| `MONITORR_LOG_LEVEL` | `INFO` | str | Root logging level (`logging.py`), `DEBUG/INFO/WARNING/ERROR` | yes |
| `MONITORR_PLEX_POLL_INTERVAL` | `30` | `ge=1` | Seconds between Plex session polls (live viewing detection) | yes |
| `MONITORR_GRACE_SWEEP_INTERVAL` | `3600` | `ge=1` | Seconds between grace-period sweeps (deferred deletions) | yes |
| `MONITORR_SYNC_INTERVAL` | `21600` | `ge=0` | Seconds between periodic syncs; `0` = the periodic sync loop is never created (`main.py:54`) | yes |
| `MONITORR_FULL_SYNC_INTERVAL` | `2592000` (30 d) | `ge=0` | Rolling floor: seconds since the last FULL sync after which the next sync is promoted to full; `0` disables the floor (full only on manual "Sync now", dep connection, or a blind history sweep) | **no — undocumented** |
| `MONITORR_SYNC_ON_STARTUP` | `true` | bool | Run a sync at startup **only when a FULL is overdue** — never ran, or the rolling floor elapsed while the app was down (`main.py:60` + `sync.full_sync_due()`). The README/`.env.example` wording "if it never ran" is stale (pre-1.5.0) | yes (stale wording) |
| `MONITORR_WEBHOOK_SECRET` | `""` (empty) | str | When non-empty, pins the Plex webhook secret; overrides the SQLite one and disables the UI "Regenerate" button (see footguns) | yes |
| `MONITORR_BUILD_SHA` | `""` | str | Build metadata shown by `GET /version`; injected by the Dockerfile `ENV` from the `VCS_REF` build arg (empty in dev) | no (build-internal) |
| `MONITORR_BUILD_DATE` | `""` | str | Same, from the `BUILD_DATE` build arg | no (build-internal) |
| `TZ` | `UTC` | (OS) | Standard container time-zone var. All internal timestamps and grace/age math are UTC (`datetime.now(UTC)` everywhere — grep confirms no naive `now()` in `src/`); the practical effect is local-time rendering (e.g. log `asctime`) | yes |
| `PUID` / `PGID` | `1000` / `1000` | (shell) | **Container-only, read by `docker-entrypoint.sh`, NOT by the app**: entrypoint (running as root) remaps the `app` user to this UID/GID, `chown -R`s `/config`, then drops privileges via gosu. Ignored when the container already runs non-root (compose `user:`) | yes |

The `ge=1` intervals exist so a zero/negative value fails at startup instead of degenerating into
a hot loop hammering Plex/Sonarr; `sync_interval`/`full_sync_interval` accept `0` as their
documented "disabled" value (`config.py:21-30`).

## 3. Policy fields and global-only app settings

`Policy` (`src/monitorr/engine/policy.py:24-37`) is the part of the configuration overridable per
series. All 11 fields:

| Field | Type | Default | Constraint |
|---|---|---|---|
| `get_count` | `int` | `1` | `ge=0` (pydantic `Field`) |
| `get_unit` | `Literal["episodes","seasons"]` | `"episodes"` | literal |
| `keep_count` | `int` | `1` | `ge=0` |
| `keep_unit` | `Literal["episodes","seasons"]` | `"episodes"` | literal |
| `always_have` | `list[str]` | `["S01E01"]` | patterns `^S(\d+|\*)(?:E(\d+|\*))?$` case-insensitive → `S01E01`, `S*E01`, `S01`, `S*`; invalid patterns silently ignored |
| `grace_watched_days` | `int \| None` | `7` | `None` = disabled; **no ge constraint on the model** (form layer is the guard, see §5) |
| `grace_unwatched_days` | `int \| None` | `365` | same |
| `dormant_days` | `int \| None` | `None` (disabled) | same |
| `grace_completed_days` | `int \| None` | `30` (**enabled** — the riskiest default once dry-run is OFF) | same |
| `search_on_get` | `bool` | `True` | checkbox |
| `auto_normalize` | `bool` | `True` | checkbox |

What these fields DO (window formulas, grace evaluation order, `_is_armed`) is owned by
`monitorr-window-engine-reference` — do not duplicate the formulas here.

Global-only app settings (cannot be overridden per series; edited in the same `/settings` form):

| Setting | Default | Storage / behavior |
|---|---|---|
| Dry-run | **ON** | Key `dry_run`, stored `"1"`/`"0"` by `set_dry_run`. Read: ON iff the key is missing **or exactly `"1"`** (`policy.py:64-66`). Turning it OFF clears all pending deletion previews (`set_dry_run` → `store.clear_pending_deletions()`) |
| Watched threshold | `0.9` | Key `watched_threshold`. Clamped to `[0.05, 1.0]` **on save** (`web/routes.py:306-308`); read side (`get_watched_threshold`) does a plain `float(raw)` with no clamp |
| User filter | `[]` (no filtering) | Key `plex_user_filter`, JSON list of Plex account names; form input split on commas/whitespace. Empty list = process everyone |

## 4. `setting` table — key inventory

Schema: `setting(key TEXT PRIMARY KEY, value TEXT NOT NULL)` (`src/monitorr/db.py`, migration 1).
Constants for most keys live in `src/monitorr/constants.py`; two modules define private extras.

| Key | Defined in | Written by | Read by | Meaning | Reset when |
|---|---|---|---|---|---|
| `plex_client_id` | constants | `routes.plex_link` (generated once) | all Plex calls (`X-Plex-Client-Identifier`) | Stable client identity toward plex.tv | never — survives unlink (identity, not credential) |
| `plex_account_token` | constants | PIN poll success (`plex_link_poll`) | webhook (de)registration, server discovery | plex.tv account token | deleted on **Unlink** |
| `plex_server_uri` | constants | `_store_server` | all PMS calls | Chosen connection URI | deleted on Unlink; rewritten on server change |
| `plex_server_token` | constants | `_store_server` | all PMS calls | Per-server access token | deleted on Unlink |
| `plex_server_name` | constants | `_store_server` | UI status | Display name | deleted on Unlink |
| `plex_server_id` | constants | `_store_server` | `_store_server` (change detection) | PMS `clientIdentifier` — the stable server identity | deleted on Unlink; comparing it on link is what triggers the per-server reset below |
| `sonarr_url` / `sonarr_api_key` | constants | `save_sonarr` | `sonarr.get_config()` | Sonarr connection | overwritten on save; never auto-deleted |
| `policy` | constants | `set_global_policy` (JSON of `Policy`) | `get_global_policy` | Global window policy | overwritten on save |
| `dry_run` | constants | `set_dry_run` (`"1"`/`"0"`) | `get_dry_run` | Master write switch | overwritten on save |
| `watched_threshold` | constants | `save_policy` (clamped) | `get_watched_threshold` (poller) | Live watched trigger | overwritten on save |
| `plex_user_filter` | constants (`USER_FILTER`) | `save_policy` (JSON list) | poller, webhook, sync | Only these Plex users count | overwritten on save |
| `webhook_secret` | constants | lazily generated on first read (`get_or_create_webhook_secret`); `regenerate_webhook_secret` | webhook route auth | Secret in the webhook URL | regenerated via UI; **ignored** while `MONITORR_WEBHOOK_SECRET` is set |
| `history_watermark` | constants | `sync._persist_watermark` | `sync._run` (full-vs-incremental, `since`) | Newest Plex play scanned | deleted on Unlink AND on linking a **different** PMS (`_store_server`, `routes.py:79-82`) |
| `last_full_sync` | constants | `sync._persist_watermark` (full only) | rolling-floor check, startup check | Timestamp of last FULL sync | same resets as watermark; also deleted once by DB migration 4 (`db.py:58`) |
| `last_sync` | `sync.py:25` (`_LAST_SYNC`, private) | `sync._run` | dashboard (`/`) | JSON summary `{at, mode, shows, matched, normalized, searched}` | display only; never reset |
| `plex_pin_id` / `plex_pin_code` | `routes.py:52-53` (`_PIN_ID`/`_PIN_CODE`, private) | `plex_link` | `plex_link_poll` | Transient PIN-linking state | overwritten on each link attempt; NOT deleted on unlink (stale but harmless) |

Rationale for the per-server reset (comment at `routes.py:76-78`): against a different PMS a
carried-over (possibly future) watermark would make incremental sweeps skip its plays forever.

## 5. Web-form input hardening (verified in `src/monitorr/web/routes.py`)

Both the global form (`POST /settings/policy`) and the per-series form
(`POST /series/{tvdb_id}/policy`) build the `Policy` through `_policy_from_form`
(`routes.py:229-269`):

- Grace/dormant day fields go through `_opt_int` (`routes.py:244-255`): empty, non-numeric, or
  **negative** → `None` (that grace **disabled**), never an HTTP 500. `0` stays valid
  ("immediately"). A negative number would make the grace fire unconditionally — deletions from a
  typo — so disabling is the safe reading.
- `get_count` / `keep_count`: `max(0, value)` — negatives floored to 0.
- Units: anything other than the exact string `"seasons"` becomes `"episodes"`.
- `always_have`: input split on commas AND whitespace (`_split`); an empty result is **forced to
  `["S01E01"]`** — you cannot save an empty Always-Have list from the UI.
- Checkboxes (`search_on_get`, `auto_normalize`, and on the global form `dry_run`): HTML absence
  → `None` → `False`. The global form ALWAYS rewrites dry-run from checkbox presence.
- `watched_threshold` (global form only): clamped to `[0.05, 1.0]` on save (`routes.py:306-308`)
  — `>1` would make the live trigger unreachable, near-0 hair-triggered.
- `user_filter` (global form only): split like `always_have`, stored as a JSON list.

## 6. Per-series overrides (`series_override` table)

Schema: `series_override(tvdb_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
policy_json TEXT)` (`db.py`, migration 2). Accessors in `src/monitorr/store.py:48-89`.

- **Complete snapshot**: `save_series_policy` (`routes.py:441-472`) stores
  `policy.model_dump_json()` — ALL 11 fields, not a diff. Later changes to the global policy do
  NOT flow into a series with a custom policy.
- **Merge**: `effective_policy(tvdb_id)` (`policy.py:51-61`) returns `(policy, enabled)`. With no
  override row → `(global, True)`. With `policy_json` → `{**global_dump, **partial}` re-validated
  as `Policy` — the merge tolerates a partial JSON (hand-written DB rows), but the UI always
  writes full snapshots.
- **Reset to global** (`POST /series/{tvdb_id}/policy/reset`): sets `policy_json = None`,
  **keeping the `enabled` flag** — the series follows the global policy again.
- **Enable/disable** (`POST /series/{tvdb_id}/override`): flips `enabled` only, **preserving any
  `policy_json`**. `enabled=False` makes the sync/reconcile skip the series entirely.
- **Global-only, never per-series**: dry-run, watched threshold, user filter, webhook secret, and
  every env var. The per-series form has no fields for them.

## 7. Footguns

1. **Env edits need a restart**: `get_settings()` is `@lru_cache` (`config.py:43-45`). Changing a
   `MONITORR_*` var (or `.env`) does nothing to a running process.
2. **Dry-run value semantics**: `get_dry_run` returns ON iff the key is missing or the stored
   value is exactly `"1"` (`policy.py:64-66`). `set_dry_run` only ever writes `"0"`/`"1"`, but a
   hand-edited or corrupted value like `"true"` reads as dry-run **OFF** — i.e. real deletions.
   Never poke this key by hand; use the UI.
3. **`MONITORR_SYNC_INTERVAL=0`** disables the periodic sync loop entirely (`main.py:54` — the
   task is simply not created). Syncs then only happen at startup (floor check), on manual "Sync
   now", or when connecting Plex+Sonarr.
4. **`MONITORR_FULL_SYNC_INTERVAL=0`** disables the rolling full-sync safety floor
   (`sync._floor_elapsed`, `sync.py:43-52`): after the first full, nothing periodic ever promotes
   to full again (only manual/dep-connection/blind-sweep causes), and `sync_on_startup` stops
   firing too (it fires only when a full is overdue).
5. **`MONITORR_WEBHOOK_SECRET` set** → the secret is pinned from env (not persisted), the SQLite
   secret is ignored, and the UI "Regenerate" button becomes a no-op (`routes.py:503-504`;
   `webhook_pinned_by_env` in the settings context).
6. **Threshold clamped on save only**: `get_watched_threshold` reads `float(raw)` unclamped — a
   manually written out-of-range DB value takes effect as-is.
7. **Policy model has no bounds on grace days**: `_opt_int` in the form layer is the only guard
   against negatives; a direct-DB or hand-written `policy_json` with negative grace days
   validates fine and would make that grace fire unconditionally.
8. **Interval validation is fail-fast**: `MONITORR_PLEX_POLL_INTERVAL=0` (or any `ge` violation)
   crashes the app at startup with a pydantic `ValidationError`. Intentional — see
   `config.py:21-23`.
9. **Saving the global settings form rewrites everything on it**: dry-run comes from checkbox
   presence, so a form POST without the checkbox turns dry-run OFF/ON as shown — scripted POSTs
   to `/settings/policy` must include every field they mean to keep.

Any change to defaults, validation, or key semantics is behavior-changing: follow
`monitorr-change-control` (dev branch, docs in the same commit, regression test).

## When NOT to use

- What a policy field DOES at runtime (window formulas, grace order, `_is_armed`, Always-Have
  matching) → `monitorr-window-engine-reference`.
- Running the app, Docker/compose usage, PUID/PGID troubleshooting in operation, first-run
  sequence, log levels in practice → `monitorr-run-and-operate`.
- Build args, CI, lockfile, toolchain → `monitorr-build-and-env`.
- Symptom-driven debugging ("my setting seems ignored" beyond the footguns above) →
  `monitorr-debugging-playbook`.
- Recording a docs/README mismatch → `monitorr-docs-and-writing` (sole home of the errata table).

## Provenance and maintenance

Re-verify each table with one command from the repo root:

| Fact | Command |
|---|---|
| Env var list, defaults, `ge` constraints | `grep -n "Field\|: " src/monitorr/config.py` |
| `get_settings` still lru_cached | `grep -n "lru_cache" src/monitorr/config.py` |
| Policy fields and defaults | `sed -n '24,40p' src/monitorr/engine/policy.py` |
| Dry-run read/write semantics | `grep -n -A2 "def get_dry_run\|def set_dry_run" src/monitorr/engine/policy.py` |
| Threshold clamp + user-filter save | `grep -n "WATCHED_THRESHOLD\|USER_FILTER" src/monitorr/web/routes.py` |
| Setting-table constants | `cat src/monitorr/constants.py` |
| Extra keys outside constants.py | `grep -rn '_LAST_SYNC\|_PIN_ID\|_PIN_CODE' src/monitorr/` |
| Per-server reset + unlink key list | `grep -n "delete_setting" src/monitorr/web/routes.py` |
| Form hardening (`_opt_int`, `_split`, forced pilot) | `grep -n -A12 "_opt_int\|def _split" src/monitorr/web/routes.py` |
| Override snapshot/merge/reset | `grep -n "policy_json\|model_dump" src/monitorr/engine/policy.py src/monitorr/web/routes.py` |
| Entrypoint PUID/PGID behavior | `cat docker-entrypoint.sh` |
| Docker-injected build vars | `grep -n "MONITORR_" Dockerfile` |
| README env table (drift check) | `grep -n "MONITORR_" README.md .env.example` |
| Migration that resets `last_full_sync` | `grep -n "last_full_sync" src/monitorr/db.py` |
| Current version (date-stamp for this file) | `grep -n "^version" pyproject.toml` |
