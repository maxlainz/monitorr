---
name: monitorr-window-engine-reference
description: >-
  Canonical reference for src/monitorr/engine/ (window.py, grace.py, policy.py, actions.py) —
  the ONE home of the exact GET/KEEP/anchor/grace formulas. Use when reading, changing, testing
  or predicting apply_window, the grace sweep, policy resolution, Always-Have matching,
  normalize-to-pilot, dry-run gating, the season-pack queue guard, or the _is_armed gate. NOT
  for Plex/Sonarr API details → monitorr-plex-sonarr-reference; WHY invariants exist →
  monitorr-architecture-contract; incident stories → monitorr-failure-archaeology; env/Policy
  editing → monitorr-config-and-flags.
---

# monitorr window engine reference

Canonical reference for `src/monitorr/engine/`. Every formula below is stated in its ONE
canonical form and anchored to code (verified against v1.6.1 working tree, 2026-07-02). If this
file and the code ever disagree, the code wins — fix this file in the same commit (see
`monitorr-change-control`). Any change to the formulas themselves is an anchor/window change and
requires the adversarial-review discipline in `monitorr-change-control`.

Definitions used throughout (defined once, used everywhere):

| Term | Meaning |
|---|---|
| anchor | The `(season, episode)` the window is applied around — the furthest-watched episode |
| GET | The window of episodes AHEAD of the anchor that monitorr monitors + searches |
| KEEP | The retention window BEHIND the anchor whose files are kept on disk |
| Always-Have | Pattern-protected episodes (default pilot `S01E01`) never deleted, always monitored |
| grace | Time-based deletion sweeps (watched/unwatched/dormant/completed) |
| dry-run | Master switch (ON by default) that turns every Sonarr write into a logged no-op |
| armed | Whether the GET window is allowed to monitor/search (see `_is_armed`) |
| `real` | Sonarr episodes with `season_number >= 1` (S00 specials excluded), sorted by `(season_number, episode_number)` — `src/monitorr/engine/window.py:53-56` |
| `idx` | The anchor's index in `real` |
| key | A `(season, episode)` tuple; keys compare lexicographically |
| season cascade | Sonarr propagating a season `monitored` flag down to that season's episodes |
| watermark | Newest Plex play timestamp already scanned by sync (sync-owned state) |
| season pack | A single torrent containing a whole season; one queue row per episode, one torrent |

Files covered: `src/monitorr/engine/window.py`, `src/monitorr/engine/grace.py`,
`src/monitorr/engine/policy.py`, `src/monitorr/engine/actions.py`, plus the entry points
`src/monitorr/plex/poller.py`, `src/monitorr/web/routes.py` (webhook route), `src/monitorr/sync.py`.

## 1. `apply_window` pipeline (window.py:86-283)

`apply_window(tvdb_id, season, episode, *, series=None, episodes=None) -> set[int]` — returns
the episode ids it searched (used by sync, step 12 of §5). Numbered steps in execution order:

1. **Policy resolution** — `policy, enabled = await effective_policy(tvdb_id)`
   (window.py:101). If the series override disables the show, return `set()` immediately
   (window.py:102-103). Sonarr unconfigured → warn and return `set()` (window.py:104-107).
2. **Dry-run read** — `dry_run = await get_dry_run()` (window.py:109). Read once, threaded into
   every action call. Default True (policy.py:64-66).
3. **Fetch series/episodes** — if the caller didn't pass them (sync does; poller/webhook don't):
   `find_series_by_tvdb` (window.py:111-115; show not in Sonarr → return `set()`), then
   `get_episodes` (window.py:117-118).
4. **Real-episodes filter** — `real = _real_episodes(episodes)`: keep `season_number >= 1`
   (S00 specials out), sort by `(season_number, episode_number)` (window.py:53-56, 119).
