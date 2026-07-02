---
name: monitorr-failure-archaeology
description: The complete monitorr incident catalog — every historical bug with commit hash, symptom, cause→effect mechanism, fix location, regression tests, and lesson. Use when asking "has this happened before", "why does this invariant exist", "what broke in vX.Y", before changing anchor/sync/window/grace code, or when a re-download / wrong-delete / oscillation smells like a known incident. NOT for diagnosing a CURRENT symptom → use monitorr-debugging-playbook. NOT for the invariants themselves → use monitorr-architecture-contract.
---

# monitorr failure archaeology — the incident catalog

This skill is the ONLY home of full incident stories. Every other skill cites a hash plus one
line and points here. Read the relevant incident before touching anchor, sync, window, or grace
code — the meta-pattern section explains why that is mandatory (see also monitorr-change-control,
discipline rule "anchor changes need adversarial review").

All facts verified against the repo at v1.6.1 (commit c580f65, 2026-07-02). Every hash below is
a real commit: verify with `git show <hash> --stat`. Every test name exists in `tests/`: verify
with `grep -rn "def <name>" tests/`.

## Glossary (terms used throughout)

| Term | Meaning |
|---|---|
| anchor | The episode the window is computed around — the furthest-watched episode |
| GET | The N episodes/seasons AHEAD of the anchor, kept monitored (and searched) in Sonarr |
| KEEP | The N episodes/seasons BEHIND the anchor whose files are retained on disk |
| Always-Have | Per-show patterns (e.g. `S01E01`) protecting key episodes from every deletion |
| grace | Deferred time-based deletion sweeps: watched / unwatched / dormant / completed |
| dry-run | Master switch (ON by default) that turns all Sonarr writes into recorded previews |
| watermark | Newest Plex-history `viewedAt` scanned; incremental syncs only sweep newer plays |
| full / incremental sync | Full = rescan every managed show; incremental = only shows with new plays |
| season cascade | Sonarr propagates a season-level `monitored` flag down to that season's episodes |
| ratingKey | Plex's per-item id; NOT stable — a removed+re-added series gets a new one |
| allLeaves | Plex endpoint listing a show's episodes currently in the library (on disk only) |
| scrobble | Plex `media.scrobble` webhook event, fired at ~90% viewed |
| season pack | A single torrent containing a whole season; one queue row per episode, one torrent |
| PMS | Plex Media Server |
| *arr | The Sonarr/Radarr app family |

## Quick index

| # | Incident | Fix commit | Release |
|---|---|---|---|
| INC-01 | Startup crash on bind-mounted /config | ee94c28 | v1.1.0 |
| INC-02 | Season-pack "upgrades" (hundreds of GB) | dd889ee | v1.2.0 |
| INC-03 | Grace deleted inside the KEEP window | 20d578e | v1.2.1 |
| INC-04 | Back-catalog missed deleted-file viewing (+ live-anchor sibling 351bc8a) | 4245d21 | v1.4.1 |
| INC-05 | Re-added series re-imported old episodes | 5ce7fab | v1.4.2 |
| INC-06 | Watched series re-downloaded itself (sync anchor regression) | e572eff | v1.5.1 |
| INC-07 | Watched episodes re-downloaded after the season cascade | 31fa9d5 | v1.6.1 |
| INC-08 | Abandoned shows oscillated download/delete | c72357d | v1.6.1 |
| INC-09 | First-full sync skipped on single-server link | 76b97c3 | v1.6.1 |
| INC-10 | Stale watermark after server switch; blind sweep advanced state | beb757c | v1.6.1 |
| INC-11 | KEEP floor dissolved on numbering mismatch | 9a189ef | v1.6.1 |
| INC-12 | Search hygiene + input hardening (4 small bugs) | 99ba5fc | v1.6.1 |

Full mechanism narratives also live in `CHANGELOG.md` (the project's memory — its entries are
written cause → mechanism → fix by rule; see monitorr-change-control).

---

## INC-01 — Startup crash on bind-mounted /config

