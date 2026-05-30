# monitorr

[![Release](https://img.shields.io/github/v/release/maxlainz/monitorr?sort=semver)](https://github.com/maxlainz/monitorr/releases)
[![CI](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml/badge.svg)](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

monitorr watches which shows you're viewing in **Plex** and, via the **Sonarr** API, keeps
*N* episodes **ahead** of your viewing point monitored/downloaded and keeps only *N*
**behind** (deleting the rest from disk through Sonarr), protecting key episodes such as the
pilot. Everything **API-only**: it never touches the media disk.

Instead of managing episodes by hand, monitorr applies a **sliding window** around what you're
actually watching.

## Features

- **Window ahead (GET)** — keeps *N* episodes ahead of the last watched one monitored;
  optionally triggers the Sonarr search to download them.
- **Window behind (KEEP)** — keeps only *N* episodes behind and **deletes the rest via
  Sonarr**, freeing up disk automatically.
- **Always-Have** — protects key episodes with patterns (`S01E01`, `S*E01`, `S01`, `S*`): they
  are never deleted even if they fall outside the window.
- **Grace periods** — deletion is deferred, not immediate after watching an episode; periodic
  sweeps apply the deletions when the deadline expires.
- **Configurable unit** — the window is measured in **episodes** or in **seasons**.
- **Dry-run by default** — master switch **enabled out of the box**: monitorr doesn't touch
  anything in Sonarr until you explicitly disable it. Try it without fear.
- **Live detection + reconciliation** — Plex session polling (with optional `media.scrobble`
  webhook) and a periodic sync that reconciles the watched state.
- **Login with Plex** — PIN/OAuth linking and automatic server discovery.
- **Global config + per-series overrides**, editable from the Web UI.
- **Single multi-arch image** (amd64/arm64), SQLite, no external services.

## How it works

```
Plex (session poll / webhook)  ──►  TVDB correlation  ──►  Sonarr (API)
   detects viewing (~90% watched)    Plex show ↔ Sonarr     monitor / search / delete
```

When you finish (or nearly finish) an episode, monitorr recomputes that show's window: it
monitors and searches for what's missing ahead, and marks for deletion what's surplus behind
(respecting Always-Have and the grace periods). A periodic sync reconciles everything in case
an event was missed.

> _(Web UI screenshot pending.)_

## Quick start

### docker compose (recommended)

Create a `docker-compose.yml` (or use the one in this repo):

```yaml
services:
  monitorr:
    image: ghcr.io/maxlainz/monitorr:latest   # or maxlainz/monitorr:latest (Docker Hub)
    container_name: monitorr
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
    environment:
      - TZ=Europe/Madrid
    restart: unless-stopped
```

Then start it:

```bash
docker compose up -d
```

### docker run

```bash
docker run -d \
  --name monitorr \
  -p 8080:8080 \
  -v "$(pwd)/config:/config" \
  -e TZ=Europe/Madrid \
  --restart unless-stopped \
  ghcr.io/maxlainz/monitorr:latest
```

Images available on **GHCR** (`ghcr.io/maxlainz/monitorr`) and **Docker Hub**
(`maxlainz/monitorr`), with tags `:1`, `:1.0`, `:1.0.0` and `:latest` for amd64 and arm64.

### First steps

1. Open the Web UI at `http://localhost:8080`.
2. **Link Plex** (Login with Plex) and choose your server.
3. Configure **Sonarr**: URL and API key (test button included).
4. Adjust the **policy**: episodes ahead (GET), behind (KEEP), Always-Have patterns,
   grace periods and unit (episode/season).
5. Once everything is ready, **disable dry-run** so monitorr starts acting.

## Configuration

The app configuration (Sonarr, window parameters, grace, dry-run, overrides) lives in
SQLite and is edited **from the Web UI**. Environment variables only cover infrastructure:

| Variable | Purpose | Default |
|---|---|---|
| `MONITORR_CONFIG_DIR` | Data directory (SQLite, Plex client identity) | `/config` |
| `MONITORR_PORT` | Listening port | `8080` |
| `MONITORR_LOG_LEVEL` | Log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) | `INFO` |
| `MONITORR_PLEX_POLL_INTERVAL` | Seconds between Plex session polls | `30` |
| `MONITORR_GRACE_SWEEP_INTERVAL` | Seconds between grace-period sweeps | `3600` |
| `MONITORR_SYNC_INTERVAL` | Seconds between syncs (`0` disables it) | `21600` |
| `MONITORR_SYNC_ON_STARTUP` | Sync once on startup if it never ran | `true` |
| `MONITORR_WEBHOOK_SECRET` | Token to protect the Plex webhook | (empty) |
| `TZ` | Time zone (affects grace periods) | `UTC` |

Template in [`.env.example`](.env.example).

## ⚠️ Security

monitorr **does not include its own authentication** in v1: it assumes it runs on a **trusted
LAN**. **Do not expose it directly to the internet.** If you need remote access, put it behind
a **reverse proxy with authentication** (Authelia, Authentik, basic-auth, etc.). The only
endpoint meant to be exposed, the optional Plex webhook, is protected with
`MONITORR_WEBHOOK_SECRET`.

## Development

```bash
uv sync
uv run monitorr            # Web UI at http://localhost:8080
```

Build/test/lint commands, deployment and the release process:
[`.claude/workflows.md`](.claude/workflows.md). Architecture and technical decisions in
[`.claude/`](.claude/).

## License

[GPL-3.0-or-later](LICENSE).