5. **Anchor floor** — the window must never apply below the furthest episode ever recorded as
   watched in the persisted store (`episode_watch` table). Canonical form (window.py:130-142):
   `floor = max((w.season, w.episode) for recorded watches that are in real_keys,
   default=(season, episode))`; if `floor > (season, episode)` re-anchor at floor.
   Recorded watches are clamped to `real_keys` (keys Sonarr actually lists) so a persisted max
   pointing at a deleted/absolute/special episode picks the furthest *real* watched episode
   instead of no-opping the anchor lookup. Enforced HERE so every caller (sync, poller, webhook)
   is protected. Why this exists: `monitorr-architecture-contract` (anchor monotonicity).
6. **Anchor index, abort-before-write** — `idx = _anchor_idx(real)` (window.py:144-159). If the
   (possibly floored) anchor is not in `real`, log a warning and return `set()` BEFORE any Sonarr
   write — no half-applied season flags.
7. **`_is_armed` gate** — canonical form (window.py:38-50):
   `thresholds = [d for d in (policy.dormant_days, policy.grace_unwatched_days) if d is not None]`;
   armed iff `thresholds` is empty, or no activity recorded (`store.get_activity` is None), or
   `age_days(last_activity) <= min(thresholds)`. `grace_completed_days` and `grace_watched_days`
   intentionally do NOT gate (a caught-up show must re-arm when a new season airs). Purpose: a
   show inactive past a grace that deletes what GET downloads must not re-arm on every full sync
   (download/delete oscillation, fixed c72357d — story in `monitorr-failure-archaeology`).
8. **Season monitoring, before episodes, re-fetch after cascade** —
   `_desired_seasons(series, season, policy, armed)` (window.py:23-35): unit `episodes` → every
   season flag off (100% per-episode control); unit `seasons` and armed → on iff
   `anchor.season <= season_number <= anchor.season + get_count`; not armed → all off. Applied via
   `actions.set_seasons_monitored(...)` (window.py:172-179), which returns True only when Sonarr
   was actually PUT (always False in dry-run, actions.py:28-42; idempotence check in
   `sonarr/client.py:159-166`, def at :145). If True, Sonarr may have cascaded the season flags to episodes,
   invalidating the snapshot → re-fetch `real` and re-resolve `idx`; anchor vanished mid-flight →
   return `set()`. Order matters: season flags before per-episode flags is the cascade-proof
   order (fix 31fa9d5).
9. **GET selection** — `ahead = _select_ahead(real, idx, policy)` (window.py:59-63, 182):
   - episodes mode: `ahead = real[idx+1 : idx+1+get_count]`.
   - seasons mode: `ahead = [e for e in real[idx+1:] if e.season_number <= anchor.season + get_count]`.
   Edge cases: `get_count=0` → empty in episodes mode; anchor last episode → empty.
   `search_ahead = [e for e in ahead if not e.has_file and e.has_aired()]` (window.py:196) —
   unaired episodes are monitored but NOT searched (guaranteed-empty indexer query; Sonarr grabs
   them on air via RSS; fix 99ba5fc). `has_aired()` = `air_date_utc <= now(UTC)`, False when the
   air date is missing (`sonarr/client.py:49-52`).
10. **Always-Have set** — `always_have_ids = {e.id for e in real if
    matches_always_have(policy.always_have, e.season_number, e.episode_number)}`
    (window.py:187-191). Computed once; reused by the unmonitor batch and the queue guard.
11. **Monitor / search application** — the monitoring invariant, canonical form (window.py:214):
    `monitored ≡ (GET window if armed else ∅) ∪ Always-Have`. Everything else is unmonitored
    WHILE KEEPING ITS FILE (anchor + KEEP-behind). Since v1.6.0 Always-Have episodes stay
    monitored so Sonarr can upgrade/re-fetch them. Code: `monitor_ids = (ahead_ids if armed else
    set()) | always_have_ids` → `actions.monitor_episodes` (window.py:214-216); if armed AND
    `policy.search_on_get` AND `search_ahead` non-empty → `actions.search_episodes` and record
    `searched_ids` (window.py:217-220). Gated (un-armed): only Always-Have keeps monitoring.
