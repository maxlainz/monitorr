---
name: monitorr-proof-and-analysis-toolkit
description: Recipes for PROVING/analyzing monitorr's engine — hand-derive monitor/search/delete/unmonitor/cancel sets for a Policy + episodes, predict-then-verify before pytest, reconstruct anchor/GET/KEEP decisions from DEBUG logs, incident-archaeology method (1.5.1 anchor regression), manual sqlite3 invariant checks, equivalence arguments for window.py refactors. NOT for fixing a live symptom → monitorr-debugging-playbook; NOT the formulas' home → monitorr-window-engine-reference.
---

# monitorr proof-and-analysis toolkit

Six recipes for reasoning about the episode-window engine with rigor — before you run anything
and after something ran. Each recipe has a purpose, inputs, numbered steps and a worked example
verified against this repo's real code, tests or history.

Jargon (defined once): the **anchor** is the furthest-watched episode the window is applied at.
**GET** = the N episodes/seasons ahead of the anchor that are monitored/downloaded; **KEEP** = the
N behind the anchor whose files are retained (everything further behind is deleted).
**Always-Have** = pattern-protected episodes (e.g. the pilot `S01E01`) never deleted and kept
monitored. **Grace** = time-based deletion sweeps (watched/unwatched/dormant/completed).
**Dry-run** = master switch (ON by default) that turns every Sonarr write into a logged preview.
**Watermark** = newest Plex-history `viewedAt` already scanned; an **incremental sync** sweeps
only plays newer than it, a **full sync** rescans every managed show. **allLeaves** = Plex's
episode listing for a show (only what is currently in the library — volatile). **ratingKey** =
Plex's per-item id (changes if an item is re-added — volatile). **Season cascade** = Sonarr
propagating a season's `monitored` flag down to its episodes. **armed** = the GET window's
re-arm gate is open (show not inactive past dormant/unwatched grace).

Engine ground truth: `src/monitorr/engine/window.py` (`apply_window`), `engine/grace.py`,
`engine/policy.py`, `engine/actions.py`, `src/monitorr/sync.py`. Canonical formulas live in
`monitorr-window-engine-reference` — this skill never redefines them, it applies them.

## Recipe 1 — Predict a window outcome by hand

**Purpose**: given a Policy + a Sonarr episode list + the persisted watches, derive on paper the
exact sets `apply_window` will produce — monitor, search, delete(ahead), delete(keep),
unmonitor, cancel — before trusting any run or any log.

**Inputs**: the effective Policy (get_count/get_unit, keep_count/keep_unit, always_have,
dormant_days, grace_unwatched_days, search_on_get), the Sonarr episode list with
`(season, episode, has_file, monitored, aired?)`, the persisted `episode_watch` rows for the
show, the trigger anchor `(season, episode)`, and dry-run state.

**Steps** (this is `apply_window`'s execution order — follow it exactly; the full formula set
lives in `monitorr-window-engine-reference`, only the ones used are quoted here):

1. Build `real` = Sonarr episodes with `season_number >= 1` (S00 specials excluded), sorted by
   `(season_number, episode_number)`. Write it out as an indexed list (0-based).
2. Apply the anchor floor:
   `floor = max((w.season, w.episode) for recorded watches that are in real_keys,
   default=(season, episode))`; if `floor > (season, episode)` re-anchor at floor.
   Watches for keys Sonarr does not list are ignored by the clamp.
3. Find `idx` = index of the anchor in `real`. If absent → the window aborts with **no writes**
   (not even season flags). Stop here.
4. Decide `armed` via `_is_armed`: `thresholds = [d for d in (dormant_days,
   grace_unwatched_days) if d is not None]`; armed iff `thresholds` is empty, or no activity
   recorded, or `age_days(last_activity) <= min(thresholds)`.
5. Season flags: episodes mode → every season OFF; seasons mode + armed → seasons
   `anchor.season .. anchor.season + get_count` ON, rest OFF. If this changes Sonarr, the
   episode snapshot is re-fetched (cascade-proof) — recompute `real`/`idx` from the fresh flags.
6. GET (episodes mode): `ahead = real[idx+1 : idx+1+get_count]`.
   `search_ahead = [e for e in ahead if not e.has_file and e.has_aired()]` — unaired episodes
   are monitored but never searched. Searched only if `armed and search_on_get`.
7. `always_have_ids` = every episode in `real` matching an Always-Have pattern.
   `monitor_ids = (ahead_ids if armed else set()) | always_have_ids` — this is the monitoring
   invariant `monitored ≡ (GET window if armed else ∅) ∪ Always-Have`.
