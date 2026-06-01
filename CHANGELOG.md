# Changelog

All notable changes to monitorr. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versioning is
[SemVer](https://semver.org/).

## [1.3.0] - 2026-06-01

### Added

- **Auto-registration of the `media.scrobble` webhook in Plex on link**: after Login with Plex,
  monitorr pushes its `/webhook/plex/{secret}` URL into the account-level Plex webhooks API
  (best-effort), so Plex Pass users get low-latency detection without copy-pasting it. A Settings
  button re-registers / removes it, Regenerate re-syncs only when already registered, and Unlink
  removes monitorr's entry. The account-wide list (shared with other integrations) is never
  clobbered — GET → drop our entries → append → POST — and the webhook URL field is editable for
  reachability (localhost / reverse proxy).

### Documentation

- **Retroactive (back-catalog) watch detection** is now documented: adding a show you already
  watched in Plex (e.g. when a new season drops) makes the sync read your Plex watch history and
  jump the window to your last watched episode instead of re-downloading from the pilot. Existing
  behaviour — no functional change.

## [1.2.1] - 2026-06-01

### Fixed

- **Grace deleted episodes inside the KEEP window**: the `watched`/`unwatched` grace periods
  ignored KEEP, so with *keep 1 season behind* an episode of the season you were actively watching
  could still be removed once it aged past the grace days. KEEP is now a **retention floor** for
  both ongoing trims — computed relative to your latest watched episode — so they only delete what
  already falls outside KEEP. The bulk purges (`completed`/`dormant`) intentionally still ignore
  KEEP, and Always-Have remains the only protection over every deletion path.

## [1.2.0] - 2026-05-31

### Added

- **`completed` grace period** (enabled, default 30 days): purges a show (except Always-Have) once
  you've watched its last aired episode and it has been inactive that many days — covering both
  finished series and shows on hiatus. Unlike `dormant`, it never deletes aired episodes you haven't
  watched yet, and GET automatically re-arms via the next sync when a new season airs.
- **Re-search of Sonarr's Wanted/Missing on every sync**: monitored episodes that have aired and
  still lack a file are searched again on each sync (excluding those already downloading in Sonarr's
  queue), recovering from transient indexer outages where the monitor-time search found nothing.
  Governed by the existing `search_on_get` flag (ON by default); the sync summary now reports a
  **searched** count, surfaced on the home page.

### Changed

- **Upgrade note**: the new `completed` grace defaults to **30 days, enabled**. Installations
  running with **dry-run OFF** will start purging finished/hiatus shows 30 days after they go
  inactive. Set *Settings → Completed (days)* to empty to disable it. With dry-run ON (the default)
  nothing is deleted for real.

### Fixed

- **Useless season-pack "upgrades"** (hundreds of GB): advancing through a season left every
  episode both on disk and monitored — the all-episodes-monitored state Sonarr needs to grab a
  season pack. Unmonitoring is now decoupled from deletion: monitoring intent tracks the GET-ahead
  window only (`monitored ≡ GET window`), independent of file retention, so kept/watched and
  on-disk Always-Have episodes are unmonitored while their files are preserved.

## [1.1.0] - 2026-05-30

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

[1.2.1]: https://github.com/maxlainz/monitorr/releases/tag/v1.2.1
[1.2.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.2.0
[1.1.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.1.0
[1.0.1]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.1
[1.0.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.0