12. **Trim ahead** — every episode in `real[idx+1:]` that is NOT in `ahead_ids`, NOT Always-Have,
    and `has_file` is deleted with reason `"ahead"` (window.py:226-235). Symmetric to KEEP.
13. **KEEP trim behind** — canonical form (window.py:66-70, 238-249):
    - episodes mode: episode at index `i < idx` is deleted iff `i < idx - keep_count + 1`
      (i.e. keep the anchor and the `keep_count - 1` episodes immediately behind).
    - seasons mode: deleted iff `e.season_number < anchor.season - (keep_count - 1)`.
    Skips: no file, inside KEEP, Always-Have. Reason `"keep"`. Edge case: `keep_count=0` deletes
    every behind episode, but the anchor itself is never in `range(idx)` and
    `keep_protected_keys` (window.py:73-83) always includes the anchor — the anchor's file
    survives any `keep_count`.
14. **Decoupled unmonitor** — `to_unmonitor = [e.id for e in real if e.id not in monitor_ids and
    e.monitored and e.id not in deleted_ids]` (window.py:258-261). Monitoring is decoupled from
    retention: the anchor and KEEP-behind episodes keep their files but lose `monitored`, so a
    season of merely-kept episodes never reaches the all-monitored state that lets Sonarr grab a
    season-pack "upgrade" (fix dd889ee). `deleted_ids` excluded because `delete_episode` already
    unmonitors what it removes (actions.py:178).
15. **Season-pack queue guard** — cancel from Sonarr's queue every queued episode outside
    `GET ∪ KEEP-protected ∪ Always-Have` (window.py:268-282): `in_window_ids = (ahead_ids if
    armed else ∅) ∪ keep_protected_keys(real, idx, policy) ∪ always_have_ids`; surplus = queued
    ids not in it; `actions.cancel_downloads` deletes each surplus queue row via
    `DELETE /api/v3/queue/{id}` with `removeFromClient=false&blocklist=false` (client keeps
    seeding; actions.py:73-89, `sonarr/client.py:204-221`). CAVEAT: a queue row is per-episode
    but maps to ONE torrent — cancelling a surplus episode cancels the whole pack, including
    in-window episodes. This is the grab/cancel oscillation mechanism; the campaign on it is
    `monitorr-season-pack-campaign`.
16. **Return** — `searched_ids` (window.py:283), consumed by sync (§5).

`keep_protected_keys(real, anchor_idx, policy)` (window.py:73-83) = the anchor's key plus every
behind key NOT selected by the KEEP-delete predicate. Shared with the grace sweep so KEEP acts as
a retention floor there too (fix 20d578e).

## 2. Grace sweep (grace.py)

`sweep()` (grace.py:133-143) runs on the `grace_sweep_interval` loop (`main.py:52`), reads
Sonarr config + dry-run once, then iterates `store.list_activity()` — ONLY shows with recorded
activity are ever swept. Per show, `_sweep_series` (grace.py:27-130):

Preconditions (any failure → skip the show silently): policy enabled; at least one of the four
grace fields non-None (grace.py:33-39); series found in Sonarr; `all_eps` = episodes with
`season_number >= 1`; `episodes` = the subset with `has_file` — grace only ever deletes files
that exist (grace.py:44-49). `activity_age = age_days(last_activity)` (grace.py:52).

Evaluation order — canonical form: completed → dormant (both bulk, both IGNORE the KEEP floor,
both respect Always-Have) → then KEEP floor computed (`keep_protected_keys`, anchor clamped to
episodes Sonarr lists) → watched (keeps most-recent watched as marker) → unwatched (keeps first
unwatched as marker; gated by activity age > grace_unwatched_days).