8. Trim ahead: every episode after `idx` that is not in `ahead`, not Always-Have, and
   `has_file` → deleted with reason `ahead`.
9. KEEP (episodes mode): episode at index `i < idx` is deleted iff `i < idx - keep_count + 1`
   (keep the anchor and the `keep_count - 1` episodes immediately behind) — if it `has_file`
   and is not Always-Have → deleted with reason `keep`.
10. Unmonitor: every `e` in `real` with `e.monitored` true, `e.id not in monitor_ids`, and not
    just deleted (deletion already unmonitors).
11. Queue guard: cancel every queued episode outside
    `(ahead_ids if armed else ∅) ∪ keep_protected_keys ∪ always_have_ids`.
12. Dry-run does not change ANY of the sets above — it only converts the writes into logged
    previews (deletions land in `deletion_log` with `dry_run=1`).

**Worked example** — `test_anchor_floored_at_persisted_furthest_watch`
(`tests/test_window.py`, currently line 578; find it: `grep -n "anchor_floored" tests/test_window.py`).

Inputs: Policy `get_count=1, keep_count=1, always_have=[]`, other fields default
(`grace_unwatched_days=365`, `dormant_days=None`); dry-run ON. Episodes = `AHEAD_EPISODES`:
S01E01–E05 with files, E06 no file + monitored, E07 no file + unmonitored, all aired.
Persisted watch: `(1, 5)` recorded now. Trigger: `apply_window(TVDB, season=1, episode=2)`.

| Step | Derivation |
|---|---|
| 1 | `real = [E1..E7]`, indices 0..6 |
| 2 | recorded watches in real_keys = {(1,5)}; `floor=(1,5) > (1,2)` → re-anchor at S01E05 |
| 3 | `idx = 4` |
| 4 | thresholds=[365]; activity recorded "now" → `age ≈ 0 ≤ 365` → **armed** |
| 5 | episodes mode + test series has no seasons → desired set empty → no season write, no re-fetch |
| 6 | `ahead = real[5:6] = [E6]`; E6 has no file and aired → `search_ahead=[E6]` → searched (preview) |
| 7 | `monitor_ids = {106}` (E6); no Always-Have |
| 8 | after idx: E6 in ahead (skip), E7 has no file (skip) → no `ahead` deletions |
| 9 | delete iff `i < 4 - 1 + 1 = 4` → indices 0..3 = **E1, E2, E3, E4**, all with files → reason `keep` |
| 10 | unmonitor = E5 only (E1–E4 deleted, E6 monitored by GET, E7 already unmonitored) |
| 11 | queue mocked empty → no cancels |
| 12 | dry-run ON → deletions recorded as previews |

Predicted pending previews: `{(1,1), (1,2), (1,3), (1,4)}`. The test asserts exactly this set —
and an inline comment in the test confirms the counterfactual: an un-floored anchor E2 would instead have kept
E1+E5 and trimmed E4/E5 as `ahead` (the 1.5.1 regression shape, Recipe 4).

## Recipe 2 — Predict-then-verify for any engine change

**Purpose**: never learn what your change does by watching pytest output. Commit to the numbers
first; a mismatch in EITHER direction is a finding. This is the engine-specific instantiation of
the general predict-numbers worksheet owned by `monitorr-research-methodology` (which also sets
the evidence bar the comparison must meet).

**Steps**:

1. Pick the ONE test that pins the behavior you are touching (Recipe 6 has the branch→test map).
   If none pins it, write it first — before the change.
2. Fill in the worksheet below using Recipe 1, BEFORE running anything.
3. Run only that test: `uv run pytest tests/test_window.py::<test_name> -q`
   (CI proves `uv run pytest` is the runner: `.github/workflows/ci.yml`).
4. Compare per row. A pass with a wrong prediction means your model of the engine is wrong —
   stop and fix the model before touching more code. An unexpected fail means the change does
   more than intended.
5. Only then run the full suite (`uv run pytest`) and the gates
   (see `monitorr-validation-and-qa`).

**Worksheet template** (copy per change):

```
Change: <one line: what and why>
Test:   tests/test_window.py::<name>
Anchor after floor: S__E__   idx: __   armed: __   dry_run: __
Predicted sets (keys, not counts alone):
  monitor    = { }
  search     = { }
  delete ahead = { }
  delete keep  = { }
  unmonitor  = { }
  cancel     = { }
Actual (from the run): ...
Verdict: match / mismatch → which step of Recipe 1 was wrong: __
```

