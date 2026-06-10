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

Each trigger records E and recomputes the window of **that show** around the **furthest-watched
episode** (the maximum recorded watch, including E), consistent with the
[sync](#sync--reconciliation) and the [grace sweep](#grace-periods-deletion-by-inactivity). So
advancing an episode slides the window forward, while **re-watching or filling an earlier gap does
not drag it backward** (which would re-download already-watched episodes). The trigger is
idempotent: the poller's debounce uses the key `(sessionKey, season, episode)`, not just
`sessionKey` (Plex can reuse the `sessionKey` when auto-playing the next episode of a binge, so
advancing an episode triggers, but re-polling the same one doesn't).

## Window

Two parameters, configurable in **episodes or seasons**:

- **GET (N ahead)**: keep **exactly** the N episodes following E in airing order.
  It monitors and searches them (`episode/monitor` + `EpisodeSearch`) and **trims the surplus
  ahead**: anything beyond the window is **deleted** (`episodefile` delete) if it's on
  disk or **unmonitored** if it hasn't been downloaded yet (reason `ahead`), except *Always-Have*.
  It's a sliding window: when advancing an episode the edge is re-monitored/searched.
  - **Season-pack guard**: searching an episode can make Sonarr grab a **full season pack** and
    import the whole season. After computing the window, monitorr **cancels from the queue**
    (`DELETE /queue/{id}` with `removeFromClient=false`, so the client keeps seeding) any episode
    being downloaded that falls **outside** GET ∪ KEEP ∪ Always-Have — so only the window lands on
    disk. Already-imported surplus is removed by the trims above.
- **KEEP (N behind)**: keep on disk the N episodes before E (including E). The
  rest, older than the KEEP window, is **deleted** (`episodefile` delete) and
  **unmonitored** (reason `keep`), unless protected by *Always-Have*.

**Monitoring is decoupled from retention**: `monitored ≡ GET window ∪ Always-Have`. The GET-ahead
episodes **and** the *Always-Have* episodes stay monitored — what monitorr actively wants Sonarr to
fetch/**upgrade**; Always-Have episodes stay monitored on purpose so Sonarr can upgrade them in
place (and re-fetch them if their file is missing). **Everything else is unmonitored while keeping
its file** — the just-watched anchor and the kept-behind episodes (KEEP). Their file is retained by
KEEP; the monitoring is dropped so Sonarr never tries to **upgrade** an episode that won't be
re-watched, and a season of merely-kept episodes never reaches the *all-episodes-monitored* state
that lets Sonarr grab a **season-pack upgrade** (see `sonarr.md`). The exception is a season fully
covered by an Always-Have pattern (`S*`/`S02`): every episode there is monitored (and is one the
user marked to keep forever), so a season-pack upgrade of those is acceptable. Unmonitoring is
non-destructive (no preview/log entry like deletions). In by-seasons mode the GET-window **seasons**
stay monitored at the season level (so new episodes inherit it), while their already-watched
**episodes** are unmonitored individually — the season-pack-proof combination.

## Always-Have (protection)

Episodes that are **never** deleted even if they fall outside KEEP or a grace period. Patterns:
`S01E01` (pilot), `S*E01` (first episode of each season), `S*` (full season). It's
the first check before any deletion. Unlike the merely-kept episodes (anchor/KEEP), Always-Have
episodes also **stay monitored** — the only protection that keeps both the file **and** the
monitoring — so Sonarr can upgrade them in place (and re-fetch them if their file is missing).

## Grace periods (deletion by inactivity)

They complement KEEP with a temporal criterion (days without activity on the show):

- **watched**: deletes already-watched episodes after X days, keeping the most recent one as a
  marker. *Default: 7.*
- **unwatched**: deletes unwatched episodes after X days. *Default: 365.*
- **dormant**: deletes everything deletable from the show if it has gone X days without viewing,
  regardless of whether episodes are watched. *Default: unassigned (`None`) → disabled: an inactive
  show is never purged in bulk.*
- **completed**: purges everything deletable (except Always-Have) when the show is **finished or on
  hiatus** and has gone X days without activity. *Default: 30, enabled.* It's **`dormant` + the
  "caught up" filter**: it only fires when the **last aired episode has been watched** (nothing
  aired is left unseen), so it never deletes aired-but-unwatched episodes. The combination
  "caught up + inactive X days" distinguishes the cases without inspecting Sonarr's series status or
  the next air date: a weekly show followed on time never reaches X inactive days while caught up
  (each new episode resets the clock); one dropped mid-run isn't caught up (aired episodes pile up
  unseen); a finished/hiatus show is caught up and goes inactive → purged. A future (unaired)
  episode doesn't count, since it isn't downloadable yet. **GET re-arms** when the show returns: the
  purge keeps the `episode_watch` anchor, so the next [sync](#sync--reconciliation) runs
  `apply_window` over the last watched and monitors/searches the new season ahead (this happens via
  sync, not live, because an undownloaded episode can't be played to trigger the live path).

**KEEP is a retention floor for the ongoing trims.** `watched` and `unwatched` never delete what the
KEEP window guarantees on disk, computed relative to the **latest watched episode** (the viewing
point): KEEP is the spatial guarantee, these graces only trim what already falls **outside** it. So
"KEEP 1 season behind" protects the whole season you're currently watching even from `grace_watched`.
The bulk purges `completed` and `dormant` **intentionally ignore KEEP** — they reclaim everything
deletable because the show is finished/abandoned (already gated on caught-up/inactivity). The shared
KEEP predicate lives in `engine/window.py` (`keep_protected_keys`). **Always-Have** remains the only
protection that overrides *every* deletion path.

**The GET arm is gated by the graces' inactivity clock.** Once a show has been inactive longer
than `dormant` or `unwatched` (whichever is assigned and smaller), `apply_window` stops arming the
GET window: nothing ahead is monitored or searched, in seasons mode every season is left OFF (new
episodes must not inherit monitoring), the still-monitored GET edge is unmonitored (file untouched)
and in-flight ahead downloads are pulled from the queue. Without the gate, every **full sync**
re-applied the window of an abandoned show — re-downloading the GET window for the next grace sweep
to delete, an endless download/delete oscillation. The spatial trims and Always-Have are unaffected.
A live watch records activity **before** applying the window, so resuming the show re-arms GET
naturally. `completed` intentionally does **not** gate: a caught-up show must re-arm when a new
season airs (see "GET re-arms" above). Implemented as `_is_armed` in `engine/window.py`, sharing
`age_days` with the sweep.

Each grace is independent; leaving one **unassigned** disables it. It requires **persisting state**
per show/episode (last watched, first unwatched, last activity). They respect Always-Have. Implemented
in `engine/grace.py`; `completed` is evaluated before `dormant` so a caught-up purge is logged with
the more precise `completed` reason.

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

Leaves the pilot (`S01E01`) **and the Always-Have episodes** monitored (so Sonarr can upgrade
them), searching for the pilot if it's missing a file. The **already-downloaded** episodes that end
up unmonitored are **deleted** (except Always-Have) — unmonitoring an episode on disk implies
deleting it. From there the window (GET) monitors forward episode by episode. It removes the need to
configure "Monitor: Pilot" by hand in Sonarr.

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
watched state by reading the Plex library **and the play history** (see [`plex.md`](plex.md)): for each
show managed by Sonarr, it unions the episodes still in the library (`allLeaves`, `viewCount>0`) with the
**play history** (a single global `/status/sessions/history/all` sweep correlated by `grandparentTitle`,
which survives file deletion **and a remove/re-add** — a per-show `metadataItemID` query would miss the
re-added show's history, see [`plex.md`](plex.md)), records them with their real date (feeds the grace
periods) and applies the window for the **last watched**. Reading the history is what keeps the
back-catalog jump correct when later seasons were watched but their files were since deleted (otherwise
the anchor would fall back to an earlier still-present episode and re-download it). It covers the cases the live mode doesn't see: shows already started at
install, episodes marked by hand in Plex, and viewing with monitorr off. **Retroactive (back-catalog):**
because it applies the window for the last watched, a previously-watched show **added later** (e.g. when a
new season drops) **jumps straight to your last watched episode** — it monitors/searches the GET window
ahead of that point and never re-downloads the series from the pilot. The detection is from Plex's reported
watch state, not from disk/Sonarr (see the pitfall in [`plex.md`](plex.md)); if Plex reports **no** viewing
the show is **normalized to pilot** instead. It also **normalizes
to pilot** the managed shows with no viewing (see [Normalize to Pilot](#normalize-to-pilot)). It
inherits the engine's dry-run.

### Incremental vs full (avoid re-scraping Plex)

The watch state is **already persisted** in SQLite (`episode_watch` / `series_activity`), kept fresh
in real time by the poller/webhook. The periodic sync is therefore **incremental**: it sweeps only
the play history **newer than a watermark** (`history_watermark` = the newest `viewedAt` seen, minus
a small overlap) and re-scans `allLeaves` **only for the shows with new plays** — skipping the
per-show library scan of the whole catalogue (the expensive part on big libraries). The history
endpoint already returns rows `viewedAt:desc`, so the sweep stops at the first play older than the
watermark.

A **full** reconciliation (scan every managed show) is entered only **by cause**, never by a routine
timer:

- **Connection of both dependencies** (Plex *and* Sonarr configured, or a Plex server change) — the
  "first full"; the local DB may be stale relative to the freshly connected source.
- **Manual "Sync now"** button — the deterministic "reconcile everything now" affordance.
- **No watermark yet** (first ready) or the **history endpoint unavailable** (an empty delta from a
  dead source must not be mistaken for "nothing new" → promote that cycle to full).
- **Rolling safety floor**: `full_sync_interval` (default **30 days**) since the last full. It's a
  *rolling* comparison (`now − last_full_sync`), checked on each cycle **and at startup**, so a
  downtime that crosses the floor triggers a full on the next boot — the window is never "missed".

`last_sync` records the `mode` (`full`/`incremental`) for the UI. Cost a full incurs (per-show
`allLeaves` + full history pagination) is the cost incremental avoids.

**Anchor floor (regression guard).** The sync derives its anchor from the live Plex read of the
cycle (`allLeaves` + the history delta), which can **under-report the furthest watch on an
incremental cycle**: if that episode's file was already trimmed **and** its play predates the
watermark it is in neither source, so the live anchor falls back to the furthest *on-disk* watch.
Left unchecked the window slides backward and re-downloads an already-seen series N episodes per
cycle (each re-download re-appears in `allLeaves`, advancing the anchor again). To prevent this,
`apply_window` **floors the anchor at the furthest episode ever recorded as watched** in the
persisted store (`episode_watch` — monotonic, filled with the complete history on every full sync),
clamped to an episode Sonarr lists. The floor is enforced in `engine/window.py` for **every** path
(sync, poller, webhook), so the persisted watch state — not the volatile on-disk state — is the
authority for where the window anchors. The poller/webhook also pre-compute this max before calling
`apply_window`; the central floor makes it robust regardless of the caller.

Both modes also **normalize** unwatched shows and **re-search Sonarr's Wanted/Missing**: every
managed show's episodes that are still **monitored, already aired and without a file** are searched
again (`EpisodeSearch`), **excluding** the ones already downloading (present in Sonarr's `queue`).
This recovers from a transient indexer outage where the search at monitor-time found nothing. It is
governed by the same `search_on_get` flag (ON by default) and respects per-series `enabled` and
dry-run.

## Configuration

- **Single global**: one policy (GET, KEEP, Always-Have, grace, auto-normalize) for all
  shows + the dry-run switch.
- **Per-series override**: per show you can (a) **enable/disable** monitorr and (b) edit a **full
  policy** that replaces the global one for that show, both from the Web UI (the `/series/{tvdb_id}`
  page). The override is stored as a **complete snapshot** in `series_override.policy_json` and
  `effective_policy()` merges it over the global (full replaces base), so later global changes don't
  leak into an overridden show until you **Reset to global** (clears `policy_json`, keeps the
  enabled flag). Toggling enable/disable **preserves** the policy override. Scope = the 11 `Policy`
  fields only; `dry_run`, the watched threshold and the user filter stay global.

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
- **Season-pack upgrade on a fully-monitored season**: prevented because watched/kept episodes
  are **unmonitored** (file kept) once they leave the GET window — the season never reaches the
  all-episodes-monitored state Sonarr requires to accept a season pack, so it can't re-download
  ~hundreds of GB as an "upgrade" of episodes that won't be re-watched. *Exception*: a season fully
  covered by an Always-Have pattern (`S*`/`S02`) keeps all its episodes monitored (so they can be
  upgraded), which can let Sonarr grab a season-pack upgrade — accepted, since every episode there
  is one the user marked to keep forever. The season-pack guard still cancels any queued episode
  that is **not** in GET ∪ KEEP ∪ Always-Have.
- **Aired vs absolute order (anime)**: the MVP always uses aired order `(season, episode)`.
  Known limitation: anime with absolute numbering may not order as expected.
- **Multi-user**: out of v1 (a single consumer is assumed). There is an **optional user filter**
  in Settings to limit which playbacks trigger actions; the poller/webhook match by user title and
  the **sync** applies it too, filtering the play history by **accountID** and skipping `allLeaves`
  when the owner isn't included (see [`plex.md`](plex.md) → "User filter in the sync").
- **Resilient sync**: a show with an error (404, timeout, nonexistent episode) is logged and
  skipped; it doesn't abort the rest of the sync nor leave `last_sync` unupdated.
- **Session without TVDB**: it's warned once and cached so it isn't re-resolved (or re-warned) on each
  poll; it's retried when that show is played again.