| Step | Predicate (canonical) | Deletes | Skips | Then |
|---|---|---|---|---|
| completed (grace.py:57-68) | `grace_completed_days is not None` AND `activity_age > grace_completed_days` AND caught-up | every on-disk episode, reason `"completed"` | Always-Have | `return` (nothing else runs) |
| dormant (grace.py:71-78) | `dormant_days is not None` AND `activity_age > dormant_days` | every on-disk episode, reason `"dormant"` | Always-Have | `return` |
| KEEP floor (grace.py:87-95) | — | — | — | `anchor = max((key for key in watches if key in real_keys), default=None)`; if found, `kept_keys = keep_protected_keys(real, anchor_idx, policy)` |
| watched (grace.py:98-115) | `grace_watched_days is not None`; per episode: on disk AND in `watches` AND not the marker AND `age_days(seen_at) > grace_watched_days` | reason `"grace_watched"` | marker = most-recently-watched (max `watched_at`); `kept_keys`; Always-Have | continue |
| unwatched (grace.py:118-130) | `grace_unwatched_days is not None` AND `activity_age > grace_unwatched_days`; per episode: on disk AND never in `watches` | reason `"grace_unwatched"` | first unwatched in airing order (`unwatched[0]`); `kept_keys`; Always-Have | done |

Caught-up, canonical form (grace.py:18-24): `max(watched_keys) >= max(aired_keys)` —
lexicographic max, i.e. furthest watch vs last aired; False if nothing aired or nothing watched.
Linear viewing assumed (a skipped mid-run episode does not block the purge — accepted).

What ignores what:

- completed and dormant IGNORE the KEEP floor (the show is finished/abandoned) but respect
  Always-Have and dry-run.
- watched and unwatched respect the KEEP floor, their markers, Always-Have and dry-run.
- The `watched` trim is NOT gated by `activity_age`; only `unwatched` (and the bulk purges) are.
- The KEEP-floor anchor is clamped to `real_keys` exactly like apply_window's floor — a recorded
  watch Sonarr doesn't list must pick the furthest real watched episode, not silently dissolve
  the whole floor (fix 9a189ef).
- All deletions route through `actions.delete_episode`, so dry-run turns them into pending
  previews (§4).

## 3. Policy resolution (policy.py)

The 11 `Policy` fields (policy.py:24-37) — types, defaults, constraints, and where each is
edited — are owned by `monitorr-config-and-flags` (§3 there is the definition of record). This
file owns each field's RUNTIME semantics: `get_count`/`get_unit` drive GET (§1 step 9),
`keep_count`/`keep_unit` drive KEEP (§1 step 13), `always_have` the protection set (grammar
below), `grace_watched_days`/`grace_unwatched_days`/`dormant_days`/`grace_completed_days` the
sweep (§2) — with the unwatched/dormant pair also gating `_is_armed` (§1 step 7) —
`search_on_get` §1 step 11, `auto_normalize` §4.

- `effective_policy(tvdb_id) -> (Policy, enabled)` (policy.py:51-61): global policy is the
  `policy` setting-table JSON, or `Policy()` defaults if unset. No `series_override` row →
  `(global, True)`. Row with `policy_json` → shallow dict merge
  `{**base.model_dump(), **partial}` re-validated as `Policy` — the override REPLACES whole
  fields; absent fields fall through to global; `enabled` comes from the row. Row without
  `policy_json` → global policy with the row's `enabled`.
- Always-Have pattern grammar — canonical form: regex `^S(\d+|\*)(?:E(\d+|\*))?$`,
  case-insensitive → `S01E01`, `S*E01`, `S01`, `S*` all valid; invalid patterns ignored. Default
  `["S01E01"]`. Code uses the equivalent named-group form at policy.py:89;
  `matches_always_have` at policy.py:92-104 (missing/`*` episode token matches the whole
  season; `*` season token matches every season).
- `get_dry_run()` (policy.py:64-66): setting `dry_run`; default True when unset ("1" = on).
  `set_dry_run(False)` clears all pending deletion previews (policy.py:69-73).