- **Affected**: v1.0.0–v1.0.1. **Fix**: ee94c28 → v1.1.0.
- **Symptom**: container crashed at startup with
  `sqlite3.OperationalError: unable to open database file` when `/config` was a bind mount.
- **Mechanism**: the image baked in a fixed non-root user; a bind mount keeps the HOST's
  ownership (and the daemon creates a missing source dir as root), so that fixed UID could not
  write the mount → SQLite could not create/open its DB file → crash before the app served.
- **The fix**: start as root; an entrypoint remaps the `app` user to `PUID`/`PGID` (default
  1000:1000), `chown -R`s `/config`, then drops privileges via `gosu`. Lives in
  `docker-entrypoint.sh:1-30` (whole file) and the `Dockerfile` USER/ENTRYPOINT wiring.
- **Regression tests**: none automated (Docker runtime behavior; not reachable from pytest).
  The entrypoint's comments are the specification; verify by reading `docker-entrypoint.sh`.
- **Lesson**: fixed non-root UIDs in images break on bind mounts — remap at runtime, then drop.

## INC-02 — Season-pack "upgrades" (hundreds of GB)

- **Affected**: v1.0.0–v1.1.0. **Fix**: dd889ee → v1.2.0.
- **Symptom**: Sonarr grabbed full season packs of shows being watched normally, re-downloading
  ("upgrading") hundreds of GB of episodes the user had already seen.
- **Mechanism**: advancing through a season left every episode both on disk AND monitored
  (monitoring was coupled to retention). An all-episodes-monitored season is exactly the state
  Sonarr's upgrade logic needs to grab a season pack → whole-season "upgrade" grabs of content
  that would never be re-watched.
- **The fix**: unmonitoring was decoupled from deletion — monitoring intent tracks the GET
  window only, independent of file retention, so kept/watched episodes are unmonitored WHILE
  keeping their files and a season of merely-kept episodes never reaches all-monitored. Lives in
  `src/monitorr/engine/window.py:251-261` (the unmonitor batch and its comment). The invariant
  was refined in v1.6.0 (4d71409) so Always-Have episodes stay monitored — that release's
  historical form was `monitored ≡ GET window ∪ Always-Have`; the armed gate was added later
  (c72357d, INC-08), so today's canonical form is
  `monitored ≡ (GET window if armed else ∅) ∪ Always-Have` (`window.py:214`,
  monitorr-window-engine-reference). Invariant text and why: monitorr-architecture-contract.
- **Regression tests**: the original test (`test_unmonitors_watched_kept_episodes_without_deleting`)
  was renamed by 4d71409; current locks are
  `test_always_have_keeps_every_episode_monitored_without_deleting` (`tests/test_window.py:230`)
  and `test_always_have_pilot_stays_monitored_while_others_unmonitored`
  (`tests/test_window.py:250`).
- **Lesson**: in Sonarr, `monitored` is a statement of fetch/upgrade INTENT, not of retention —
  conflating the two hands Sonarr a license to re-download.

## INC-03 — Grace deleted inside the KEEP window

- **Affected**: v1.0.0–v1.2.0. **Fix**: 20d578e → v1.2.1.
- **Symptom**: with "keep 1 season behind", episodes of the season being ACTIVELY watched were
  deleted once they aged past `grace_watched_days`.
- **Mechanism**: the `watched`/`unwatched` grace sweeps deleted purely by age and ignored KEEP
  entirely → any watched episode older than the grace days was removed, even ones the KEEP
  window guaranteed on disk → the spatial retention promise was silently void.
- **The fix**: KEEP became a retention FLOOR for the ongoing trims, via a shared helper
  `keep_protected_keys` (`src/monitorr/engine/window.py:73-83`) consumed by the sweep
  (`src/monitorr/engine/grace.py:80-95`, then checked at `grace.py:109` and `grace.py:124`).
  The bulk purges (`completed`/`dormant`) intentionally still ignore KEEP; Always-Have remains
  the only protection over every deletion path.
- **Regression tests**: `test_watched_respects_keep_seasons_floor` (`tests/test_grace.py:167`),
  `test_watched_respects_keep_episodes_floor` (`tests/test_grace.py:197`),
  `test_unwatched_respects_keep_floor` (`tests/test_grace.py:252`).
