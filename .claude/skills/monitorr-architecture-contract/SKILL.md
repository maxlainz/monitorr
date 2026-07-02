---
name: monitorr-architecture-contract
description: The load-bearing invariants of monitorr — the contract every change must preserve. Read BEFORE modifying anchor/window/sync/grace/actions/db code, or when asking "is it safe to change X", "why does the code do Y", "can I derive the anchor from Plex", "why is unmonitor separate from delete". NOT for full incident stories → use monitorr-failure-archaeology; NOT for the window formulas → use monitorr-window-engine-reference; NOT for git/release steps → use monitorr-change-control.
---

# monitorr architecture contract

This skill states the load-bearing invariants of monitorr: what each one guarantees, WHY it
exists, which production incident proved it, where the code enforces it, what breaks if you
violate it, and the change-control gate. If a change you are planning touches any invariant
below, STOP and follow the gate before writing code.

All file:line references were verified against v1.6.1 (commit c580f65, 2026-07-02). Line numbers
drift; the "Provenance and maintenance" section gives a re-verification command per invariant.
Incident hashes are one-liners here — full stories live in `monitorr-failure-archaeology`.
Formulas are named here but stated canonically only in `monitorr-window-engine-reference`.

Terms (defined once): **anchor** = the furthest-watched episode, the point the window is computed
around. **GET** = the N episodes ahead of the anchor to monitor/download. **KEEP** = the N
episodes behind the anchor to retain on disk. **Always-Have** = per-policy patterns (e.g.
`S01E01`) protecting episodes from every deletion. **grace** = time-based deletion of inactive
content (watched/unwatched/dormant/completed). **dry-run** = master switch (default ON) that turns
every Sonarr write into a logged/recorded no-op. **watermark** = newest Plex history `viewedAt`
scanned, floor of the next incremental sync. **season cascade** = Sonarr propagating a season
`monitored` flag down to its episodes. **ratingKey** = Plex's per-item id (NOT stable across
remove/re-add). **allLeaves** = Plex's per-show episode listing (only what is currently in the
library). **PMS** = Plex Media Server.

## The meta-pattern (read this first)

Almost every incident in this project is the same failure: **the anchor slid backward → already-
watched content re-downloaded**, always caused by deriving the anchor from VOLATILE state
(on-disk `allLeaves`, a current `ratingKey`, a pre-cascade Sonarr snapshot) instead of the
persisted monotonic watch store. The loop is SELF-AMPLIFYING: each wrong re-download re-enters
`allLeaves` and re-advances the anchor, so the bug hides its own cause. The invariant had to be
re-asserted on four separate paths before it held (history 4245d21 → 5ce7fab, sync e572eff,
grace sweep 9a189ef). Invariants 1 and 2 below are the codification; treat them as one rule seen
from two sides.

## Change-control gate (applies to every invariant)

Per `monitorr-change-control` (discipline rules): any change touching anchor/sync/window code
must be argued adversarially against the full incident list in `monitorr-failure-archaeology`
(the re-download loop is self-amplifying) and must add a regression test before merging. Grace,
actions, and db.py migration changes get the same treatment. No behavior-changing merge routes
around `monitorr-change-control`.

## The invariants

### 1. Anchor monotonicity

**Statement**: The persisted watch store (`episode_watch` table) is the sole authority for the
window anchor; the anchor never slides backward; the floor is enforced centrally inside
`apply_window` so every caller (sync, poller, webhook) is protected.

- **Why**: Live Plex reads under-report the furthest watch whenever the file was already trimmed
  AND the play predates the incremental watermark. An anchor derived from live state falls back,
  the window re-monitors and re-downloads already-seen episodes, and each re-download re-enters
  the library and amplifies the loop.
- **Incident**: e572eff → v1.5.1 — v1.5.0 incremental sync derived the anchor from a live Plex
  read only; a trimmed file + pre-watermark play made the furthest watch invisible and a whole
  watched series re-downloaded itself. See `monitorr-failure-archaeology`.
- **Enforced at**: `src/monitorr/engine/window.py:121-142` (floor computed from
  `store.get_watches`, clamped to `real_keys`, re-anchors when `floor > (season, episode)`);
  `src/monitorr/store.py:101-118` (`record_watch` upserts `episode_watch`, activity advances by
  `MAX`); callers: `src/monitorr/plex/poller.py:35-46` (`process_watch` anchors on max recorded
  watch), `src/monitorr/sync.py:209-231`, `src/monitorr/web/routes.py:571` (webhook →
  `process_watch`). Formula ("Anchor floor"): `monitorr-window-engine-reference`.
