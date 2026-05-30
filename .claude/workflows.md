# Workflows

> Keep up to date per [`documentation.md`](documentation.md): any new command
> is documented here.

## Branches and daily work

- **`dev`**: working branch. **All development happens here.** Never commit
  directly to `main`.
- **`main`**: stable/published branch. It only receives explicit merges from `dev`
  (see [Merge to `main`](#merge-to-main)).

Work cycle on `dev` (after each change):

```bash
git checkout dev            # make sure you're on dev before editing
# … edit code and docs …
git add -A
git commit -m "message in English"
git push                   # immediate push after each commit
```

Before starting to edit, always check the current branch with `git status`; if you're
not on `dev`, switch with `git checkout dev`.

## Development commands

```bash
uv sync                    # creates/updates .venv from uv.lock
uv run monitorr            # starts the app (Web UI at http://localhost:8080)
```

By default the DB is created in `/config`; locally export `MONITORR_CONFIG_DIR=./config` to
avoid needing permissions on `/config`.

## Build / test / lint

```bash
uv run ruff check .            # lint
uv run ruff format .           # format (or --check to validate without touching)
uv run mypy .                  # strict type checking
uv run pytest                  # tests
```

Single pre-push command (the same one CI runs):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Docker image:

```bash
docker build -t monitorr .                                          # local build (current arch)
docker buildx build --platform linux/amd64,linux/arm64 -t monitorr . # multi-arch
```

Image publishing is automatic by tag (see [Publishing and release](#publishing-and-release)).

## Deploy

Single image in Docker. Example in [`docker-compose.yml`](../docker-compose.yml): maps
`8080:8080` and mounts a volume on `/config` (SQLite + Plex client identity).

```bash
docker compose up -d
```

The container starts as **root**, then [`docker-entrypoint.sh`](../docker-entrypoint.sh) remaps the
`app` user to `PUID`/`PGID`, `chown`s `/config` and drops privileges via `gosu` before running
`monitorr`. This makes a bind-mounted `/config` writable regardless of its host ownership (the
original cause of the `unable to open database file` startup crash on non-root images).

## Environment variables

Infrastructure only; the app config (Sonarr, window, grace, overrides) lives in SQLite and
is edited via the Web UI. Defined in [`config.py`](../src/monitorr/config.py).

| Var | Purpose | Default | Required |
|---|---|---|---|
| `MONITORR_CONFIG_DIR` | Data dir (SQLite, client identity) | `/config` | no |
| `MONITORR_PORT` | Listening port | `8080` | no |
| `MONITORR_LOG_LEVEL` | Log level | `INFO` | no |
| `MONITORR_PLEX_POLL_INTERVAL` | Seconds between session polls | `30` | no |
| `MONITORR_GRACE_SWEEP_INTERVAL` | Seconds between grace-period sweeps | `3600` | no |
| `MONITORR_SYNC_INTERVAL` | Seconds between watched-state syncs (`0` disables) | `21600` | no |
| `MONITORR_SYNC_ON_STARTUP` | Sync once on startup if it never ran | `true` | no |
| `MONITORR_WEBHOOK_SECRET` | Token for the optional webhook endpoint | (empty) | no |
| `TZ` | Time zone (grace periods) | `UTC` | no |

Container-only (read by [`docker-entrypoint.sh`](../docker-entrypoint.sh), **not** by `config.py`):

| Var | Purpose | Default | Required |
|---|---|---|---|
| `PUID` | UID the process runs as / owner of `/config` | `1000` | no |
| `PGID` | GID the process runs as | `1000` | no |

## Merge to `main`

`main` only receives explicit merges. `CLAUDE.md` and `.claude/` **travel to `main`** (they are
not excluded). The merge message summarizes everything new since the previous commit on `main`.

```bash
git checkout main
git merge dev --no-ff
# edit the message to summarize everything new since the last commit on main
git push
git checkout dev
```

## Publishing and release

The image is published **automatically when pushing a semver tag `vX.Y.Z`** via
[`.github/workflows/release.yml`](../.github/workflows/release.yml):

- Multi-arch build (`linux/amd64,linux/arm64`) and push to **Docker Hub** (`maxlainz/monitorr`) and
  **GHCR** (`ghcr.io/maxlainz/monitorr`) with tags `:X.Y.Z`, `:X.Y`, `:X` and `:latest`.
- Injects `VERSION`/`VCS_REF`/`BUILD_DATE` as build-args (OCI labels + `/version` endpoint).
- **Version guard**: the workflow fails if the tag doesn't match the `version` in `pyproject.toml`.
- Creates the **GitHub Release** with the notes from the corresponding section of
  [`CHANGELOG.md`](../CHANGELOG.md).

Steps for a release:

```bash
# 1) on dev: bump the version and the changelog
#    - pyproject.toml  → version = "X.Y.Z"
#    - CHANGELOG.md     → new section [X.Y.Z]
git commit -am "chore: release vX.Y.Z" && git push
# 2) merge to main (see above)
# 3) tag from main → triggers the publishing
git tag vX.Y.Z && git push origin vX.Y.Z
```

**Required secrets** (GitHub → Settings → Secrets and variables → Actions): `DOCKERHUB_USERNAME`
and `DOCKERHUB_TOKEN` (Docker Hub Access Token). GHCR uses the automatic `GITHUB_TOKEN`. After the
first push to GHCR, mark the package as **public** and link it to the repo.