- **Lesson**: two deletion mechanisms (spatial window, temporal grace) must share one protection
  computation, or one will delete what the other guarantees.

## INC-04 — Back-catalog missed deleted-file viewing

- **Affected**: v1.0.0–v1.4.0. **Fix**: 4245d21 → v1.4.1 (with sibling live-path fix 351bc8a in
  the same release).
- **Symptom**: re-adding an already-watched series re-downloaded an early WHOLE SEASON instead
  of jumping the window to the user's true last-watched episode.
- **Mechanism**: watch detection during sync used only `allLeaves`, which reflects the CURRENT
  library — episodes whose files had been deleted disappeared from it, so their viewing was
  invisible → the anchor fell back to the highest still-on-disk watched episode (e.g. mid-S1)
  → the window slid backward and re-downloaded already-seen content. Separately, a GET search
  could make Sonarr grab a full season pack and import every episode of the season.
- **The fix**: the sync also reads Plex's play history (`/status/sessions/history/all`), which
  persists independently of the files, and unions it with the library state to anchor on the
  true last-watched (`src/monitorr/plex/client.py:350` `get_watch_history_by_show`; merge and
  anchor choice in `src/monitorr/sync.py:205-230`). It also added the season-pack queue guard:
  cancel from Sonarr's queue every queued episode outside GET ∪ KEEP-protected ∪ Always-Have
  with `removeFromClient=false` (`src/monitorr/engine/window.py:263-282`). Sibling fix 351bc8a
  made the LIVE path (poller/webhook) anchor on the furthest recorded watch instead of the
  episode just played, so a re-watch or gap-fill never drags the window back
  (`src/monitorr/plex/poller.py:36-46`).
- **Regression tests**: `test_sync_anchors_on_history_when_files_deleted`
  (`tests/test_sync.py:355`), `test_cancels_season_pack_surplus_from_queue`
  (`tests/test_window.py:275`), `test_get_watch_history_keeps_most_recent_view`
  (`tests/test_plex_history.py:94`; its sibling `..._parses_and_filters` was renamed by 5ce7fab).
  For 351bc8a: `test_process_watch_anchors_on_furthest_watched` (`tests/test_poller.py:90`),
  `test_process_watch_advances_to_new_max` (`tests/test_poller.py:106`).
- **Lesson**: `allLeaves` is volatile (files gone = viewing gone); play history persists. Never
  derive the anchor from on-disk state alone. NOTE: the queue guard added here is also the
  mechanism behind the season-pack grab/cancel oscillation trade-off — see
  monitorr-season-pack-campaign.

## INC-05 — Re-added series re-imported old episodes

- **Affected**: v1.4.1 only (INTRODUCED by 4245d21). **Fix**: 5ce7fab → v1.4.2.
- **Symptom**: despite the v1.4.1 history fix, re-adding an already-watched series STILL
  re-downloaded old episodes.
- **Mechanism**: 4245d21's history query was per-show, filtered by `metadataItemID={ratingKey}`.
  Plex assigns a series a NEW ratingKey when it is removed and re-added, orphaning the old
  history under the previous ids → the filtered query returned NOTHING → the anchor again fell
  back to the furthest still-on-disk episode and the next, already-watched one was searched and
  downloaded.
- **The fix**: the sync sweeps the GLOBAL play history ONCE per cycle and correlates each show
  by normalized `grandparentTitle`, which is stable across re-adds
  (`src/monitorr/plex/client.py:313-320` `normalize_title`, `client.py:350`
  `get_watch_history_by_show`; correlation in `src/monitorr/sync.py:189` and `sync.py:205`).
  Bonus: one history call per sync instead of one per show.
- **Regression tests**: `test_get_watch_history_groups_by_title` (`tests/test_plex_history.py:10`),
  `test_get_watch_history_recovers_readded_show_by_title` (`tests/test_plex_history.py:56`).
- **Lesson**: ratingKeys are not stable identifiers; titles (same-server, normalized) are the
  durable join key for history. Also: fixes spawn fixes — this bug was born in the previous fix.