- `get_watched_threshold()` (policy.py:76-78): default 0.9; clamped to `[0.05, 1.0]` on save in
  the settings form (`web/routes.py:306-308`), not on read.
- `get_user_filter()` (policy.py:81-86): JSON list of Plex user names; empty/unset → `[]`.
- `age_days(iso_timestamp)` (policy.py:15-21): naive (zone-less legacy) timestamps are assumed
  UTC; returns fractional days vs `datetime.now(UTC)`. The single inactivity clock shared by the
  grace sweep and `_is_armed`, so both judge inactivity identically.

## 4. Actions (actions.py) — real effect vs dry-run

Every Sonarr WRITE in the codebase routes through this module; reads (get_episodes, get_queue)
do not. With `dry_run=True` Sonarr is never called — intent is logged, deletions recorded.

| Action (line) | Real effect | Dry-run behavior |
|---|---|---|
| `monitor_episodes` (17) | `set_monitored(ids, True)` | log only |
| `set_seasons_monitored` (28) | GET-modify-PUT of `seasons[].monitored`; returns True iff a PUT was issued (idempotent — no PUT when flags already match, `sonarr/client.py:159-166`) | log only; **returns False ALWAYS** — the contract callers rely on: a False return means "no cascade happened, the episode snapshot is still valid, do not re-fetch" |
| `unmonitor_episodes` (45) | `set_monitored(ids, False)` | log only |
| `search_episodes` (62) | POST EpisodeSearch command | log only |
| `cancel_downloads` (73) | for each queued item whose `episode_id` is wanted: `DELETE /queue/{id}?removeFromClient=false&blocklist=false` (torrent keeps seeding) | log only (queue not even read) |
| `delete_episode` (150) | delete the episode file (if `episode_file_id`), unmonitor the episode, append a real row to `deletion_log` (removes the fulfilled preview) | record a PREVIEW row (`dry_run=1`, deduped to one row per episode, `store.py:162-182`) + log |
| `normalize_to_pilot` (92) | sequence below | seasons/searches/monitors are no-ops; deletions become previews; final re-monitor+pilot-search skipped |

`normalize_to_pilot` exact sequence (actions.py:92-147):

1. All seasons monitored=off first (cascade-proof order — Sonarr must not re-monitor episodes by
   season after step 4).
2. Resolve pilot (S01E01) and `always_have_ids`.
3. `to_delete` = has-file episodes that are neither pilot nor Always-Have → `delete_episode`
   each, reason `"normalize"`.
4. Remaining non-pilot non-Always-Have episodes → unmonitored (real mode only).
5. `cancel_downloads` on the unmonitored set (in-flight downloads not imported).
6. Dry-run stops here. Real mode: re-monitor pilot + Always-Have (so Sonarr can upgrade/re-fetch
   them), and search the pilot if it lacks a file.

`normalize_to_pilot` is AUTOMATIC. Sync calls it every cycle for every enabled managed show with
`policy.auto_normalize` and NO recorded viewing (`sync.py:315-321` — `_reconcile_managed`,
"set-and-forget"). There is NO manual route or button (`web/routes.py` has none; the button was
removed in 9468870). The only opt-outs are the `auto_normalize` policy field (global or
per-series override) or disabling the series. Note: `.claude/architecture.md` is stale here —
see `monitorr-docs-and-writing`.

## 5. Entry points into `apply_window`

Three callers; all inherit the anchor floor (§1 step 5), so a caller passing a "low" anchor is
corrected inside `apply_window`.

