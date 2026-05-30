# Tech stack

Fixed stack for monitorr. The *why* of each choice; the operational details (commands,
deploy, env vars) are in [`workflows.md`](workflows.md).

## Versions and dependencies

- **Python 3.12** (pin in [`pyproject.toml`](../pyproject.toml) `requires-python` and in
  [`.python-version`](../.python-version); base image `python:3.12-slim`).
- **Dep management: `uv`** with lockfile [`uv.lock`](../uv.lock). Reproducible and fast;
  in Docker it installs with `uv sync --frozen --no-dev --no-editable`.
- Runtime: **FastAPI** + **Uvicorn** (a single ASGI process), **httpx** (async outbound client),
  **Pydantic v2** + **pydantic-settings** (validation/config at boundaries), **aiosqlite**
  (async SQLite), **Jinja2** (templates), **python-multipart** (Web UI forms).
- Dev (group `dev`): **ruff**, **mypy**, **pytest** + **pytest-asyncio**, **respx**.

## Directory layout

Modules **by domain**, not by layer (see [`rules.md`](rules.md)). Structure in
[`README.md`](../README.md) and detailed:

- `src/monitorr/main.py` — FastAPI app, `lifespan` (starts pollers), `/health`, mounts web.
- `src/monitorr/config.py` — `Settings` (pydantic-settings, env `MONITORR_*`).
- `src/monitorr/db.py` — aiosqlite connection + migrations by `PRAGMA user_version`.
- `src/monitorr/logging.py` — logger configuration (no `print`).
- `src/monitorr/plex/` — login, discovery, poller, webhook, correlation. See [`plex.md`](plex.md).
- `src/monitorr/sonarr/` — Sonarr API client. See [`sonarr.md`](sonarr.md).
- `src/monitorr/engine/` — `window.py` and `grace.py`. See [`behavior.md`](behavior.md).
- `src/monitorr/web/` — `routes.py`, `templates/` (Jinja2), `static/` (vendored htmx + pico).

## Decisions and why

- **Single-image, single process**: Uvicorn serves API + Web UI on `:8080` and the pollers run
  as `asyncio` tasks in the `lifespan`. No supervisord or multiprocess → simple image and
  operation. State in the `/config` volume.
- **Server-rendered Web UI (HTMX + Jinja2), no frontend build**: `htmx.min.js` and `pico.min.css`
  are **vendored** in `web/static/`. Avoids a Node toolchain and keeps the image small;
  fits a config panel + dashboard.
- **SQLite via aiosqlite, no ORM**: fits single-image (file in a volume, no external
  service) and is transactional for the per-episode/grace state. Migrations by hand per schema
  version; no ORM to avoid adding premature abstraction ([`rules.md`](rules.md)).
- **Own Plex/Sonarr clients with httpx**: the surface used is small and already
  documented; `python-plexapi`/`pyarr` remain as **reference** (they are sync and heavy), not as
  a dependency, to keep everything async-native.
- **Multi-arch (amd64 + arm64)**: the typical audience for *arr apps runs on NAS/Raspberry Pi. All
  deps ship precompiled wheels (incl. `pydantic-core`), so arm64 compiles nothing.
  Published on **Docker Hub** (`maxlainz/monitorr`) and **GHCR** (`ghcr.io/maxlainz/monitorr`) by
  `vX.Y.Z` tag (see [`workflows.md`](workflows.md)).
- **Config in SQLite + Web UI**: the adjustable parts (Sonarr, window, grace, dry-run, overrides) are
  edited via the UI; env vars only cover infrastructure. Plex is linked via Login with Plex.

## Web UI auth (v1)

No own login: a trusted LAN / reverse proxy is assumed. The only endpoint exposed to the
outside (optional Plex webhook) is protected with `MONITORR_WEBHOOK_SECRET` in the URL. The UI's
own login remains pending (see [`architecture.md`](architecture.md)).