- **If violated**: self-amplifying re-download of watched back-catalog (hundreds of GB observed).
- **Gate**: adversarial review against the incident list + regression test (discipline rule a).
  Never add a new `apply_window` caller that pre-computes its own anchor bypassing the floor.

### 2. Volatile state never drives destruction

**Statement**: `allLeaves`, ratingKeys, and pre-cascade Sonarr episode snapshots are volatile and
must never, alone, drive deletion or unmonitoring; only the persisted monotonic store may.

- **Why**: `allLeaves` only lists what is currently on disk (monitorr itself deletes files, so it
  shrinks); ratingKeys change on remove/re-add; Sonarr episode snapshots are invalidated by the
  season cascade. Each of these lied to a destructive path once.
- **Incidents**: 4245d21 → v1.4.1 (allLeaves-only detection missed deleted-file viewing; anchor
  fell back); 5ce7fab → v1.4.2 (`metadataItemID={ratingKey}` history query returned empty after a
  re-add); 31fa9d5 → v1.6.1 (pre-cascade snapshot made the unmonitor batch skip episodes the
  cascade had just re-monitored).
- **Enforced at**: `src/monitorr/sync.py:271-281` (`_merge_watches` unions allLeaves with play
  history — allLeaves is a contributor, never the authority); `src/monitorr/plex/client.py:350-376`
  (global history sweep, explicitly NOT scoped by ratingKey — docstring documents why);
  `src/monitorr/engine/window.py:121-131` (comment + floor); snapshot re-fetch: invariant 6.
- **If violated**: same re-download loop as invariant 1, or wrong episodes unmonitored/deleted.
- **Gate**: any new data source feeding a delete/unmonitor decision must be argued as
  non-volatile, or routed through `episode_watch` first.

### 3. Monitoring invariant (season-pack defense)

**Statement**: `monitored ≡ (GET window if armed else ∅) ∪ Always-Have`; everything else is
unmonitored WHILE KEEPING ITS FILE — unmonitoring is decoupled from deletion.

- **Why**: A season whose kept/watched episodes stay monitored eventually reaches the
  all-episodes-monitored state, which lets Sonarr grab a season-pack "upgrade" of episodes nobody
  will re-watch. Keeping only the GET window + Always-Have monitored removes the trigger while
  the KEEP files stay on disk.
- **Incident**: dd889ee → v1.2.0 — watched episodes stayed monitored+on-disk and Sonarr pulled
  season-pack upgrades worth hundreds of GB.
- **Enforced at**: `src/monitorr/engine/window.py:214` (`monitor_ids = (ahead_ids if armed else
  set()) | always_have_ids` — the authority set), `window.py:258-261` (unmonitor everything else
  still monitored, excluding just-deleted ids), `window.py:23-35` (`_desired_seasons`: seasons off
  unless armed seasons-mode GET); `src/monitorr/engine/actions.py:45-59` (`unmonitor_episodes`
  docstring codifies the decoupling). Since v1.6.0 Always-Have stays monitored so Sonarr can
  upgrade/re-fetch it.
- **If violated**: silent multi-hundred-GB season-pack grabs return.
- **Gate**: any change to what gets monitored must preserve the identity above; test against the
  all-monitored season state.

### 4. KEEP is a retention floor; Always-Have overrides everything

**Statement**: The watched/unwatched graces may only delete what already falls outside the KEEP
window (`keep_protected_keys`, anchor clamped to episodes Sonarr lists); the completed/dormant
bulk purges intentionally bypass KEEP; Always-Have overrides EVERY deletion path.

- **Why**: KEEP is the spatial guarantee ("N behind stays on disk"); graces are temporal trims.
  A grace that ignores KEEP deletes the episodes the user was promised. Conversely a
  finished/abandoned show should be purged whole — hence completed/dormant bypass. An unclamped
  anchor (a recorded watch Sonarr does not list) must pick the furthest real watched episode, not
  silently dissolve the whole floor.
- **Incidents**: 20d578e → v1.2.1 (grace_watched/unwatched ignored KEEP and deleted inside the
  window); 9a189ef → v1.6.1 (KEEP floor anchored on unclamped max(watches) dissolved on a
  numbering mismatch).