| Caller | Path | Anchor passed | Notes |
|---|---|---|---|
| Poller (live sessions) | `plex/poller.py:35-46` `process_watch` | `max((w.season, w.episode) for all recorded watches)` after recording the just-played episode; default = the just-played key | Triggered when `viewOffset/duration >= watched_threshold` (default 0.9), OR a session that disappears between polls with `progress >= NEAR_COMPLETE_PROGRESS` (0.85, `constants.py:7`). Debounce key: `(session_key, season, episode)` — sessionKey alone is NOT unique across a binge (Plex reuses it on auto-play; poller.py:21) |
| Webhook (`media.scrobble`) | `web/routes.py:553-571` | same path — it calls `process_watch` | Plex-Pass webhook; user-filter applied first; always answers 200 |
| Sync (reconciliation) | `sync.py:229-231` in `_apply_watches` | per show: `max(watched, key=(season, episode))` over the MERGED watch set — union of Plex library `allLeaves` and global play history, keeping the newest `viewed_at` per key (`_merge_watches`, sync.py:271-281); all merged watches recorded to the store first | Passes the prefetched `series` so `apply_window` skips `find_series_by_tvdb`; `episodes` fetched inside |

Return value usage: sync unions every window's `searched_ids` and hands the set to
`_reconcile_managed(..., already_searched)` (sync.py:122-124, 284-337), whose Wanted/Missing
re-search skips them — the queue snapshot it filters on predates those grabs, so without the
exclusion every missing GET-window episode got TWO EpisodeSearch commands per sync (fix
99ba5fc). Poller/webhook discard the return value.

The grace sweep (§2) is a separate periodic loop and never calls `apply_window`. Full-vs-
incremental sync mechanics, watermark and blind-sweep rules are sync-side state — summary:
full iff forced, no watermark yet, or the rolling `full_sync_interval` floor elapsed; a failed
history read promotes incremental→full and a blind sweep advances neither watermark nor
last-full (sync.py:94-131). Why that rule exists → `monitorr-architecture-contract`; Plex
endpoints behind it → `monitorr-plex-sonarr-reference`.

## 6. Worked example (derive it yourself, then compare)

Scenario — one season, 10 episodes S01E01..S01E10, all aired. Files on disk: E01–E06 and E10
(E07–E09 missing). All 10 episodes currently `monitored` in Sonarr; the season-1 flag is already
off. Policy: `get_count=2 get_unit=episodes`, `keep_count=1 keep_unit=episodes`,
`always_have=["S01E01"]`, `search_on_get=True`. Persisted furthest watch = S01E05; show active
recently (armed). Dry-run OFF. Call: `apply_window(tvdb, 1, 5)`.

1. Policy/dry-run/fetch: as given. `real = [E01..E10]`, indices 0..9.
2. Anchor floor: recorded max = (1,5) = passed anchor → no re-anchor. `idx = 4`.
3. `_is_armed`: thresholds = [365] (grace_unwatched default; dormant None); activity recent →
   armed.
4. Season monitoring: unit is episodes → desired = {1: False}; season already off → idempotent,
   no PUT → returns False → NO re-fetch, snapshot stays valid.
5. GET: `ahead = real[5:7] = [E06, E07]`; `ahead_ids = {E06, E07}`.
   `search_ahead = [E07]` (E06 has a file; E07 aired and file-less).
6. Always-Have: `always_have_ids = {E01}`.
7. Monitor: `monitor_ids = {E06, E07} ∪ {E01} = {E01, E06, E07}` → monitored.
   Search: armed ∧ search_on_get → search `{E07}`; return value will be `{E07.id}`.
8. Trim ahead (`real[5:]` minus ahead, minus Always-Have, with file): E08/E09 no file → skip;
   **E10 deleted** (reason `"ahead"`).
9. KEEP behind: delete iff `i < idx - keep_count + 1 = 4 - 1 + 1 = 4` → i ∈ {0,1,2,3} =
   E01..E04. E01 is Always-Have → kept. **E02, E03, E04 deleted** (reason `"keep"`). E05
   (i=4, the anchor) is outside `range(idx)` → kept.
10. Unmonitor: monitored, not in `monitor_ids`, not deleted → **E05, E08, E09 unmonitored**
    (E02–E04, E10 were unmonitored by their deletions).
