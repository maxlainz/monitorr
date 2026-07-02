---
name: monitorr-season-pack-campaign
description: Executable campaign on monitorr's hardest live problem, the season-pack grab/cancel oscillation — sync re-searches missing GET episodes, Sonarr grabs a full season pack, the queue guard cancels the surplus (killing the whole torrent), next sync repeats. Use for reproducing, measuring, or fixing the grab/cancel loop, indexer hammering, or paused torrents piling up in the download client. NOT for diagnosing other re-download loops → monitorr-debugging-playbook; NOT for incident history → monitorr-failure-archaeology; NOT for window formulas → monitorr-window-engine-reference.
---

# Season-pack grab/cancel oscillation — the campaign

This is a decision-gated campaign, not a reference. Work the phases in order; do not skip a
gate. Every EXPECTED number below is derived from the code cited next to it (verified at
v1.6.1, 2026-07-02). Anything not derivable from the repo is labeled
"unverified — measure in Phase 1".

Terms used here, defined once: **GET** = the N episodes ahead of the viewing anchor that
monitorr monitors/searches; **KEEP** = the N behind it retained on disk; **Always-Have** =
pattern-protected episodes (default `S01E01`) never deleted; **anchor** = furthest watched
episode, floored at the persisted watch store; **dry-run** = master switch (ON by default)
that turns every Sonarr write into a logged no-op; **season pack** = a single torrent
containing a whole season, which Sonarr tracks as one download but shows as one queue row
PER EPISODE; **sync** = the periodic Plex→Sonarr reconciliation in `src/monitorr/sync.py`.

## 1. Problem statement

### The mechanism, step by step

1. **Search**: for a show with missing GET episodes, each sync cycle issues one
   `EpisodeSearch` for the fileless, aired GET-window episodes
   (`src/monitorr/engine/window.py:218-220`, `search_ahead` computed at line 196), or the
   Wanted/Missing re-search pass does (`src/monitorr/sync.py:322-334`).
2. **Pack grab**: when the indexers only carry season packs for that show, Sonarr answers
   the episode search by grabbing a full season pack. One torrent → one tracked download →
   one queue row per episode of the pack.
3. **Guard cancels the surplus**: at the end of the next `apply_window` for that show, the
   season-pack guard fetches the queue and cancels every queued episode outside
   GET ∪ KEEP-protected ∪ Always-Have (`src/monitorr/engine/window.py:268-282`, cancel in
   `src/monitorr/engine/actions.py:73-89`), with `removeFromClient=false&blocklist=false`
   (`src/monitorr/sonarr/client.py:204-221`).
4. **The whole torrent dies**: `DELETE /api/v3/queue/{id}` cancels the *tracked download*,
   not one episode. Queue rows are per-episode but map to ONE torrent, so cancelling a
   surplus row also drops the pack's **in-window** episodes from the queue. Nothing from the
   pack ever imports. The torrent stays behind in the client (paused/seeding).
5. **Repeat**: the GET episodes still have no file and have aired, so the next cycle's
   window searches them again (`window.py:196` filters only on `not e.has_file and
   e.has_aired()` — a cancelled grab does not suppress the re-search). Sonarr grabs the same
   or an equivalent pack. Go to step 3. Period ≈ one sync interval
   (`MONITORR_SYNC_INTERVAL`, default 21600 s = 6 h, `src/monitorr/config.py:26`), shorter
   if live watching triggers `apply_window` via the poller.

Timing note: within a single `apply_window` the search fires (line 218-220) BEFORE the
queue is fetched (line 272), so the grab provoked by cycle N usually appears in the queue —
and gets cancelled — during cycle N+1. In steady state every cycle shows both a re-search
and a cancel batch for the same show.

### Why nothing surplus ever imports (the guard always wins)

The guard runs at the end of every `apply_window` — sync, poller and webhook paths all
funnel through it (`window.py:86` is the single entry point). Sonarr imports a download only
after it *completes*; a pack large enough to matter takes longer than the guard's revisit
period (poller trigger 30 s during live viewing; sync 6 h otherwise) (unverified —
Sonarr/client runtime behavior; confirm in Phase 0/1). And even if a pack
completed and imported between visits, the ahead/behind trims (`window.py:226-249`) delete
the surplus files immediately after. Correctness holds; the loop is a pure waste problem.

