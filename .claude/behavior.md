# Behavior: episode window

monitorr's core logic. Defines **what** it does when it detects that an episode has been watched.
The *how* of each external system lives in [`plex.md`](plex.md) (detection/correlation) and
[`sonarr.md`](sonarr.md) (monitor/search/delete).

## Trigger

The engine activates when a show is considered **watched up to episode E** (anchor):
- By polling: `viewOffset/duration ≥ ~0.9`, or a session that was **almost complete**
  (`progress ≥ NEAR_COMPLETE_PROGRESS`, 0.85 in `constants.py`) **disappears** between polls
  (the user finished and the session closed before crossing the threshold).
- By optional webhook: `media.scrobble` event.

Each trigger recomputes the window of **that show** around E. The trigger is idempotent:
the poller's debounce uses the key `(sessionKey, season, episode)`, not just `sessionKey`
(Plex can reuse the `sessionKey` when auto-playing the next episode of a binge, so
advancing an episode triggers, but re-polling the same one doesn't).

## Window

Two parameters, configurable in **episodes or seasons**:

- **GET (N ahead)**: keep **exactly** the N episodes following E in airing order.
  It monitors and searches them (`episode/monitor` + `EpisodeSearch`) and **trims the surplus
  ahead**: anything beyond the window is **deleted** (`episodefile` delete) if it's on
  disk or **unmonitored** if it hasn't been downloaded yet (reason `ahead`), except *Always-Have*.
  It's a sliding window: when advancing an episode the edge is re-monitored/searched.
- **KEEP (N behind)**: keep on disk the N episodes before E (including E). The
  rest, older than the KEEP window, is **deleted** (`episodefile` delete) and
  **unmonitored** (reason `keep`), unless protected by *Always-Have*.

## Always-Have (protection)

Episodes that are **never** deleted even if they fall outside KEEP or a grace period. Patterns:
`S01E01` (pilot), `S*E01` (first episode of each season), `S*` (full season). It's
the first check before any deletion.

## Grace periods (deletion by inactivity)

They complement KEEP with a temporal criterion (days without activity on the show):

- **watched**: deletes already-watched episodes after X days. *Default: 7.*
- **unwatched**: deletes unwatched episodes after X days. *Default: 365.*
- **dormant**: deletes everything deletable from the show if it has gone X days without viewing.
  *Default: unassigned (`None`) → disabled: an inactive show is never purged in bulk.*

Each grace is independent; leaving one **unassigned** disables it. It requires **persisting state**
per show/episode (last watched, first unwatched, last activity). They respect Always-Have.

## Dry-run (master switch)

`dry_run` is the **master safety switch**, **ON by default**. With dry-run ON,
monitorr **performs no writes to Sonarr**: no monitoring, no searching, no
[normalize to Pilot](#normalize-to-pilot), no deleting. It only records/logs what it would do; the
deletions remain as **pending** for review. It applies to everything (live logic and
[sync](#sync--reconciliation)). Centralized in `engine/actions.py`. When
you trust the behavior, you disable it in Settings and everything starts running for real.

The pending **previews** are **deduplicated** (one row per episode: the grace sweep and the
sync re-preview each cycle without accumulating duplicates) and **auto-cleaned**: when the
episode is deleted for real its preview is removed, and when dry-run is disabled they're all emptied. The history of
real deletions is always kept.

## Normalize to Pilot

Leaves **only the pilot** (`S01E01`) monitored, searching for it if it's missing a file. The
**already-downloaded** episodes that end up unmonitored are **deleted** (except Always-Have) —
unmonitoring an episode on disk implies deleting it. From there the window (GET) monitors forward
episode by episode. It removes the need to configure "Monitor: Pilot" by hand in Sonarr.

It's **automatic (set-and-forget)** and its **only trigger is the
[sync](#sync--reconciliation)**: each cycle it normalizes every managed show
**with no recorded viewing**, preventing Sonarr's RSS/cron from accumulating downloads of newly
added shows. Shows **with** viewing are handled by the window and not touched here. There is no manual
action. Configurable via `auto_normalize` (ON by default), with per-series override; respects dry-run.

**Unmonitored episodes that are still downloading** (not imported): they're pulled from Sonarr's
queue (`DELETE /queue/{id}` with `removeFromClient=false`) so they **aren't imported**; the torrent
stays in the client seeding until its ratio, which the download client itself removes (not
monitorr). Without unmonitoring first, Sonarr would import the already-started download anyway.

## Sync / reconciliation

Live detection (poller + webhook) only triggers when an episode is watched. The **sync** reconciles the
watched state by reading the Plex library (see [`plex.md`](plex.md)): for each show managed by
Sonarr, it records the watched episodes with their real date (feeds the grace periods) and applies the
window for the **last watched**. It covers three cases the live mode doesn't see: shows already started at
install, episodes marked by hand in Plex, and viewing with monitorr off. It also **normalizes
to pilot** the managed shows with no viewing (see [Normalize to Pilot](#normalize-to-pilot)). It's
triggered by the "Sync now" button, on startup (once) and periodically. It inherits the engine's
dry-run.

Finally, each sync **re-searches Sonarr's Wanted/Missing**: every managed show's episodes that are
still **monitored, already aired and without a file** are searched again (`EpisodeSearch`),
**excluding** the ones already downloading (present in Sonarr's `queue`). This recovers from a
transient indexer outage where the search at monitor-time found nothing. It is governed by the same
`search_on_get` flag (ON by default) and respects per-series `enabled` and dry-run.

## Configuration

- **Single global**: one policy (GET, KEEP, Always-Have, grace, auto-normalize) for all
  shows + the dry-run switch.
- **Per-series override**: manual settings that replace the global policy on specific shows.

## Seasons unit (semantics)

- **GET by seasons (N)**: keeps the episodes after E with `season ≤ E.season + N`
  (rest of the current season + the next N); anything in later seasons is trimmed
  (deleted if on disk, unmonitored if not).
- **KEEP by seasons (N)**: keeps the episodes with `season ≥ E.season − (N−1)`; deletes
  those of older seasons.
- **Season-level monitoring**: monitorr is also the authority over the `monitored` flag
  of each **season** (not just the episode), because the new episodes Sonarr discovers
  **inherit their season's flag** and would auto-monitor. By **episodes** it leaves **all**
  seasons off (100% per-episode control); by **seasons** it leaves on only those in the
  GET window. It's applied in window and normalize (seasons before episodes, cascade-proof),
  via `GET`-modify-`PUT /series/{id}` only if it changes, and respects dry-run. It neutralizes
  season overrides done by hand in Sonarr.

## Edge cases (resolved in the MVP)

- **Season crossover**: the window reasons in **airing order** `(season, episode)`, not by
  isolated season; the "next" one can fall in the following season.
- **Specials (`S00`)**: **excluded** from GET/KEEP/grace.
- **Episode without file** (`hasFile:false`): there's nothing to delete; monitoring only.
- **Aired vs absolute order (anime)**: the MVP always uses aired order `(season, episode)`.
  Known limitation: anime with absolute numbering may not order as expected.
- **Multi-user**: out of v1 (a single consumer is assumed). There is an **optional user filter**
  in Settings to limit which playbacks trigger actions.
- **Resilient sync**: a show with an error (404, timeout, nonexistent episode) is logged and
  skipped; it doesn't abort the rest of the sync nor leave `last_sync` unupdated.
- **Session without TVDB**: it's warned once and cached so it isn't re-resolved (or re-warned) on each
  poll; it's retried when that show is played again.