If the change touches anchor/sync/window code at all, it additionally needs the adversarial
review + regression test required by `monitorr-change-control` (discipline rule: anchor changes
are argued against the full incident list before merging).

## Recipe 3 — Trace a live decision from DEBUG logs

**Purpose**: reconstruct exactly why a running instance monitored/searched/deleted/cancelled
what it did, from its logs alone.

**Setup**: set `MONITORR_LOG_LEVEL=DEBUG` (env var; `config.py` field `log_level`, default
`INFO`) and restart. The window's two decision lines are DEBUG-only; the sync's per-show anchor
line is INFO, so it is visible even without DEBUG.

**The lines these steps grep for** (verified in code; `%`-style formats quoted verbatim — the
complete verified log-format inventory is owned by `monitorr-diagnostics-and-tooling`):

| Level | Source | Format |
|---|---|---|
| INFO | `sync.py` (per show) | `sync show tvdb=%s title=%s rating_key=%s allLeaves=%d history=%d merged_keys=%s anchor=S%02dE%02d` |
| INFO | `window.py` (floor fired) | `re-anchored tvdb=%s from S%02dE%02d to S%02dE%02d (persisted furthest watch)` |
| INFO | `window.py` (gated) | `GET window not armed for tvdb=%s (inactive past dormant/unwatched grace)` |
| DEBUG | `window.py` (pre-write) | `apply_window tvdb=%s anchor=S%02dE%02d idx=%d get=%d%s keep=%d%s dry_run=%s ahead=%s search_ahead=%s` |
| DEBUG | `window.py` (post-write) | `apply_window tvdb=%s unmonitor=%d in_window=%d queued=%d surplus=%s` |
| INFO | `actions.py` (dry-run) | `[dry-run] would monitor/unmonitor/search %d episodes`, `[dry-run] would delete S%02dE%02d (%s)`, `[dry-run] would cancel queued downloads of %d episodes`, `[dry-run] would set monitored seasons on=%s off=%s` |
| INFO | `actions.py` (real) | `deleted S%02dE%02d (%s)`, `queued download cancelled (episodeId=%s); still seeding` |

**Steps to reconstruct a decision**:

1. Find the show's `sync show tvdb=...` line. `allLeaves=` and `history=` are the two source
   counts; `merged_keys=` is their union; `anchor=` is the max of merged_keys — the anchor the
   sync HANDED to the window (pre-floor).
2. If a `re-anchored tvdb=... (persisted furthest watch)` line follows, the floor corrected that
   anchor upward — the live sources under-reported the furthest watch (exactly the 1.5.1
   condition; benign now, but note WHY the sources missed it).
3. Read the first `apply_window` DEBUG line: `anchor`/`idx` = the post-floor anchor;
   `get=1e keep=1e` encodes count+unit (first letter of `episodes`/`seasons`); `ahead=` is the
   GET set as `(season, episode)` tuples; `search_ahead=` is the subset actually searched
   (no file AND aired). Check each against your Recipe-1 derivation.
4. Read the second `apply_window` DEBUG line: `unmonitor=` count, `in_window=` size of
   GET ∪ KEEP-protected ∪ Always-Have, `queued=` queue size, `surplus=` the tuples the
   season-pack guard cancelled.
5. Deletions appear as the `actions.py` lines (or as `[dry-run] would delete ...` previews) and
   are also persisted in `deletion_log` with a `reason` of `ahead`/`keep`/`grace_watched`/
   `grace_unwatched`/`dormant`/`completed`/`normalize` — cross-check counts with Recipe 5.
6. A `GET window not armed` INFO line means step 4 of Recipe 1 gated the show: expect
   `ahead` still printed but nothing searched and the GET edge in the unmonitor batch.

Re-verify formats before quoting them elsewhere:
`grep -n "logger.debug\|logger.info" src/monitorr/engine/window.py src/monitorr/sync.py`.

## Recipe 4 — Reconstruct an incident from first principles (the archaeology method)

**Purpose**: turn "something bad happened" into ONE mechanism that explains every observation —
including the negative ones — before writing a fix. The full incident stories live in
`monitorr-failure-archaeology`; this recipe is the method, worked on the 1.5.1 anchor
regression (fix `e572eff` → v1.5.1).

**Steps**:

1. **State the observations**, positives AND negatives, from logs/CHANGELOG/DB — no
   interpretation yet.
2. **Enumerate candidate mechanisms** — every path that could produce the headline symptom.
3. **Eliminate**: a candidate survives only if it explains ALL observations. One unexplained
   negative kills it.