### The real costs

- **Indexer hammering**: one guaranteed-futile `EpisodeSearch` per affected show per cycle,
  forever. With several affected shows this is a standing load on your indexers (and a
  ban/rate-limit risk on private trackers — unverified, indexer-dependent).
- **Download-client accumulation**: `removeFromClient=false` means each cancelled grab
  leaves a torrent seeding/paused in the client. Sonarr forgets it; nothing cleans it up.
  They accumulate one per cycle per affected show until the user intervenes.
- **Wasted bandwidth**: each grab downloads part (sometimes most) of a multi-GB pack before
  the cancel drops it (unverified — Sonarr/client runtime behavior; confirm in Phase 0/1).

### Why it was ACCEPTED in 1.6.1, not fixed

Commit `eb9c1c1` ("docs: changelog for the deep-dive fixes; document two accepted
trade-offs") documented it deliberately, without code changes: *"DELETE /queue/{id} cancels
the whole tracked download, so the season-pack guard can enter a grab/cancel cycle when only
packs exist (preferred over importing hundreds of GB first)"*. The alternative —
import-then-trim — is the pre-1.4.1 world in which season packs landed hundreds of GB on
disk before anything trimmed them (see §5a). The docs of record carry the same acceptance,
`.claude/sonarr.md` (Pitfalls):

> **`DELETE /api/v3/queue/{id}` cancels the whole download, not one episode**: queue rows
> are per-episode but map to one tracked download (one torrent), so the season-pack guard
> pulling a *surplus* episode also drops the **in-window** episodes of that same pack from
> the queue. Known trade-off: when only season packs exist for a show, the next sync can
> re-search → re-grab → re-cancel (the guard always wins, nothing surplus is ever imported,
> but the grab/cancel cycle repeats and the client accumulates the paused/seeding torrent).
> Accepted for now: the alternative — letting the pack import and trimming after — would
> land hundreds of GB on disk first.

"Accepted" means "the safe side of the trade-off was chosen", not "closed". This campaign
exists to close it without reopening the disk-flood side.

## 2. Phase 0 — Reproduce / observe

Preconditions: a live instance with Plex + Sonarr linked, **dry-run OFF** (with dry-run ON,
`search_episodes` and `cancel_downloads` are both no-ops — `actions.py:66-70,80-82` — so the
oscillation cannot occur; you can only observe it on a real, opted-in instance).

1. Enable DEBUG logging and restart. In `docker-compose.yml` set the env var, then:

   ```
   docker compose up -d monitorr
   ```

   with `- MONITORR_LOG_LEVEL=DEBUG` under `environment:` (`src/monitorr/config.py:20`;
   format `%(asctime)s %(levelname)s %(name)s: %(message)s`, `src/monitorr/logging.py:4-8`).

2. Identify an affected show: one whose missing GET episodes exist on your indexers **only
   inside season packs** (check Sonarr → Series → the season → Interactive Search: every
   result is a multi-episode/full-season release). Confirm the signature in Sonarr →
   Activity → History: repeated "Grabbed" events for the same season pack with no matching
   "Imported".

3. Follow the logs and force one cycle:

   ```
   docker compose logs -f monitorr
   ```

   and in another shell (the manual sync button; forces a FULL sync,
   `src/monitorr/web/routes.py:487-494`):

   ```
   curl -s -o /dev/null -X POST http://localhost:8080/sync
   ```

4. Watch one full cycle for the affected show (find its `tvdb=` id in the sync line).

### EXPECTED at the gate (per affected show, per steady-state cycle)

| # | Observation | Expected | Derivation |
|---|---|---|---|
| 1 | DEBUG line `apply_window tvdb=... anchor=... ahead=[...] search_ahead=[...]` | exactly 1, with `search_ahead` = the missing GET episodes | `window.py:197-211` |
| 2 | `EpisodeSearch` command batches for those episode ids | exactly **1** per cycle (the window's; the re-search pass skips ids a window searched this cycle AND ids already queued) | `window.py:218-220` + `sync.py:322-334` (`already_searched`, `queued`); locked by `tests/test_sync.py:187` `test_resync_pass_skips_episodes_the_window_just_searched`. Note: a real (non-dry-run) search emits NO monitorr log line (`actions.py:62-70`) — count grabs in Sonarr's History, or infer from observation #1 |
| 3 | DEBUG line `apply_window tvdb=... unmonitor=N in_window=N queued=N surplus=[...]` | exactly 1; `queued` counts the pack's rows, `surplus` lists the out-of-window `(season, episode)` tuples | `window.py:274-281` |
| 4 | INFO cancel lines | exactly **one per surplus queue row**, format: `queued download cancelled (episodeId=<id>); still seeding` (logger `monitorr.engine.actions`) | `actions.py:84-89`. In dry-run you would instead see one line: `[dry-run] would cancel queued downloads of N episodes` (`actions.py:81`) |
| 5 | Sonarr queue after the cycle | the pack's rows gone (in-window rows of the same torrent vanish too — one tracked download) | `.claude/sonarr.md` Pitfalls; `client.py:204-221` |
| 6 | Download client | one more paused/seeding torrent than before the cycle | `removeFromClient=false`, `actions.py:87` |

Caveat: `get_queue` requests `pageSize=1000` with **no pagination**
(`src/monitorr/sonarr/client.py:196-201`). A queue over 1000 rows silently hides items from
the guard — if your queue is that large, drain it before trusting observation #3.

### Gate decisions

- **2+ `EpisodeSearch` batches for the same ids in one cycle** → that is the pre-`99ba5fc`
  double-search bug (fixed in v1.6.1, "Search hygiene"). Check your version
  (`grep version pyproject.toml`, or the UI footer); upgrade before continuing.
- **You see the cancel but the surplus episodes still import** → the guard is broken. STOP.
  This is a regression, not the campaign's problem — route to `monitorr-debugging-playbook`
  and `monitorr-failure-archaeology` (the guard was born in `4245d21`).
- **No re-grab at all** (search fires, queue stays empty or fills with single episodes) →
  your indexers have single-episode releases for this show; it does not reproduce the
  problem. Find another show or accept you cannot reproduce locally.
- All six observations match → proceed to Phase 1.

## 3. Phase 1 — Measure

Pick K ≥ 4 consecutive sync cycles (≥ 1 day at the default 6 h interval). Count:

| Metric | How | Command |
|---|---|---|
| Cancelled grabs per show per day | monitorr logs | `docker logs monitorr 2>&1 \| grep -c "queued download cancelled"` (per-show: correlate the preceding DEBUG `apply_window tvdb=... surplus=` line) |
| Distinct oscillation cycles | monitorr logs | `docker logs monitorr 2>&1 \| grep "surplus=\[(" ` — each non-empty `surplus` on a guard line is one cycle iteration |
| Torrents left in client | download-client UI/API | count paused/seeding torrents matching the pack's release name (unverified — client-specific, measure in Phase 1) |
| Indexer searches issued | Sonarr → System → Logs / History | count "Grabbed" events per show per day for the same release (Sonarr-side; monitorr does not log real searches) |
| Bandwidth wasted | client's per-torrent downloaded bytes before removal | unverified — client-specific, measure in Phase 1 |

**Instrumentation gap (verify, then plan around it)**: `deletion_log` does NOT record
cancels. Verified: `actions.cancel_downloads` (`actions.py:73-89`) calls no `store.*`
function; `store.record_deletion` is called only from `delete_episode`
(`actions.py:159,179`), and the only `reason` values ever written are `ahead`, `keep`,
`normalize`, `completed`, `dormant`, `grace_watched`, `grace_unwatched`. Confirm on your DB:

```
sqlite3 "file:./config/monitorr.db?mode=ro" "SELECT DISTINCT reason FROM deletion_log;"
```

Measurement is therefore **log-based only**, and Docker log rotation can eat your baseline.
The campaign may first need to close this gap — a persisted cancel record (new table or a
counter keyed by tvdb_id) is a small, low-risk change and makes the success criterion in §6
checkable without log archaeology. That change itself goes through
`monitorr-change-control`.

Record the baseline numbers before touching anything. A fix you cannot show moving these
numbers is not a fix.

## 4. Ranked solution menu

Any chosen solution MUST add regression tests and MUST go through
`monitorr-change-control`'s anchor-adjacent adversarial review — `window.py` is a trigger
file (the incident list shows why: `dd889ee`, `4245d21`, `e572eff`, `31fa9d5` all live in or
around it). Argue your change against the full incident catalog before writing it.

### Rank 1 — (a) Per-show search backoff

- **Mechanism**: when the guard cancels a grab for a show, persist a cooldown (e.g.
  `search_backoff_until` keyed by tvdb_id); while it runs, `apply_window` still monitors the
  GET window but skips the search, and `_reconcile_managed`'s re-search skips the show.
- **Derivation obligations**: read where each search decision is made — `window.py:218-220`
  (`armed and policy.search_on_get and search_ahead`) and `sync.py:322-334` — and confirm
  both paths consult the new state; needs a new persisted key/table (append-only migration,
  see `monitorr-architecture-contract`) and a place to set it (the guard, when
  `surplus` is non-empty). Decide the cooldown unit (cycles vs hours) and expiry.
- **Expected effect**: cancelled grabs per show per day drop from ~4 (one per 6 h cycle) to
  ≤ 24 h / cooldown; torrent accumulation and indexer load drop proportionally. The loop
  still exists, but bounded.
- **Risk**: LOW. Delays legitimate single-episode releases that appear during the cooldown
  (bounded by the cooldown, self-heals). No import-path change, guard untouched.

### Rank 2 — (c) Blocklist the cancelled release

- **Mechanism**: cancel the surplus with `blocklist=true` so Sonarr stops re-grabbing THAT
  release; the next search either finds a different (single-episode) release or nothing.
- **Derivation obligations**: `delete_queue_item` already accepts `blocklist`
  (`client.py:204-221`, currently passed `False` from `actions.py:87`) — the diff is small.
  BUT you must confirm against Sonarr (docs or a live test): that the blocklist is
  per-release (unverified — measure in Phase 1) and that it will not block wanted single
  episodes from the same release group (unverified — measure in Phase 1). Also decide
  whether to blocklist only when the whole grab is pack-shaped (multiple queue rows sharing
  one download) — `QueueItem` currently parses only `id` and `episodeId`
  (`client.py:189-194`), so detecting "same torrent" needs an extra field (e.g.
  `downloadId`) — a client model change.
- **Expected effect**: cancelled grabs drop to ~1 per release variant (each pack blocked
  once); torrent accumulation stops for blocked releases.
- **Risk**: MEDIUM. If the pack is the ONLY source, blocklisting starves the show — the GET
  promise silently unfulfilled with no signal to the user. Mitigate by pairing with (a) or
  by surfacing blocked shows in the UI. Also pollutes user-visible Sonarr state.

### Rank 3 — (b) Season-mode escape hatch

- **Mechanism**: if the GET window covers the whole remaining season (trivially true in
  seasons mode with `get_count ≥ 1`, `window.py:59-63`), every pack episode ahead of the
  anchor is in-window — let the pack land instead of cancelling. The guard already spares
  in-window rows; the surplus in this case is only the *behind* episodes outside
  KEEP ∪ Always-Have, and cancelling those still kills the torrent. The hatch would tolerate
  that bounded behind-surplus and let the import-then-trim path (the ahead/behind trims,
  `window.py:226-249`) delete it right after import.
- **Derivation obligations**: verify the KEEP/Always-Have interplay — compute exactly which
  keys `keep_protected_keys` (`window.py:73-83`) protects for the affected show and bound
  the surplus that would import (it must be "a few episodes", never "hundreds of GB");
  verify the trims actually fire on the import path (they run on every `apply_window`);
  verify grace sweeps won't fight the imported surplus (see `_is_armed`, `window.py:38-50`).
- **Expected effect**: oscillation ends for shows whose window spans the pack; the pack
  imports once.
- **Risk**: MEDIUM-HIGH. Deliberately violates exact-window semantics (bounded, but a
  precedent), and any bounding mistake reopens the pre-1.4.1 disk flood. Needs the
  adversarial review more than any other option.

### Rank 4 — (d) SeasonSearch when >X% of a season is in the GET window

- **Mechanism**: issue `SeasonSearch` (`{name, seriesId, seasonNumber}`) instead of
  per-episode `EpisodeSearch`, telling Sonarr up front it is fetching a season — better
  release selection when packs are the natural unit.
- **Derivation obligations**: `.claude/sonarr.md` (Search / download) documents
  `SeasonSearch` as an alternative, but the code does NOT expose it — verified:
  `grep -rn SeasonSearch src/ tests/` is empty; `search_episodes`
  (`client.py:172-180`) only sends `EpisodeSearch`. This is a new client method + new
  action. You must also verify what Sonarr grabs when a `SeasonSearch` season is only
  partially monitored (unverified — measure in Phase 1).
- **Expected effect**: alone, almost none — the grabbed pack still contains out-of-window
  episodes and the guard still cancels it. Only useful as a companion to (b): search at the
  season level *when the hatch would let the pack land anyway*.
- **Risk**: LOW alone (it changes nothing), MEDIUM combined with (b) (inherits its risks).

**Why this ranking**: (a) is the only option that bounds all three measured costs with zero
change to import/delete behavior — the failure mode is "slower", never "wrong". (c) attacks
the root re-grab but adds a silent-starvation mode and depends on unverified Sonarr
blocklist semantics. (b) actually *solves* the problem for the pack-shaped case but trades
against the very flood the guard exists to prevent — acceptable only with a proven bound.
(d) is an enabler, not a fix. A plausible end state is (a) + (c) with a UI signal, or
(a) + (b)(+d) after the bound is proven. Whatever you pick: regression tests first, then
`monitorr-change-control`'s anchor-adjacent review.

## 5. FENCED-OFF wrong paths (do not revisit without new evidence)

| Path | Why it is fenced off | Evidence |
|---|---|---|
| (a) "Let the pack import and trim after" (unconditionally) | This is the pre-1.4.1 world. Season packs landed whole seasons — "hundreds of GB" — before anything trimmed them; the guard was added in `4245d21` (v1.4.1) precisely to stop surplus **before it reaches disk**, and `eb9c1c1` records that the oscillation is "preferred over importing hundreds of GB first". §4(b) differs only by a *proven bound*; without one, this is the same mistake | `4245d21`, `eb9c1c1`, `dd889ee` context |
| (b) `removeFromClient=true` on the cancel | Kills the torrent in the client mid-seed — punishes private-tracker users (ratio damage, possible H&R penalties) to clean up monitorr's own mess. The current behavior is deliberate and test-locked | `actions.py:87`; `tests/test_actions.py:101` `test_cancel_downloads_removes_from_queue_keeping_seed` asserts `removeFromClient=false` |
| (c) "Unmonitor the missing episodes to stop the search" | Breaks the monitoring invariant `monitored ≡ (GET window if armed else ∅) ∪ Always-Have` — the GET promise is exactly "these episodes get fetched". It also doesn't work: every `apply_window` re-monitors the GET window (`window.py:214-216`), so the unmonitor lasts one cycle. If you want to suppress the *search* without breaking the promise, that is §4(a) | `dd889ee` established the invariant; `monitorr-architecture-contract` |
| (d) "Re-monitor everything so the pack is 'wanted' and imports cleanly" | Recreates the v1.2.0 season-pack-upgrade bug: the all-episodes-monitored state is precisely what lets Sonarr grab season-pack "upgrades" of episodes that will never be re-watched (hundreds of GB). The unmonitor-while-keeping-file design exists to make that state unreachable | `dd889ee` (v1.2.0); `.claude/sonarr.md` Pitfalls ("Season packs"); locked by `tests/test_window.py:250` `test_always_have_pilot_stays_monitored_while_others_unmonitored` |

Guard behaviors locked by tests (breaking any of these = regression, not iteration):
`tests/test_window.py:275` `test_cancels_season_pack_surplus_from_queue` (surplus cancelled,
in-window queue rows spared), `tests/test_actions.py:101`
`test_cancel_downloads_removes_from_queue_keeping_seed` (`removeFromClient=false`, only the
matching queue row), `tests/test_actions.py:120` `test_cancel_downloads_dry_run_makes_no_calls`
(dry-run gate), `tests/test_sync.py:187`
`test_resync_pass_skips_episodes_the_window_just_searched` (one search batch per cycle),
`tests/test_window.py:405` `test_unaired_ahead_episodes_are_monitored_but_not_searched`
(search hygiene), `tests/test_window.py:430` `test_get_window_gated_when_inactive_past_grace`
(re-arm gate).

## 6. Promotion protocol

1. **Dry-run first**: flip dry-run ON (Settings) with your change deployed.
   `cancel_downloads` is dry-run-gated — verified at `actions.py:80-82` and locked by
   `tests/test_actions.py:120` — as is `search_episodes` (`actions.py:66-70`), so dry-run
   pauses the whole loop and lets you read the window's *intended* decisions
   (`[dry-run] would ...` lines, plus the DEBUG `apply_window` lines) without any write.
2. **Staged rollout**: enable the behavior for ONE affected show first. Per-series policy
   editing exists (`src/monitorr/web/routes.py:441` `save_series_policy`) — if your change
   adds a flag, make it per-series-overridable so the stage is possible.
3. **Expected-numbers re-check**: rerun Phase 0's table and Phase 1's counts on the staged
   show. The fix must move the measured numbers in the predicted direction AND leave
   observations #1/#5-equivalents intact for unaffected shows (no surplus imports, no
   in-window episode starved).
4. **Docs + changelog in the same change**: update `.claude/sonarr.md`'s accepted-trade-off
   paragraph (it stops being "accepted for now") and write the CHANGELOG entry
   cause → mechanism → fix. Route the whole change — code, tests, docs, release — through
   `monitorr-change-control` (anchor-adjacent review is mandatory; `window.py` is a trigger
   file).
5. **Falsifiable success criterion** — you have a result when, over 7 days on an affected
   show:
   - cancelled-grab count (Phase 1 metric) drops to ≤ 1, AND
   - no surplus episode imports (deletion_log shows no unexpected `ahead`/`keep` deletions
     for that show; Sonarr History shows no surplus "Imported"), AND
   - no in-window episode is starved (every missing GET episode either imports or is
     demonstrably unavailable as a single on your indexers).

   Any of the three failing = not done; document the retirement or iterate (see
   `monitorr-research-methodology` for the evidence bar).

## When NOT to use this skill

- Surplus episodes ARE importing, or any other re-download/deletion misbehavior →
  `monitorr-debugging-playbook` (this campaign assumes a correctly working guard).
- You want the incident history behind the guard → `monitorr-failure-archaeology`.
- You need the window/KEEP/Always-Have formulas → `monitorr-window-engine-reference`.
- You need the Sonarr/Plex endpoint tables → `monitorr-plex-sonarr-reference`.
- You are ready to commit/release a fix → `monitorr-change-control` (mandatory anyway).
- Env vars / policy fields referenced here → `monitorr-config-and-flags`.

## Provenance and maintenance

Verified against v1.6.1 (2026-07-02). Before trusting a fact here, re-verify:

| Fact | Re-verification command |
|---|---|
| Guard location and in-window set | `grep -n "Season-pack guard" -A 16 src/monitorr/engine/window.py` |
| Cancel log format + no store write | `grep -n "cancelled\|store\." src/monitorr/engine/actions.py` |
| `delete_queue_item` params (blocklist available) | `grep -n "removeFromClient\|blocklist" src/monitorr/sonarr/client.py` |
| `get_queue` pageSize=1000, no pagination | `grep -n "pageSize" src/monitorr/sonarr/client.py` |
| Re-search dedup (`already_searched`, `queued`) | `grep -n "already_searched" src/monitorr/sync.py` |
| SeasonSearch still not implemented | `grep -rn "SeasonSearch" src/ tests/` (empty → still true) |
| Accepted trade-off still documented | `grep -n "grab/cancel" .claude/sonarr.md CHANGELOG.md` |
| Trade-off provenance | `git show eb9c1c1 --stat` ; `git show 4245d21 --stat` ; `git show dd889ee --stat` |
| Locked tests still present | `grep -rn "test_cancels_season_pack_surplus\|test_cancel_downloads_\|test_resync_pass_skips" tests/` |
| deletion_log reasons (no cancel reason) | `grep -rn 'delete_episode(' src/monitorr \| grep -v def` |
| Sync interval default | `grep -n "sync_interval" src/monitorr/config.py` |

If the guard code, `cancel_downloads`, or the re-search pass has changed since v1.6.1,
re-derive the Phase 0 expected numbers from the current code before running the campaign.
