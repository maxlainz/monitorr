# Changelog

All notable changes to monitorr. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versioning is
[SemVer](https://semver.org/).

## [1.6.0] - 2026-06-04

### Changed

- **Always-Have episodes now stay monitored so Sonarr can upgrade them**: previously they were kept
  on disk but **unmonitored** once outside the GET window, which hid them from Sonarr's upgrade
  logic. The monitoring invariant is now `monitored ≡ GET window ∪ Always-Have`: Always-Have
  episodes (e.g. the pilot) stay monitored — and are re-monitored after the season cascade — so
  Sonarr can upgrade them in place and re-fetch them if their file is missing. Applied in both the
  watch window and **Normalize to Pilot**. The just-watched anchor and the kept-behind (KEEP)
  episodes are still unmonitored-while-kept, so a season of merely-kept episodes stays
  season-pack-proof; a season fully covered by an Always-Have pattern (`S*`/`S02`) may allow a
  season-pack upgrade of episodes the user marked to keep forever (accepted), and the season-pack
  guard still cancels any non-Always-Have surplus from the queue.

## [1.5.1] - 2026-06-04

### Fixed

- **Watched series no longer re-download themselves (sync anchor regression)**: the incremental sync
  derived its anchor only from the cycle's live Plex read (`allLeaves` on disk + the history delta),
  ignoring the persisted watch store. When a watched episode's file had already been trimmed **and**
  its play predated the history watermark, it was in neither source, so the anchor fell back to the
  furthest *on-disk* watch and the window slid backward — re-monitoring/searching already-watched
  back-catalog. Each re-download re-appeared in `allLeaves`, advancing the anchor again, so a
  fully-watched series re-downloaded itself N episodes per sync cycle. `apply_window` now **floors
  the anchor at the furthest episode ever recorded as watched** (clamped to one Sonarr lists),
  enforced for every path (sync, poller, webhook): the persisted, monotonic watch state — not the
  volatile on-disk state — is the authority for where the window anchors.

### Changed

- A one-time migration drops `last_full_sync` so the first cycle after upgrading runs a **full
  reconciliation**, re-anchoring and trimming the shows the regression left over-monitored.

## [1.5.0] - 2026-06-03

### Added

- **Incremental Plex reconciliation (history watermark)**: the periodic sync no longer re-scrapes
  Plex in full. It sweeps only the play history **newer than a stored watermark** (the newest
  `viewedAt` seen) and re-scans a show's library (`allLeaves`) **only when it has new plays**, instead
  of scanning every managed show each cycle. The watch state is already persisted in SQLite and kept
  fresh by the poller/webhook, so the watermark just fetches deltas.
- **Full reconciliation by cause, not by routine timer**: a full scan now runs only on the first
  connection of **both** Plex and Sonarr (or a Plex server change), the manual **"Sync now"** button,
  when there is no watermark or the history endpoint is unavailable, or a rolling safety floor since
  the last full. The floor is re-checked on each cycle **and at startup**, so a downtime that crosses
  it still triggers a full on the next boot.
- **`MONITORR_FULL_SYNC_INTERVAL`** env var: the rolling full-scrape floor in seconds (default
  `2592000` = 30 days; `0` disables it, leaving full only by connection / manual / blind-history).
- The status page now shows the **sync mode** (full/incremental) of the last run.

### Changed

- **"Sync now" forces a full reconciliation** (the deterministic "reconcile everything now"
  affordance); the periodic timer stays incremental.
- **Startup sync runs only when a full is overdue** (never ran, or the rolling floor elapsed) instead
  of "once if it never ran".
- **Fewer Sonarr calls per sync**: the window reuses the series already fetched by `list_series` (no
  per-show `find_series_by_tvdb`), and normalize + re-search share a single `get_episodes` per managed
  show and one queue snapshot (a just-normalized show skips the redundant re-search of its pilot).
- **HTTP connection pooling for the sync cycle**: every Plex/Sonarr call in a cycle reuses one
  connection (a `ContextVar`-scoped client) instead of a new TCP/TLS handshake per call. The
  poller/webhook/routes keep a fresh client per call.

## [1.4.2] - 2026-06-03

### Fixed

- **Re-adding an already-watched series still re-imported old episodes**: the back-catalog play
  history was queried **per show** with `metadataItemID={ratingKey}`. Plex assigns a series a **new
  ratingKey** when it is removed and re-added, so that query returned **nothing** (the old history is
  orphaned to the previous ids); the window then anchored on the furthest **still-on-disk** episode
  and searched/downloaded the next, already-watched one. The sync now sweeps the **global** play
  history once and correlates each show by **`grandparentTitle`** (stable across re-add), recovering
  the true last-watched regardless of ratingKey changes — and with one history call per sync instead
  of one per show.

### Added

- **Deep `DEBUG` tracing of the back-catalog detection and window** (gated at `DEBUG`, quiet at
  `INFO`): per-show `allLeaves`/history counts and the chosen anchor, the global history sweep
  summary, and `apply_window`'s GET-ahead / search-ahead / unmonitor / queue-cancel decisions, plus
  an anchor-regression warning.

## [1.4.1] - 2026-06-02

### Fixed

- **Back-catalog detection missed viewing of episodes whose files were deleted**: re-adding an
  already-watched series could re-download an early **whole season** instead of jumping to your
  last watched episode. The sync detected viewing only from `allLeaves`, which reflects the
  **current** Plex library — when the files of later seasons had been deleted, Plex dropped those
  episodes from `allLeaves`, so their viewing was invisible and the window anchored on the highest
  still-present episode (e.g. mid-S1). The sync now **also reads Plex's play history**
  (`/status/sessions/history/all`), which persists independently of the files, and unions it with
  the library state to anchor on the **true last-watched** episode.
- **Season-pack grabs imported the whole season**: a search for the GET window could make Sonarr
  grab a full season pack and import every episode (including ones already watched). The window now
  **cancels from Sonarr's queue** (`removeFromClient=false`, so the client keeps seeding) any
  episode being downloaded that falls **outside** GET ∪ KEEP ∪ Always-Have, so only the window
  lands on disk.
- **Live window slid backward on re-watch / gap-fill**: a `media.scrobble` or polled play
  recomputed the window around the episode just played, so re-watching or filling an earlier gap of
  an already-watched show pulled the GET window back and re-downloaded episodes already seen. It now
  anchors on the **furthest-watched** episode (the maximum recorded watch), consistent with the sync
  and the grace sweep — advancing slides forward, an earlier play never drags it backward.

## [1.4.0] - 2026-06-01

### Added

- **Per-series policy editing in the Web UI**: each show now has its own page (Series → *Edit*)
  to override the full policy — GET/KEEP and units, Always-Have, the grace periods,
  `search_on_get` and `auto_normalize` — not just enable/disable. The override is stored as a
  complete snapshot and replaces the global policy for that show; **Reset to global** drops it so
  the show follows the global policy again. The Series list shows a **custom/global** badge per
  show. `dry_run`, the watched threshold and the Plex user filter remain global.

### Fixed

- **Toggling enable/disable wiped a per-series policy**: the enable/disable switch used to clear
  any stored per-series override. It now preserves the override and only flips the enabled flag.

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
