# Architecture

> Fixed stack (see [`tech-stack.md`](tech-stack.md)). A runnable skeleton exists; the
> Plex/Sonarr/window logic is in place as a contract (signatures + TODO). Keep it up to date per
> [`documentation.md`](documentation.md).

## Purpose

monitorr watches which shows are being viewed in Plex and, via the Sonarr API, keeps
*N* episodes **ahead** of the viewing point monitored/downloaded and keeps only *N*
**behind** (deleting the rest from disk through Sonarr), protecting key episodes (e.g. the
pilot). Everything **API-only**, with no access to the media disk.

## Stack

Python 3.12 · FastAPI + Uvicorn · HTMX + Jinja2 (server-rendered) · SQLite (aiosqlite) ·
Docker single-image multi-arch (amd64/arm64). A single ASGI process serves API + Web UI on
`:8080` and starts the pollers as `asyncio` tasks in the `lifespan`. Versions, layout and the
*why* of each choice in [`tech-stack.md`](tech-stack.md).

## Components

Organized by domain (not by layer):

- **Linking/auth (Plex)** — "Login with Plex" (PIN/OAuth flow), server discovery and
  persistence of the token and client identity. See [`plex.md`](plex.md).
- **Viewing detector** — polling of `/status/sessions` (primary) with debounce, and
  `media.scrobble` webhook (optional). Emits the "show watched up to episode E" event.
- **Sync/reconciliation** — scans the Plex library and applies the window to the last
  watched of each show (button, on startup, periodic). Covers shows already started, manual marks
  and offline viewing. See [`behavior.md`](behavior.md).
- **Window engine** — applies GET / KEEP / Always-Have / grace; "force to Pilot" (opt-in). All
  writes to Sonarr go through `engine/actions.py`, guarded by dry-run. See [`behavior.md`](behavior.md).
- **Sonarr client** — monitor/unmonitor, trigger searches and delete files. See
  [`sonarr.md`](sonarr.md).
- **State/persistence** — tokens (account + server), client identity, and per-show/episode
  state for the grace periods and the debounce.
- **Configuration** — global policy + per-series overrides.

## Data flow

1. Login with Plex → token + discovered server (once).
2. Polling of `/status/sessions` (or `media.scrobble` webhook) → "watched up to E".
3. Correlation to TVDB via `/library/metadata/{grandparentRatingKey}?includeGuids=1`.
4. Window engine computes GET (monitor+search ahead) and KEEP/grace (delete behind,
   except Always-Have); in dry-run it only records.
5. Sonarr client executes: `episode/monitor` + `EpisodeSearch` and `episodefile` delete.

## Technical decisions

1. **Source: Plex directly** (source abstraction; Tautulli documented as an alternative).
   *Why*: it's the user's use case and avoids the dependency on a second service.
2. **Linking via Login with Plex (PIN/OAuth)**. *Why*: Plex doesn't expose the token
   easily; login avoids pasting it by hand and allows discovering the server.
3. **Detection via primary polling + optional webhook**. *Why*: polling works with only
   the login token (no Plex Pass or manual setup); the webhook gives lower latency to
   those who have Plex Pass. The webhook secret is auto-generated and stored in SQLite;
   `MONITORR_WEBHOOK_SECRET` is an optional override. On link, monitorr **auto-registers** its
   webhook URL in Plex via the account-level webhooks API (best-effort, never clobbering other
   integrations' URLs); a Settings button re-registers/removes it. Both paths share
   `process_watch()` and the same `user_filter`. See [`plex.md`](plex.md).
4. **"Watched" trigger at ~90%**. *Why*: it matches the real end of the episode (scrobble) and
   is reproducible by polling with `viewOffset/duration`.
5. **Combined deletion: count + grace**. *Why*: the count is predictable and grace covers
   inactivity.
6. **Global config + per-series override**. *Why*: simple to start, with an escape hatch for
   special cases without a per-series tag model.
7. **Dry-run = master switch (ON by default)**. *Why*: the app is destructive; with
   dry-run **nothing** is written to Sonarr (live and sync), it only previews. A single
   centralized switch in `engine/actions.py`.
8. **Reconciliation in addition to live detection**. *Why*: polling/webhook only see
   playbacks; the sync picks up what's already watched, what's marked by hand and what's watched with monitorr off.
9. **Force to Pilot opt-in (manual)**. *Why*: touching Sonarr's monitoring state is
   sensitive; it's done only when the user requests it.

## Pending decisions

- Aired vs absolute order (anime): the MVP uses aired order; support absolute later.
- Multi-user support (risk of deleting what someone else hasn't watched) — out of v1.
- The Web UI's own login (v1 assumes a trusted LAN / reverse proxy).
- Editing the per-series policy (override) from the UI; today the override only enables/disables.