## INC-06 — Watched series re-downloaded itself (sync anchor regression)

- **Affected**: v1.5.0 only (INTRODUCED by c315cda, the incremental-sync feature).
  **Fix**: e572eff → v1.5.1. The most instructive incident in the catalog.
- **Symptom**: a fully-watched series re-downloaded itself, N episodes per sync cycle, growing
  without user action.
- **Mechanism**: the v1.5.0 incremental sync derived its anchor only from the CYCLE'S LIVE READ
  (`allLeaves` on disk + the history delta since the watermark), ignoring the persisted watch
  store. An episode whose file had been trimmed AND whose play predated the watermark was in
  NEITHER source → the anchor fell back to the furthest on-disk watch → the window slid backward
  and re-monitored/searched already-watched back-catalog. SELF-AMPLIFYING: each wrong
  re-download re-entered `allLeaves`, re-advanced the anchor, and armed the next backward slide.
- **The fix**: `apply_window` FLOORS the anchor at the furthest episode ever recorded as watched
  in the persisted store (`episode_watch`), clamped to episodes Sonarr lists, enforced INSIDE
  the window so every caller (sync, poller, webhook) is protected
  (`src/monitorr/engine/window.py:121-142`). A one-time migration (migration 4 of 4 — list
  index 3 in `MIGRATIONS`, schema version 4) drops `last_full_sync` to force one full
  reconciliation after upgrading
  (`src/monitorr/db.py:54-58`). Canonical floor formula: monitorr-window-engine-reference.
- **Regression tests**: `test_anchor_floored_at_persisted_furthest_watch`
  (`tests/test_window.py:578`), `test_anchor_floor_clamps_to_episode_sonarr_lists`
  (`tests/test_window.py:594`), `test_anchor_floor_no_op_when_anchor_at_or_above_recorded`
  (`tests/test_window.py:610`), `test_incremental_sync_does_not_regress_below_recorded_max`
  (`tests/test_sync.py:812`), `test_migration_clears_last_full_sync_to_force_full`
  (`tests/test_sync.py:907`).
- **Lesson**: the persisted, MONOTONIC watch store is the only authority for where the window
  anchors; any live read under-reports. Enforce the invariant at the choke point, not per caller.

## INC-07 — Watched episodes re-downloaded after the season cascade

- **Affected**: releases through v1.6.0 (season-level monitoring enforcement dates to 583f12d,
  pre-v1.0.0). **Fix**: 31fa9d5 → v1.6.1.
- **Symptom**: after an anchor jump in seasons mode (e.g. back-catalog detection), watched
  episodes re-downloaded even though the anchor was correct.
- **Mechanism**: `apply_window` fetched the episode snapshot, THEN wrote season `monitored`
  flags. Sonarr CASCADES a season flag to that season's episodes, invalidating the snapshot →
  the unmonitor batch, computed from stale `monitored` values, skipped watched episodes the
  cascade had just re-monitored → the sync's Wanted/Missing re-search downloaded them. A
  write-after-read hazard: monitorr's own write changed the state it had already read.
- **The fix**: the anchor is resolved BEFORE any write (an anchor Sonarr doesn't list aborts
  with zero half-applied season flags), and the episode list is RE-FETCHED whenever the season
  flags actually changed, so every subsequent set is computed from post-cascade flags
  (`src/monitorr/engine/window.py:154-179`; `set_seasons_monitored` returns whether it wrote,
  `src/monitorr/engine/actions.py:28-30`).
- **Regression tests**: `test_refetches_episodes_after_season_cascade`
  (`tests/test_window.py:502`), `test_missing_anchor_aborts_before_touching_seasons`
  (`tests/test_window.py:559`).
- **Lesson**: any Sonarr season-level write invalidates a previously fetched episode snapshot —
  order writes cascade-first, then re-read before deciding anything episode-level.

## INC-08 — Abandoned shows oscillated download/delete

- **Affected**: releases through v1.6.0. **Fix**: c72357d → v1.6.1.
- **Symptom**: a show nobody watched anymore alternated forever: full sync re-downloaded its GET
  window, the next grace sweep deleted it, the next full sync re-downloaded it again.