- **Enforced at**: `src/monitorr/engine/window.py:73-83` (`keep_protected_keys`, shared with the
  grace sweep); `src/monitorr/engine/grace.py:87-95` (floor computed with the same clamp as
  apply_window), `grace.py:109,124` (watched/unwatched skip `kept_keys`), `grace.py:54-77`
  (completed/dormant run BEFORE the floor is computed and ignore it, but check
  `matches_always_have`); Always-Have checks on every delete path: `window.py:229-231,244-246`,
  `grace.py:63-65,72-74,111-113,126-128`, `actions.py:112-119` (normalize). Grace order and
  caught-up formula: `monitorr-window-engine-reference`.
- **If violated**: episodes inside the promised KEEP window (or protected pilots) get deleted.
- **Gate**: any new deletion path MUST check `matches_always_have` and must explicitly decide
  (and document) whether it respects `keep_protected_keys` — bypassing is a design decision, not
  a default.

### 5. All Sonarr writes go through engine/actions.py, dry-run-gated

**Statement**: Every Sonarr write (monitor, unmonitor, season flags, search, delete, queue
cancel) routes through `engine/actions.py` and is a no-op when dry-run is on; dry-run defaults ON.

- **Why**: One choke point makes "nothing destructive happens until the user opts in" provable,
  and gives the UI its pending-deletion previews. This matters most because the default policy
  ships `grace_completed_days=30` ENABLED — the riskiest default the moment dry-run goes OFF.
- **Incident**: none — a designed guardrail that has prevented incidents; every destructive bug
  above was survivable in dry-run.
- **Enforced at**: `src/monitorr/engine/actions.py:17-25,28-42,45-59,62-70,73-89,150-188` (every
  function early-returns on `dry_run`; `delete_episode` records a deduped preview instead);
  `src/monitorr/engine/policy.py:64-66` (`get_dry_run` → `True` when unset); consumers fetch it
  once per cycle: `window.py:109`, `grace.py:138`, `sync.py:81`. Disabling dry-run clears
  previews: `policy.py:69-73`.
- **If violated** (a direct `sonarr.set_monitored`/`delete_*` call outside actions.py): dry-run
  stops being a guarantee; users testing the tool lose data.
- **Gate**: grep for direct `sonarr.` write calls outside `actions.py` in review (normalize's
  internal calls at `actions.py:131-147` are inside the module and behind its own dry-run check).

### 6. Season flags before episode flags; re-fetch after a season-flag change

**Statement**: Season-level `monitored` is written BEFORE any per-episode flag, and the episode
list is re-fetched whenever a season flag actually changed — the Sonarr season cascade is a
write-after-read hazard.

- **Why**: Sonarr propagates a season flag down to its episodes, invalidating any episode
  snapshot taken before the write. Computing episode sets from the stale snapshot makes the
  unmonitor batch skip episodes the cascade just re-monitored — which the sync's re-search then
  re-downloads.
- **Incident**: 31fa9d5 → v1.6.1 — watched episodes re-downloaded after a season cascade; the fix
  also resolves the anchor before any write so a missing anchor aborts with no half-applied flags.
- **Enforced at**: `src/monitorr/engine/window.py:154-179` (anchor resolved pre-write; seasons
  set first; on `True` return, episodes re-fetched and the anchor re-resolved);
  `src/monitorr/engine/actions.py:28-42` (`set_seasons_monitored` returns whether Sonarr actually
  changed — ALWAYS `False` in dry-run, so callers skip the refresh); `actions.py:104-107`
  (normalize turns seasons off first, cascade-proof order).
- **If violated**: stale-snapshot writes; re-monitored watched episodes; re-download loop.
- **Gate**: never reorder season vs episode writes in `apply_window`/`normalize_to_pilot`; any
  new Sonarr write sequence must state which snapshot each set is computed from.

### 7. A blind sweep advances nothing

**Statement**: When the Plex history read fails, an incremental sync is promoted to full, and
neither the watermark nor `last_full_sync` is stamped for that cycle.

- **Why**: An empty delta from a dead endpoint is indistinguishable from "nothing new". Stamping
  the watermark at "now" buries the plays missed during the outage below the incremental floor
  forever; stamping `last_full_sync` stops the promote-to-full degradation for a whole floor
  interval. Leaving both untouched keeps every cycle full until history recovers.
- **Incident**: beb757c → v1.6.1 — a failed history read stamped `watermark=now` (same commit as
  invariant 8).
