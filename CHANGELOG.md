# Changelog

All notable changes to monitorr. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versioning is
[SemVer](https://semver.org/).

## [Unreleased]

### Added

- **Plex webhook is now configurable from the UI**: *Settings → Plex webhook* shows a
  ready-to-copy URL with an auto-generated secret (plus Copy and Regenerate). No env var or
  manual URL crafting needed; `MONITORR_WEBHOOK_SECRET` becomes an optional override.

### Changed

- The webhook now honors the same Plex `user_filter` as the session poller, so both detection
  paths behave identically.

### Fixed

- **Startup crash on bind-mounted `/config`** (`sqlite3.OperationalError: unable to open database
  file`): the image ran as a fixed non-root user that couldn't write a host-owned bind mount. It
  now starts as root and an entrypoint remaps the user to `PUID`/`PGID` (default `1000:1000`),
  fixes `/config` ownership and drops privileges via `gosu` before running.

## [1.0.1] - 2026-05-30

### Changed

- Project language switched to English: all documentation and the Web UI are now in English,
  along with internal code comments, docstrings and log messages.

### Removed

- All references to episeerr; the positioning was rephrased to keep the sliding-window value
  proposition without naming it.

## [1.0.0] - 2026-05-30

First public release. monitorr watches viewing in Plex and manages episodes in Sonarr
**API-only**, keeping a sliding window around what you watch.

### Added

- **Episode window** GET (ahead) / KEEP (behind) with unit per episode or season; option to
  trigger the Sonarr search for the episodes ahead.
- **Always-Have**: protection of key episodes by patterns (`S01E01`, `S*E01`, `S01`, `S*`).
- **Grace periods**: deferred deletion with periodic sweeps (watched / unwatched / dormant).
- **Dry-run** as a master switch, enabled by default.
- **Viewing detection** via Plex session polling and optional `media.scrobble` webhook, with
  a trigger at ~90% watched and debounce.
- **Periodic sync/reconciliation** of the watched state; auto-normalization to pilot of
  shows with no viewing.
- **Login with Plex** (PIN/OAuth), server discovery and Plex↔Sonarr correlation via TVDB.
- **Sonarr client** (v3/v4): monitor/unmonitor, search, delete and queue management.
- **Server-rendered Web UI** (HTMX + Jinja2): status, series, deletion history and settings,
  with global config and per-series overrides.
- **Persistence** in SQLite (aiosqlite, WAL) with migrations by schema version.
- **Single multi-arch Docker image** (amd64/arm64), `/health` and `/version` endpoints, and
  automated publishing to Docker Hub and GHCR via `vX.Y.Z` tags.

[1.0.1]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.1
[1.0.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.0