- **Mechanism**: past `dormant_days`/`grace_unwatched_days` of inactivity the graces delete what
  GET downloads, but every full sync unconditionally re-armed the GET window (monitor + search)
  → an endless download/delete oscillation re-triggered by every full sync, wasting bandwidth
  and indexer hits.
- **The fix**: the GET arm is gated by the SAME inactivity clock the graces use — `_is_armed`
  (`src/monitorr/engine/window.py:38-50`, applied at `window.py:161` and in `_desired_seasons`,
  `window.py:23-35`). Any live watch records activity first, so resuming the show re-arms GET
  naturally; `grace_completed_days` intentionally does NOT gate, so a caught-up show re-arms
  when a new season airs. Canonical `_is_armed` formula: monitorr-window-engine-reference.
- **Regression tests**: `test_get_window_gated_when_inactive_past_grace`
  (`tests/test_window.py:430`), `test_get_window_rearms_with_fresh_activity`
  (`tests/test_window.py:456`), `test_gated_window_turns_seasons_off_in_seasons_mode`
  (`tests/test_window.py:474`).
- **Lesson**: when one subsystem acquires and another releases, they must share the same clock,
  or the pair becomes an oscillator.

## INC-09 — First-full sync skipped on single-server link

- **Affected**: releases through v1.6.0. **Fix**: 76b97c3 → v1.6.1.
- **Symptom**: linking Plex when exactly one server was discovered left the app idle — no
  initial reconciliation until the next periodic cycle (up to hours later).
- **Mechanism**: server linking has two paths; the multi-server chooser fired the
  "first connection of both deps → full sync" trigger, but the auto-select path (exactly one
  server) stored the server and returned without firing it → the connection-of-both-deps promise
  silently deferred to the periodic timer.
- **The fix**: the single-server path now calls the same trigger as the chooser
  (`src/monitorr/web/routes.py:355-359`, calling `_maybe_trigger_full_sync`,
  `routes.py:121-130`).
- **Regression tests**: `test_single_server_link_triggers_full_sync` (`tests/test_app.py:215`).
- **Lesson**: fast paths and slow paths of the same operation must converge on the same
  side-effects; a convenience shortcut that skips one is a latent bug.

## INC-10 — Stale watermark after server switch; blind sweep advanced state

- **Affected**: v1.5.0–v1.6.0 (the watermark exists since c315cda). **Fix**: beb757c → v1.6.1.
- **Symptom**: after relinking a DIFFERENT PMS, new plays were never detected (incremental
  sweeps stayed empty forever). Separately, a Plex-history outage during a full sync silently
  buried the outage's plays.
- **Mechanism**: two related state bugs. (1) `history_watermark`/`last_full_sync` are
  per-server state but survived relinking a different PMS — a carried-over (possibly future)
  watermark made incremental sweeps skip the new server's plays forever. (2) A full sync whose
  history endpoint failed still stamped `watermark=now` + `last_full_sync` → the outage's plays
  fell below the incremental floor AND the promote-to-full degradation was disabled for a whole
  floor interval.
- **The fix**: the linked server's `clientIdentifier` is persisted and BOTH keys reset whenever
  it changes or on unlink (`src/monitorr/web/routes.py:72-87` `_store_server`;
  `constants.py:17` `PLEX_SERVER_ID`); and a blind sweep advances NOTHING — neither watermark
  nor `last_full_sync` — so cycles stay full until the history endpoint recovers
  (`src/monitorr/sync.py:126-131`).
- **Regression tests**: `test_unlink_clears_per_server_sync_state` (`tests/test_app.py:303`),
  `test_switching_servers_resets_per_server_sync_state` (`tests/test_app.py:324`),
  `test_failed_history_advances_neither_watermark_nor_full_stamp` (`tests/test_sync.py:762`).
- **Lesson**: name the scope of every piece of persisted state (this one is PER-SERVER) and
  reset it when the scope's identity changes; never advance a progress marker on a failed read.

## INC-11 — KEEP floor dissolved on numbering mismatch