11. Queue guard: protected = `keep_protected_keys` = {(1,5)} (with keep_count=1 nothing behind
    survives the predicate; the anchor is always included). `in_window_ids = {E06, E07} ∪
    {E05} ∪ {E01}`. If a season pack queued E01–E10, rows for **E02–E04, E08–E10 are cancelled**
    (which, one torrent per pack, kills the whole pack — the §1 step 15 caveat).

End state: files on disk E01 (Always-Have), E05 (anchor), E06 (GET); monitored exactly
{E01, E06, E07} = GET ∪ Always-Have; return `{E07.id}`. With dry-run ON instead: zero Sonarr
calls, E02/E03/E04/E10 recorded as pending previews, `set_seasons_monitored` returns False,
same return value `{E07.id}` is still reported.

## 7. When NOT to use this skill

- Plex/Sonarr endpoint details, auth, TVDB/title correlation, API quirks →
  `monitorr-plex-sonarr-reference`.
- WHY the invariants exist and what must not change without review →
  `monitorr-architecture-contract` (it states the invariants; the formulas live here).
- Incident histories behind the fixes cited above (dd889ee, 20d578e, e572eff, 31fa9d5, c72357d,
  9a189ef, 99ba5fc, 9468870) → `monitorr-failure-archaeology`.
- Editing/validating config, env vars, the settings UI surface → `monitorr-config-and-flags`.
- Diagnosing a live symptom (re-download loop, deletions not happening) →
  `monitorr-debugging-playbook`. Predicting/tracing a window decision on real data →
  `monitorr-proof-and-analysis-toolkit`.
- Changing any of this code → `monitorr-change-control` first (anchor/window changes need
  adversarial review + a regression test).

## 8. Provenance and maintenance

Verified against the working tree at v1.6.1, 2026-07-02. Line numbers drift; symbols rarely do.
Re-verify before trusting:

| Fact | Re-verification command (from repo root) |
|---|---|
| Pipeline order & line anchors in §1 | `grep -n "def apply_window\|anchor floor\|_is_armed\|set_seasons_monitored\|_select_ahead\|to_unmonitor\|Season-pack guard" src/monitorr/engine/window.py` |
| GET/KEEP formulas | `grep -n -A4 "_select_ahead\|_should_delete_behind" src/monitorr/engine/window.py` |
| Monitoring invariant line | `grep -n "monitor_ids = " src/monitorr/engine/window.py` |
| Grace order & predicates | `grep -n "grace_completed_days\|dormant_days\|kept_keys\|grace_watched_days\|grace_unwatched_days" src/monitorr/engine/grace.py` |
| Caught-up formula | `grep -n -A6 "_is_caught_up" src/monitorr/engine/grace.py` |
| Policy field names referenced in §3 (table of record: `monitorr-config-and-flags`) | `grep -n -A14 "class Policy" src/monitorr/engine/policy.py` |
| Always-Have grammar | `grep -n "_PATTERN = " src/monitorr/engine/policy.py` |
| Dry-run default / threshold clamp | `grep -n "raw == \"1\"\|0.9" src/monitorr/engine/policy.py && grep -n "max(0.05" src/monitorr/web/routes.py` |
| set_seasons_monitored False-in-dry-run contract | `grep -n -B2 -A6 "async def set_seasons_monitored" src/monitorr/engine/actions.py` |
| normalize_to_pilot is automatic, no manual route | `grep -n "normalize_to_pilot" -r src/monitorr/ \| grep -v test` (expect: actions.py definition + sync.py call, nothing in web/) |
| Entry-point anchors | `grep -n -A6 "async def process_watch" src/monitorr/plex/poller.py && grep -n "anchor = max" src/monitorr/sync.py` |
| Queue-guard cancel semantics | `grep -n -A8 "async def delete_queue_item" src/monitorr/sonarr/client.py` |
| NEAR_COMPLETE_PROGRESS | `grep -n "NEAR_COMPLETE_PROGRESS" src/monitorr/constants.py` |
| Engine tests still cover this | `ls tests/ \| grep -i -E "window\|grace\|policy\|actions\|sync"` |
