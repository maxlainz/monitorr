# Changelog

All notable changes to monitorr. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versioning is
[SemVer](https://semver.org/).

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

[1.0.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.0