4. **Demand the amplifier**: if the symptom recurs or grows, the mechanism must contain the
   feedback loop explicitly.
5. **Check the fix targets the mechanism**, not a symptom — and that it explains why the fix
   ends the loop everywhere, not just on the observed path.

**Worked application** — the 1.5.1 anchor regression (fix `e572eff` → v1.5.1), in brief: after
v1.5.0's incremental sync (`c315cda`) began deriving the anchor only from the cycle's LIVE Plex
read, a fully-watched series re-downloaded its own back-catalog, `get_count` episodes per sync
cycle; the fix floors the anchor at the persisted furthest watch INSIDE `apply_window` (pinned
by the three `anchor_floor` tests in `tests/test_window.py` — Recipe 1's worked example is the
main one). The full application of these five steps — the observations table, the rival
mechanisms, and their refutations — is the worked example in `monitorr-research-methodology` §1;
the incident facts are `monitorr-failure-archaeology` INC-06.

## Recipe 5 — Invariant-checking a live/stopped instance

**Purpose**: check anchor monotonicity and floor facts directly from the SQLite DB. Executable
scripted versions live in `monitorr-diagnostics-and-tooling`; this is the manual `sqlite3` form.
Always open read-only (the DB is WAL, at `<config_dir>/monitorr.db`, default `/config`):

```
sqlite3 "file:/config/monitorr.db?mode=ro" <<'SQL'
-- Floor fact: the furthest persisted watch per show = the anchor floor apply_window enforces.
SELECT tvdb_id, season, episode, watched_at FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY tvdb_id ORDER BY season DESC, episode DESC) rn
  FROM episode_watch
) WHERE rn = 1;

-- Monotonicity probe: 'ahead' deletions AT OR BELOW the floor are evidence the window once
-- anchored below the furthest watch (the regression shape). Expect zero rows.
SELECT d.tvdb_id, d.season, d.episode, d.reason, d.created_at
FROM deletion_log d
JOIN (SELECT tvdb_id, MAX(season*10000 + episode) AS floorkey
      FROM episode_watch GROUP BY tvdb_id) f ON f.tvdb_id = d.tvdb_id
WHERE d.reason = 'ahead' AND (d.season*10000 + d.episode) <= f.floorkey;
SQL
```

Reading the probe: `season*10000 + episode` emulates the lexicographic `(season, episode)`
comparison (valid while episode numbers stay < 10000). A hit is not automatically a live bug —
compare `created_at` against the watch's `watched_at`: a deletion that PREdates the watch is
normal viewing progress; a deletion AFTER the watch was recorded is an anchor-below-floor event
and warrants Recipe 4. `episode_watch` keys are append-only (rows are upserted, never deleted:
`store.record_watch`), which is what makes the floor trustworthy.

## Recipe 6 — Equivalence arguments for refactors

**Purpose**: argue that a `window.py` refactor is behavior-preserving, branch by branch, instead
of "tests pass, ship it".

**Steps**:

1. Enumerate the decision surface. For `apply_window` it is the product of:
   `get_unit`/`keep_unit` (episodes×seasons) × `armed` (true/false) × Always-Have match
   (none/partial/whole-show) × `has_file` × aired/unaired × pre-existing `monitored` ×
   queued/not — plus the ordered special paths: anchor floor (fires / clamps / no-op), missing
   anchor abort, season-cascade re-fetch, passed-in vs fetched series/episodes, dry-run.
2. Map every branch to the test that pins it (table below). For each branch your refactor can
   reach, state WHY the new code takes the same action — cite the Recipe-1 step it implements.
3. **No test pins this branch → write it first**, against the OLD code, watch it pass, then
   refactor. A refactor merged on an unpinned branch is a behavior change in disguise.
4. Confirm the ORDER is preserved: floor → anchor resolve → season flags (+re-fetch) → GET/
   search → trims → unmonitor → queue guard. Two incidents (31fa9d5, 9a189ef) were pure
   ordering/clamping bugs — order is part of the behavior.
5. Anything that intentionally changes a branch is not a refactor: route it through
   `monitorr-change-control` (anchor changes need adversarial review + a regression test).

Branch → pinning test map (`tests/test_window.py`, verified 2026-07-02 at v1.6.1; re-verify with
`grep -n "^async def test" tests/test_window.py`):