- **Affected**: v1.2.1–v1.6.0 (the floor exists since 20d578e). **Fix**: 9a189ef → v1.6.1.
- **Symptom**: the grace sweep trimmed the WHOLE watched tail of a show — episodes the KEEP
  window guaranteed — when Plex and Sonarr disagreed on episode numbering.
- **Mechanism**: the sweep's KEEP-floor anchor was `max(watches)` UNCLAMPED. A recorded watch
  Sonarr doesn't list (numbering mismatch, removed episode) made the anchor lookup fail →
  `kept_keys` stayed empty → the retention floor of INC-03 silently dissolved and the age-based
  trims deleted everything old enough.
- **The fix**: the sweep clamps its anchor to the furthest watched episode Sonarr actually
  lists — the same clamp as `apply_window`'s anchor floor
  (`src/monitorr/engine/grace.py:84-95`).
- **Regression tests**: `test_keep_floor_clamps_anchor_to_episodes_sonarr_lists`
  (`tests/test_grace.py:222`).
- **Lesson**: a protection that can silently evaluate to "protect nothing" on bad input is not a
  protection; clamp inputs so degradation picks the nearest valid state, never the empty one.

## INC-12 — Search hygiene + input hardening (4 small bugs)

- **Affected**: releases through v1.6.0. **Fix**: 99ba5fc → v1.6.1.
- **Symptom**: (a) weekly shows fired a guaranteed-empty indexer search for the unaired next
  episode on every trigger; (b) each missing window episode got TWO `EpisodeSearch` commands per
  sync; (c) a non-numeric/negative grace value in the policy form returned HTTP 500 (or made the
  grace fire unconditionally); (d) zero/negative poller/sweep intervals hot-looped the process.
- **Mechanism and fixes** (one commit, four hardenings):
  - Unaired GET episodes are monitored but NOT searched — `search_ahead` requires
    `not e.has_file and e.has_aired()` (`src/monitorr/engine/window.py:192-196`); Sonarr grabs
    them on air via RSS.
  - The sync's Wanted/Missing re-search skips ids a window already searched in the same cycle —
    windows return their searched ids (`window.py:99-100`, `window.py:217-220`) and the resync
    pass receives them (`src/monitorr/sync.py:118-123`).
  - The policy form parses grace fields with `_opt_int`: empty/non-numeric/NEGATIVE → None
    (grace disabled), never a 500 — negative would fire the grace unconditionally, deletions from
    a typo (`src/monitorr/web/routes.py:229-269`); the watched threshold is clamped to
    [0.05, 1.0] (`routes.py:305-308`).
  - Poller/sweep intervals validate `ge=1` at startup; sync intervals `ge=0` (0 = documented
    "disabled") (`src/monitorr/config.py:23-30`). Full config surface: monitorr-config-and-flags.
- **Regression tests**: `test_unaired_ahead_episodes_are_monitored_but_not_searched`
  (`tests/test_window.py:405`), `test_resync_pass_skips_episodes_the_window_just_searched`
  (`tests/test_sync.py:187`), `test_policy_form_tolerates_bad_numbers_and_clamps_threshold`
  (`tests/test_app.py:271`), `test_settings_reject_hot_loop_intervals` (`tests/test_app.py:292`).
- **Lesson**: hygiene bugs are cheap individually and expensive in aggregate (indexer bans,
  hot loops, deletion-by-typo); harden inputs where they enter, not where they explode.

---

## Synthesis: the meta-pattern

**Almost every serious incident is one bug wearing different clothes: the anchor slid backward,
so already-watched content re-downloaded.** Members of the family: INC-04, its sibling 351bc8a,
INC-05, INC-06, INC-07 — and INC-11 is the deletion-side mirror (the protection anchored on a
watch that "wasn't there").

**Root cause, every time: deriving the anchor from VOLATILE state** instead of the persisted
monotonic watch store (`episode_watch` in SQLite):