- **Enforced at**: `src/monitorr/sync.py:108-115` (`history_failed`; incremental promoted to
  full), `sync.py:126-131` (`_persist_watermark` only when `not history_failed`);
  `src/monitorr/sync.py:256-268` (`_watch_history` returns `None` on failure and documents the
  contract).
- **If violated**: plays silently lost during outages; sync state advances on no evidence.
- **Gate**: any change to `_run`'s stamping logic needs a test where the history endpoint raises.

### 8. Sync state is per-server

**Statement**: `history_watermark` and `last_full_sync` are bound to the linked PMS identity
(`plex_server_id` = the server's `clientIdentifier`) and are reset whenever that identity changes
or the server is unlinked.

- **Why**: Watermarks are timestamps from ONE server's history. Carried over to a different PMS,
  a (possibly future) watermark makes incremental sweeps skip the new server's plays forever.
  `clientIdentifier` is the stable identity — URIs and names change.
- **Incident**: beb757c → v1.6.1 — stale watermark survived relinking a different PMS.
- **Enforced at**: `src/monitorr/web/routes.py:72-87` (`_store_server`: identity compare +
  delete of both keys on change), `routes.py:393-401` (unlink deletes `PLEX_SERVER_ID`,
  `HISTORY_WATERMARK`, `LAST_FULL_SYNC`); `src/monitorr/constants.py:15-17` (key + rationale).
- **If violated**: a relinked instance never sees the new server's viewing; anchors never advance.
- **Gate**: any new persisted sync-state key must be added to BOTH reset sites (link-change and
  unlink) or justified as server-independent.

### 9. Migrations are append-only; schema version = list index

**Statement**: `db.MIGRATIONS` is an ordered list; `PRAGMA user_version` equals the count of
applied entries (index + 1); new migrations are appended at the end and published entries are
never edited.

- **Why**: Deployed databases only replay entries beyond their stored version. Editing a
  published entry means existing installs never see the change while fresh installs diverge —
  two silently different schemas in the wild.
- **Incident**: none directly — but migration 4 (`db.py:54-58`, drop `last_full_sync` to force one
  full re-anchoring sync) is how the e572eff anchor fix was safely rolled out: a data migration
  in the same append-only list. That pattern (append a corrective migration) is the sanctioned
  way to force post-upgrade reconciliation.
- **Enforced at**: `src/monitorr/db.py:5-8` (rule stated), `db.py:62-72` (`init_db` applies
  `range(current, len(MIGRATIONS))` and stamps `version + 1`).
- **If violated**: schema drift between installs; un-diagnosable per-user bugs.
- **Gate**: diff review — a change inside an existing `MIGRATIONS` element is an automatic
  reject; only appends pass.

### 10. Identifier stability

**Statement**: Plex↔Sonarr correlation uses `tvdbId`; Plex history↔show correlation uses the
normalized `grandparentTitle` (never ratingKey); the poller debounce key includes
`(season, episode)`, not sessionKey alone.

- **Why**: Each identifier is chosen for stability across the failure that broke its predecessor:
  ratingKeys change on remove/re-add (title does not, both sides come from the same PMS);
  Plex reuses sessionKey when auto-playing the next episode of a binge, so sessionKey alone
  debounces away real watches.
- **Incident**: 5ce7fab → v1.4.2 (ratingKey-scoped history query emptied by a re-add).
- **Enforced at**: `src/monitorr/sync.py:86-89` (`by_tvdb` index of Sonarr series);
  `src/monitorr/plex/client.py:313-317` (`normalize_title` = casefold+strip, documented as the
  join key stable across re-add), `client.py:357-366` (global history sweep, no
  `metadataItemID`); `src/monitorr/plex/poller.py:19-21` (`WatchKey =
  tuple[session_key, season, episode]`); tvdb resolution from Plex GUIDs:
  `client.py:24,193,227-240`. Full identifier-stability table: `monitorr-plex-sonarr-reference`.
- **If violated**: lost correlation → anchor falls back (re-download) or watches silently dropped.
- **Gate**: introducing any new cross-system key requires a stability argument (what survives
  re-add, rename, server switch, binge auto-play?).

### 11. The GET arm is gated by the graces' inactivity clock

**Statement**: `_is_armed` disarms the GET window (no monitoring ahead, no search, seasons off)
once a show has been inactive longer than `min(dormant_days, grace_unwatched_days)`; only those
two graces gate — `grace_completed_days`/`grace_watched_days` intentionally do not.

- **Why**: Without the gate, every full sync re-armed GET on a show already past a grace that
  deletes what GET downloads: download → sweep deletes → next sync downloads again, an endless
  oscillation. `completed` must NOT gate so a caught-up show re-arms itself when a new season
  airs. A live watch records activity before applying the window, so resuming a show re-arms
  naturally.
- **Incident**: c72357d → v1.6.1 — abandoned shows oscillated download/delete on every full sync.
- **Enforced at**: `src/monitorr/engine/window.py:38-50` (`_is_armed`, sharing `age_days` with
  the grace sweep so both judge inactivity identically — `src/monitorr/engine/policy.py:15-21`);
  consumed at `window.py:161-164` and in `monitor_ids`/search (`window.py:214-220`), season flags
  (`window.py:30-35`), and the queue-guard window (`window.py:268`). Canonical `_is_armed`
  formula: `monitorr-window-engine-reference`.
- **If violated**: download/delete oscillation on inactive shows; or a returning show that never
  re-arms (if the wrong graces gate).
- **Gate**: any change to which thresholds gate, or to when activity is recorded relative to
  `apply_window`, needs a test for BOTH the oscillation and the re-arm-on-return case.

### 12. S00 specials are excluded; airing order is the ordering everywhere

**Statement**: Episodes with `season_number == 0` (specials) never enter the window or grace
computations; every ordering and comparison uses the `(season_number, episode_number)` tuple
(airing order), lexicographically.

- **Why**: Specials have no meaningful position in the watch sequence — counting them corrupts
  index arithmetic (GET/KEEP slices) and anchor comparisons. One consistent ordering keeps the
  anchor floor, KEEP floor, caught-up check, and debounce comparable across modules.
- **Incident**: none for S00 itself (designed-in); 9a189ef showed what a numbering mismatch does
  to an unclamped comparison. KNOWN LIMITATION: shows Plex numbers with anime ABSOLUTE numbering
  do not map to Sonarr's `(season, episode)` — the clamp (invariants 1, 4) contains the damage
  but does not translate the numbering.
- **Enforced at**: `src/monitorr/engine/window.py:53-56` (`_real_episodes`: `season_number >= 1`,
  sorted by the tuple); `src/monitorr/engine/grace.py:44-46` (same filter), `grace.py:87` (same
  sort); comparisons: `window.py:130-133`, `grace.py:18-24,90`, `poller.py:45`.
- **If violated**: off-by-N windows on shows with specials; incomparable anchors.
- **Gate**: any new episode list must pass through the `season_number >= 1` filter and the tuple
  sort before indexing; never sort or compare by Sonarr episode id or ratingKey.

## Component contract (what each module owes the others)

| Provider | Contract |
|---|---|
| `engine/window.py` `apply_window(tvdb_id, season, episode, *, series=None, episodes=None)` | Caller MAY pass an already-fetched `SonarrSeries`/episode list to save API calls (the sync does; poller/webhook pass None). `series` is only read for `.id`/`.seasons`; `episodes` must be the same start-of-cycle snapshot apply_window would have fetched itself — never a filtered or mutated list. Returns the set of episode ids it searched, so the sync's same-cycle re-search can skip them (its queue snapshot predates those grabs). Enforces the anchor floor internally — callers need not (and must not) pre-floor. (`window.py:86-100`) |
| `engine/actions.py` (all functions) | Sole Sonarr write path; every function is a no-op under `dry_run=True`. `set_seasons_monitored` returns whether Sonarr was ACTUALLY changed — always `False` in dry-run — so callers key their snapshot re-fetch on it (`actions.py:28-42`). `delete_episode` records a deduped preview in dry-run, and on a real delete removes the file, unmonitors, and logs — callers must exclude deleted ids from their own unmonitor batches (`window.py:256-259`). |
| `plex/poller.py` `process_watch` | The one live-watch entry point, shared by poller and webhook: records the watch, then anchors on the MAX recorded watch — never on the episode just played (`poller.py:35-46`). |
| `store.py` `record_watch` | Upserts `episode_watch` and advances `series_activity` by `MAX(last_watch_at, new)` so backfill order never regresses activity (`store.py:101-118`). |
| `engine/policy.py` `effective_policy` | Returns `(Policy, enabled)`; the per-series override field-merges over the global policy. Every engine path must check `enabled` before acting (`policy.py:51-61`). |
| `engine/grace.py` `sweep` | Iterates `store.list_activity()` — a show with no recorded activity is never swept (`grace.py:139`). Shares `keep_protected_keys` and `age_days` with window.py. |
| `sync.py` `run_sync` | Single-flight via lock; decides full vs incremental; owns watermark stamping (invariants 7, 8). |

## Before you change X, read Y

| You are about to change… | First read |
|---|---|
| Anchor computation, `apply_window`, `process_watch`, `_merge_watches` | Invariants 1, 2, 12 here; formulas in `monitorr-window-engine-reference`; incidents e572eff, 4245d21, 5ce7fab, 9a189ef in `monitorr-failure-archaeology`; gate in `monitorr-change-control` |
| What gets monitored/unmonitored, `_desired_seasons`, normalize | Invariants 3, 6, 11; incidents dd889ee, 31fa9d5, c72357d |
| Any deletion path (window trims, grace, normalize) | Invariants 4, 5; incidents 20d578e, 9a189ef; grace order in `monitorr-window-engine-reference` |
| Sonarr write sequencing or a new Sonarr call | Invariants 5, 6; API quirks in `monitorr-plex-sonarr-reference` |
| Sync mode logic, watermark, `_persist_watermark` | Invariants 7, 8; incident beb757c |
| `db.py` / schema | Invariant 9 |
| Plex↔Sonarr correlation, history sweep, poller debounce | Invariants 10, 12; `monitorr-plex-sonarr-reference` |
| Policy defaults, grace thresholds, Always-Have grammar | Invariants 4, 11; owner tables in `monitorr-config-and-flags` |
| Season-pack guard / queue cancel | Invariant 3 + the accepted grab/cancel trade-off: `monitorr-season-pack-campaign` |

## When NOT to use

- Full incident narratives (what happened, how it was found) → `monitorr-failure-archaeology`.
- The canonical GET/KEEP/grace/armed formulas with worked math → `monitorr-window-engine-reference`.
- Diagnosing a live symptom (re-download happening NOW, sync stuck) → `monitorr-debugging-playbook`.
- Git/branch/release/merge procedure → `monitorr-change-control`.
- Plex/Sonarr endpoint details and auth → `monitorr-plex-sonarr-reference`.
- Env vars, Policy field tables, defaults → `monitorr-config-and-flags`.
- Doc/code disagreements (errata) → `monitorr-docs-and-writing`.

## Provenance and maintenance

Verified against v1.6.1 (commit c580f65) on 2026-07-02. Line numbers WILL drift; re-verify each
invariant from the repo root before relying on a cited line:

| Invariant | Re-verification command |
|---|---|
| 1 | `grep -n "Anchor floor" src/monitorr/engine/window.py` and `grep -n "def record_watch" src/monitorr/store.py` |
| 2 | `grep -n "metadataItemID\|allLeaves" src/monitorr/plex/client.py src/monitorr/sync.py` |
| 3 | `grep -n "monitor_ids = " src/monitorr/engine/window.py` |
| 4 | `grep -n "keep_protected_keys\|matches_always_have" src/monitorr/engine/*.py` |
| 5 | `grep -rn "await sonarr\.\(set_monitored\|delete_\|search_episodes\)" src/monitorr/ --include="*.py" \| grep -v actions.py` (expect no hits outside actions.py) and `grep -n "def get_dry_run" -A 2 src/monitorr/engine/policy.py` |
| 6 | `grep -n "set_seasons_monitored" src/monitorr/engine/window.py src/monitorr/engine/actions.py` |
| 7 | `grep -n "history_failed" src/monitorr/sync.py` |
| 8 | `grep -n "PLEX_SERVER_ID\|HISTORY_WATERMARK" src/monitorr/web/routes.py src/monitorr/constants.py` |
| 9 | `grep -n "user_version\|MIGRATIONS" src/monitorr/db.py` |
| 10 | `grep -n "normalize_title\|WatchKey\|by_tvdb" src/monitorr/plex/*.py src/monitorr/sync.py` |
| 11 | `grep -n "_is_armed" src/monitorr/engine/window.py` |
| 12 | `grep -n "season_number >= 1" src/monitorr/engine/*.py` |

Incident hashes: `git log --oneline <hash> -1` (tags v1.0.0–v1.6.1 exist on the remote; a fresh
clone may need `git fetch --tags`). If code and this skill disagree, the code wins — fix the
skill in the same commit as the change, per `monitorr-change-control`.
