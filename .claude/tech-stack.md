# Tech stack

Stack fijado para monitorr. El *porqué* de cada elección; los detalles operativos (comandos,
deploy, env vars) están en [`workflows.md`](workflows.md).

## Versiones y dependencias

- **Python 3.12** (pin en [`pyproject.toml`](../pyproject.toml) `requires-python` y en
  [`.python-version`](../.python-version); imagen base `python:3.12-slim`).
- **Gestión de deps: `uv`** con lockfile [`uv.lock`](../uv.lock). Reproducible y rápido;
  en Docker se instala con `uv sync --frozen --no-dev --no-editable`.
- Runtime: **FastAPI** + **Uvicorn** (un solo proceso ASGI), **httpx** (cliente async saliente),
  **Pydantic v2** + **pydantic-settings** (validación/config en fronteras), **aiosqlite**
  (SQLite async), **Jinja2** (plantillas), **python-multipart** (forms de la Web UI).
- Dev (grupo `dev`): **ruff**, **mypy**, **pytest** + **pytest-asyncio**, **respx**.

## Layout de directorios

Módulos **por dominio**, no por capa (ver [`rules.md`](rules.md)). Estructura en
[`README.md`](../README.md) y detallada:

- `src/monitorr/main.py` — app FastAPI, `lifespan` (arranca pollers), `/health`, monta web.
- `src/monitorr/config.py` — `Settings` (pydantic-settings, env `MONITORR_*`).
- `src/monitorr/db.py` — conexión aiosqlite + migraciones por `PRAGMA user_version`.
- `src/monitorr/logging.py` — configuración del logger (sin `print`).
- `src/monitorr/plex/` — login, discovery, poller, webhook, correlación. Ver [`plex.md`](plex.md).
- `src/monitorr/sonarr/` — cliente de la API de Sonarr. Ver [`sonarr.md`](sonarr.md).
- `src/monitorr/engine/` — `window.py` y `grace.py`. Ver [`behavior.md`](behavior.md).
- `src/monitorr/web/` — `routes.py`, `templates/` (Jinja2), `static/` (htmx + pico vendorados).

## Decisiones y porqué

- **Single-image, un solo proceso**: Uvicorn sirve API + Web UI en `:8080` y los pollers corren
  como tareas `asyncio` en el `lifespan`. Sin supervisord ni multiproceso → imagen y operación
  simples. Estado en el volumen `/config`.
- **Web UI server-rendered (HTMX + Jinja2), sin build de frontend**: `htmx.min.js` y `pico.min.css`
  se **vendoran** en `web/static/`. Evita una toolchain de Node y mantiene la imagen pequeña;
  encaja con un panel de config + dashboard.
- **SQLite vía aiosqlite, sin ORM**: encaja con single-image (fichero en volumen, sin servicio
  externo) y es transaccional para el estado por episodio/grace. Migraciones a mano por versión
  de esquema; sin ORM para no añadir abstracción prematura ([`rules.md`](rules.md)).
- **Clientes Plex/Sonarr propios con httpx**: la superficie usada es pequeña y ya está
  documentada; `python-plexapi`/`pyarr` quedan como **referencia** (son sync y pesados), no como
  dependencia, para mantener todo async-native.
- **Multi-arch (amd64 + arm64)**: público típico de apps *arr corre en NAS/Raspberry Pi. Todas
  las deps traen wheels precompilados (incl. `pydantic-core`), así que arm64 no compila nada.
  Se publica en **Docker Hub** (`maxlainz/monitorr`) y **GHCR** (`ghcr.io/maxlainz/monitorr`) por
  tag `vX.Y.Z` (ver [`workflows.md`](workflows.md)).
- **Config en SQLite + Web UI**: lo ajustable (Sonarr, ventana, grace, dry-run, overrides) se
  edita por la UI; las env vars solo cubren infraestructura. Plex se vincula por Login with Plex.

## Auth de la Web UI (v1)

Sin login propio: se asume LAN de confianza / reverse proxy. El único endpoint expuesto hacia
fuera (webhook opcional de Plex) se protege con `MONITORR_WEBHOOK_SECRET` en la URL. Un login
propio de la UI queda como pendiente (ver [`architecture.md`](architecture.md)).