| Volatile source trusted | Incident it caused |
|---|---|
| `allLeaves` (on-disk library only) | INC-04, INC-06 |
| ratingKey (changes on re-add) | INC-05 |
| The episode just played (not the max) | 351bc8a (live path) |
| Live-cycle read (history delta + disk) | INC-06 |
| Pre-cascade Sonarr episode snapshot | INC-07 |
| Unclamped `max(watches)` | INC-11 |

**The loop is SELF-AMPLIFYING**: each wrong re-download re-enters `allLeaves` and re-advances
the anchor, so the system converges on repeating the mistake instead of recovering (INC-06 is
the purest example: N episodes per cycle, unbounded). This is why anchor bugs are the worst
class in this codebase — they do not stay small.

**The invariant had to be re-asserted on FOUR separate paths before it held**:

| Path | Commit | What it asserted |
|---|---|---|
| Live (poller/webhook) | 351bc8a | Anchor on the furthest recorded watch, not the episode played |
| History (sync detection) | 4245d21 → 5ce7fab | Union persistent play history; correlate by title |
| Sync (window entry) | e572eff | Floor the anchor at the persisted furthest watch, in `apply_window` |
| Grace sweep | 9a189ef | Clamp the KEEP-floor anchor to episodes Sonarr lists |

An invariant enforced in one place per caller WILL be missed by the next caller; e572eff finally
moved enforcement inside `apply_window` (`window.py:121-142`) so every path inherits it. The
invariant statements and their rationale live in monitorr-architecture-contract.

**Fixes spawn fixes**: 4245d21 (per-ratingKey history) directly caused the v1.4.2 bug fixed by
5ce7fab; c315cda (incremental sync) directly caused the v1.5.1 regression fixed by e572eff. Two
of the worst incidents were born inside fixes/features touching the same code. This is why
monitorr-change-control's discipline rule (a) exists: any change touching anchor/sync/window
code must be argued against THIS catalog and add a regression test before merging.

## Riskiest subsystems, ranked by fix count (as of v1.6.1, 2026-07-02)

Counting the fix commits above by the subsystem whose logic was wrong:

| Rank | Subsystem | Fixes | Incidents |
|---|---|---|---|
| 1 | sync + window anchor cluster (`sync.py`, `engine/window.py`, `plex/client.py` history) | 7 | INC-04 (+351bc8a), INC-05, INC-06, INC-07, INC-08, INC-10, INC-12(a,b) |
| 2 | grace sweep (`engine/grace.py`) | 2 | INC-03, INC-11 |
| 3 | web/link flow (`web/routes.py`) | 2 | INC-09, INC-12(c) |
| 4 | packaging/runtime (`Dockerfile`, entrypoint) | 1 | INC-01 |
| 5 | config validation (`config.py`) | 1 | INC-12(d) |

The sync/window/plex-client cluster is where anchor state flows; treat any edit there as
high-risk and apply change-control rule (a). Re-derive this ranking after new releases:
`git log --oneline --grep='^fix' -- src/monitorr/`.

## The Sonarr season-cascade write-after-read hazard (INC-07, 31fa9d5)

Generalized: Sonarr's series PUT with changed season `monitored` flags CASCADES to every episode
of those seasons. Any episode list fetched BEFORE such a write is stale AFTER it. Pattern to
follow (as `apply_window` now does, `window.py:154-179`): (1) resolve everything you need from
the snapshot and validate preconditions BEFORE the first write; (2) after a season-flag write
that reports a change, RE-FETCH episodes; (3) compute every episode-level set (unmonitor,
delete, cancel) from the post-cascade snapshot only. If you add any new season-level write,
this hazard applies to it. Endpoint details: monitorr-plex-sonarr-reference.

## Why none of the delete-side bugs caused real data loss: dry-run by default

The master dry-run switch defaults to ON (`get_dry_run()` default True; every Sonarr write
routes through `src/monitorr/engine/actions.py` and is a recorded no-op preview under dry-run).
INC-03 and INC-11 — both of which deleted files the user was promised to keep — shipped in
releases, but a fresh install previews deletions instead of performing them until the user
explicitly opts in, so the blast radius was previews plus opted-in installs, not silent fleet-
wide data loss. This is a designed property, not luck: keep dry-run the default, keep ALL writes
routed through `engine/actions.py`, and treat any change that adds a write path bypassing it as
an incident waiting to happen. The riskiest default in the system is `grace_completed_days=30`
ENABLED — harmless under dry-run, a 30-day purge timer without it (see monitorr-config-and-flags).