| Branch | Pinning test |
|---|---|
| Dry-run previews, no Sonarr writes | `test_dry_run_previews_without_touching_sonarr` |
| Real mode monitor+search+delete | `test_real_mode_monitors_searches_deletes` |
| Passed-in series/episodes ≡ fetched path | `test_passed_series_and_episodes_match_fetched_path` |
| Ahead trim (preview / real+unmonitor) | `test_forward_trim_previews_ahead_deletions`, `test_forward_trim_deletes_files_and_unmonitors_in_real_mode` |
| Always-Have vs delete (partial / trim / whole show) | `test_always_have_protects_pilot_in_preview`, `test_forward_trim_respects_always_have`, `test_always_have_keeps_every_episode_monitored_without_deleting` |
| Always-Have stays monitored, rest unmonitored | `test_always_have_pilot_stays_monitored_while_others_unmonitored` |
| Season-pack queue guard | `test_cancels_season_pack_surplus_from_queue` |
| Season flags: episodes mode all-off / seasons mode window-on / idempotent skip | `test_window_unmonitors_all_seasons_in_episode_mode`, `test_window_seasons_mode_monitors_only_window_seasons`, `test_window_skips_series_put_when_seasons_already_correct` |
| Unaired: monitored, never searched | `test_unaired_ahead_episodes_are_monitored_but_not_searched` |
| Re-arm gate: gated / re-armed / gated seasons-off | `test_get_window_gated_when_inactive_past_grace`, `test_get_window_rearms_with_fresh_activity`, `test_gated_window_turns_seasons_off_in_seasons_mode` |
| Cascade re-fetch after season PUT | `test_refetches_episodes_after_season_cascade` |
| Missing anchor aborts before season writes | `test_missing_anchor_aborts_before_touching_seasons` |
| Anchor floor: fires / clamps to real / no-op | `test_anchor_floored_at_persisted_furthest_watch`, `test_anchor_floor_clamps_to_episode_sonarr_lists`, `test_anchor_floor_no_op_when_anchor_at_or_above_recorded` |

Grace-sweep branches are pinned in `tests/test_grace.py`; sync branches in `tests/test_sync.py`
(see `monitorr-validation-and-qa` for the suite map).

## When NOT to use

| You want | Go to |
|---|---|
| Fix a live symptom NOW (re-download loop, stuck sync, silent webhook) | `monitorr-debugging-playbook` |
| The canonical formulas / engine reference itself | `monitorr-window-engine-reference` |
| The full incident stories behind Recipe 4 | `monitorr-failure-archaeology` |
| Ready-made diagnostic scripts instead of manual SQL | `monitorr-diagnostics-and-tooling` |
| Run/write tests, suite layout, gates | `monitorr-validation-and-qa` |
| Merge/release the change you just proved | `monitorr-change-control` |
| Provable-correctness ambitions (property tests, simulation) | `monitorr-research-frontier` |
| The general evidence bar and worksheet discipline | `monitorr-research-methodology` |

## Provenance and maintenance

Verified against v1.6.1 (commit `c580f65`) on 2026-07-02. Re-verify before trusting:

| Fact in this skill | Re-verification command (repo root) |
|---|---|
| Recipe-1 step order matches `apply_window` | `grep -n "def apply_window" -A 200 src/monitorr/engine/window.py` |
| Anchor-floor / `_is_armed` / GET / KEEP code | `grep -n "floor\|_is_armed\|_select_ahead\|_should_delete_behind" src/monitorr/engine/window.py` |
| Worked-example test still exists and asserts {(1,1)..(1,4)} | `grep -n -A 15 "test_anchor_floored_at_persisted_furthest_watch" tests/test_window.py` |
| Log formats in Recipe 3 | `grep -n "logger.debug\|logger.info" src/monitorr/engine/window.py src/monitorr/sync.py src/monitorr/engine/actions.py` |
| `MONITORR_LOG_LEVEL` env var | `grep -n "log_level" src/monitorr/config.py src/monitorr/main.py` |
| 1.5.1 mechanism and fix text | `git show e572eff --stat` and `grep -n -A 15 "\[1.5.1\]" CHANGELOG.md` |
| Migration 4 forces a full sync | `grep -n "last_full_sync" src/monitorr/db.py` |
| DB schema used by Recipe 5 (`episode_watch`, `deletion_log`) | `grep -n "CREATE TABLE" -A 10 src/monitorr/db.py` |
| Watch rows are upserted, never deleted | `grep -n -A 8 "async def record_watch" src/monitorr/store.py` |
| Test runner is `uv run pytest` | `grep -n "pytest" .github/workflows/ci.yml` |
| Branch→test map still complete | `grep -n "^async def test" tests/test_window.py` |
