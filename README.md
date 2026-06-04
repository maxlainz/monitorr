# monitorr

[![Release](https://img.shields.io/github/v/release/maxlainz/monitorr?sort=semver)](https://github.com/maxlainz/monitorr/releases)
[![CI](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml/badge.svg)](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/maxlainz/monitorr?logo=docker)](https://hub.docker.com/r/maxlainz/monitorr)
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
- **Back-catalog aware (retroactive)** — add a show you already watched in Plex (e.g. a new
  season is dropping) and the sync reads your Plex watch history and jumps the window straight
  to your **last watched episode** — it doesn't re-download the series from the pilot.
- **Login with Plex** — PIN/OAuth linking and automatic server discovery.
- **Global config + per-series overrides** — edit GET/KEEP/Always-Have/grace per show (or just
  enable/disable it), then Reset to global anytime. All from the Web UI.
- **Single multi-arch image** (amd64/arm64), SQLite, no external services.

## How it works

```
Plex (session poll / webhook)  ──►  TVDB correlation  ──►  Sonarr (API)
   detects viewing (~90% watched)    Plex show ↔ Sonarr     monitor / search / delete
```

When you finish (or nearly finish) an episode, monitorr recomputes that show's window: it
monitors and searches for what's missing ahead, and marks for deletion what's surplus behind
(respecting Always-Have and the grace periods). A periodic sync reconciles everything in case
an event was missed — and it reads your **existing** Plex watch history, so a show you watched
before installing monitorr (or added afterwards) is handled from your real viewing point, not
from scratch.

> _(Web UI screenshot pending.)_

## Quick start

### docker compose (recommended)

Create a `docker-compose.yml` (or use the one in this repo):

```yaml
services:
  monitorr:
    image: maxlainz/monitorr:latest   # or ghcr.io/maxlainz/monitorr:latest (GHCR)
    container_name: monitorr
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
    environment:
      - TZ=Europe/Madrid
      # - PUID=1000   # host user/group that should own ./config (see Permissions below)
      # - PGID=1000
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
  maxlainz/monitorr:latest
```

Images available on **Docker Hub** (`maxlainz/monitorr`) and **GHCR**
(`ghcr.io/maxlainz/monitorr`), with tags `:1`, `:1.0`, `:1.0.0` and `:latest` for amd64 and arm64.

### First steps

1. Open the Web UI at `http://localhost:8080`.
2. **Link Plex** (Login with Plex) and choose your server.
3. Configure **Sonarr**: URL and API key (test button included).
4. Adjust the **policy**: episodes ahead (GET), behind (KEEP), Always-Have patterns,
   grace periods and unit (episode/season).
5. Once everything is ready, **disable dry-run** so monitorr starts acting.

> **Optional (Plex Pass):** for lower-latency detection, copy the **webhook URL** shown under
> *Settings → Plex webhook* into Plex (*Settings → Webhooks → Add Webhook*). It's complementary
> to the always-on session poller; you don't need it.

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
| `MONITORR_WEBHOOK_SECRET` | Override for the Plex webhook secret (auto-generated if empty) | (empty) |
| `TZ` | Time zone (affects grace periods) | `UTC` |

Template in [`.env.example`](.env.example).

### Permissions (PUID/PGID)

monitorr stores its state in the bind-mounted `/config`. The container starts as root, then drops
to a normal user before running the app and fixes ownership of `/config` so it stays writable.
Two **container-only** variables control that user (they are read by the entrypoint, not by the
app):

| Variable | Purpose | Default |
|---|---|---|
| `PUID` | User id the process runs as (and owner of `/config`) | `1000` |
| `PGID` | Group id the process runs as | `1000` |

The defaults (`1000:1000`) match the typical desktop/NAS user, so most setups need nothing. If your
host user differs, set `PUID`/`PGID` to its `id -u` / `id -g` so files in `./config` stay owned by
you. (If you override the container user yourself, e.g. compose `user:`, the entrypoint skips the
remap and just runs as that user.)

## ⚠️ Security

monitorr **does not include its own authentication** in v1: it assumes it runs on a **trusted
LAN**. **Do not expose it directly to the internet.** If you need remote access, put it behind
a **reverse proxy with authentication** (Authelia, Authentik, basic-auth, etc.). The only
endpoint meant to be exposed, the optional Plex webhook, is protected with an auto-generated
secret embedded in its URL (overridable with `MONITORR_WEBHOOK_SECRET`).

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