## History: the manual "Normalize to Pilot" button (added, then removed)

Normalize to Pilot = reset a show with no recorded viewing to its pilot-anchored state. A manual
UI button for it existed briefly in the MVP era (introduced around f59a4e5/961a500, 2026-05-29 —
961a500 added "forzar Pilot"; d8e9e8f made it delete what it unmonitored) and was REMOVED in
9468870 ("quitar el botón manual 'Normalizar a Pilot' (solo cron)") once 14f081e made
normalization AUTOMATIC on every sync. Today there is no manual route or button
(`grep -n normalize src/monitorr/web/routes.py` finds only the `auto_normalize` policy flag);
the automatic path is `actions.normalize_to_pilot` (`src/monitorr/engine/actions.py:92`) called
from the sync. `.claude/architecture.md` and `.claude/sonarr.md` were stale here until
2026-07-02 (still describing the manual/opt-in button) — fixed, both now describe the automatic
normalize; retired errata E1/E3 in `monitorr-docs-and-writing`. The retirement rationale is the
ledger exemplar in `monitorr-research-methodology` §4.

## When NOT to use this skill

- You are debugging a CURRENT symptom and need a runbook (what to check, in what order) →
  `monitorr-debugging-playbook`. Come back here when the symptom matches an incident and you
  need its mechanism.
- You need the invariants themselves (statement, rationale, what must not change) →
  `monitorr-architecture-contract`.
- You need the canonical window/grace formulas → `monitorr-window-engine-reference`.
- You are working the season-pack grab/cancel oscillation → `monitorr-season-pack-campaign`
  (this catalog only records where the guard came from, INC-04).
- Doc/code disagreements, including the retired E1/E3 rows referenced above →
  `monitorr-docs-and-writing` (errata owner).
- You are about to CHANGE anchor/sync/window code → read the synthesis here, then follow
  `monitorr-change-control` (adversarial review + regression test required).

## Provenance and maintenance

Verified against v1.6.1 (c580f65) on 2026-07-02. Re-verification one-liners:

| Fact | Command |
|---|---|
| Any incident's commit exists and touched what's claimed | `git show <hash> --stat` (e.g. `git show e572eff --stat`) |
| Fix commit → release mapping | `git log --oneline` (release commits: `chore: release vX.Y.Z`) and `CHANGELOG.md` |
| A regression test still exists | `grep -rn "def test_anchor_floored_at_persisted_furthest_watch" tests/` (same pattern per name) |
| Anchor floor still in `apply_window` | `grep -n "Anchor floor" src/monitorr/engine/window.py` |
| Cascade re-fetch still present | `grep -n "cascade" src/monitorr/engine/window.py` |
| `_is_armed` gate still present | `grep -n "_is_armed" src/monitorr/engine/window.py` |
| KEEP-floor clamp in the sweep | `grep -n "real_keys" src/monitorr/engine/grace.py` |
| Per-server reset + blind-sweep rule | `grep -n "delete_setting" src/monitorr/web/routes.py; grep -n "blind" src/monitorr/sync.py` |
| Migration 4 still drops last_full_sync | `grep -n "last_full_sync" src/monitorr/db.py` |
| No manual normalize route | `grep -n "normalize" src/monitorr/web/routes.py` |
| Test renames (INC-02, INC-04) | `git show 4d71409 -- tests/test_window.py \| grep "def test_"` and `git show 5ce7fab -- tests/test_plex_history.py \| grep "def test_"` |
| Fix-count ranking still holds | `git log --oneline --grep='^fix' -- src/monitorr/` |

File:line references drift with edits — trust the grep commands over the frozen line numbers.
When a NEW incident is fixed, add its entry here (same fixed format) in the same change as the
fix, per monitorr-change-control; the CHANGELOG entry must explain the mechanism (discipline
rule c).
