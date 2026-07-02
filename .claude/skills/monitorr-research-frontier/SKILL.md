---
name: monitorr-research-frontier
description: >-
  The provable-correctness research frontier: candidate verification work (Hypothesis property
  tests, Plex/Sonarr divergence simulator, anchor state-machine proof, sync state-space
  enumeration, mutation testing) plus external positioning — and the CLAIMS-EVIDENCE RULE (no
  public capability claim without a named in-repo test). Use when planning new correctness work,
  writing README/release-note/positioning claims, or comparing monitorr to other *arr tools. NOT
  for running/writing today's tests → monitorr-validation-and-qa; NOT for the evidence bar of a
  live investigation → monitorr-research-methodology.
---

# monitorr research frontier

This skill defines what "beyond state of the art" means for this repo and lists the candidate
work items that get there. It is also the home of the **claims-evidence rule** governing every
public capability claim.

The maintainer's definition of the frontier (2026-07-02): **PROVABLE CORRECTNESS — zero
re-download incidents ever again**. Invariants stated formally, property-style tests, simulation
of Plex/Sonarr divergence. The frontier is reliability no other *arr companion has — not feature
scope, not adoption. (*arr = the Sonarr/Radarr app family; monitorr is a companion service that
drives Sonarr over its API.)

"State of the art" (SOTA) here means two things, and every item below must beat both:

1. **This repo's current test suite**: 108 example-based tests as of v1.6.1, 2026-07-02
   (count: `grep -c "def test_" tests/*.py | awk -F: '{s+=$2} END {print s}'`), HTTP faked with
   respx, mypy strict over `src` and `tests`. Excellent regression coverage of *known* failures.
2. **The *arr ecosystem's typical practice**: example-based tests that replay known bugs. No
   mainstream *arr companion property-tests its deletion logic or simulates source divergence.

Every item below is **candidate/open — none is implemented**. Verified 2026-07-02: `hypothesis`,
`mutmut` and `cosmic-ray` appear nowhere in `pyproject.toml` or `uv.lock`, and `tests/` contains
no property-based, stateful, state-space or mutation tests. Do not describe any of this as
existing capability.

Terms used below (defined once): the **anchor** is the episode the window centers on (the
furthest watched); **GET** = episodes kept monitored ahead of the anchor; **KEEP** = episodes
kept on disk behind it; **Always-Have** = pattern-protected episodes (e.g. the pilot) never
deleted; **grace** = time-deferred deletion sweeps; **dry-run** = the master switch (ON by
default) that turns all Sonarr writes into recorded previews; **watermark** = the newest Plex
history timestamp already scanned, floor of the next incremental sync; a **full sync** rescans
every show, an **incremental** only shows with new plays; a **season cascade** is Sonarr
propagating a season-level `monitored` flag to its episodes; a **ratingKey** is Plex's per-item
id (not stable across re-adds); **respx** is the pytest library that fakes httpx traffic;
**season pack** = one torrent containing a whole season.

## The claims-evidence rule (binding — this skill is its home)

> **No public capability claim ships without a NAMED in-repo test that demonstrates it.**
> "Public" = README.md, release notes, positioning/comparison text, the Docker Hub/GHCR
> description. The claim must cite the test (file + test function name). If the test does not
> exist, the claim does not ship — write the test first or don't make the claim.

Checklist before shipping any capability sentence:

- [ ] Name the exact test(s): `tests/<file>.py::<test_function>`.
- [ ] Run it: `uv run pytest tests/<file>.py::<test_function> -q` — green on the commit that
      ships the claim.
- [ ] The test asserts the *claimed behavior*, not a proxy (a test that the code runs is not a
      test that it never re-downloads).
- [ ] Claims about *absence* of behavior ("never X") need a test that sets up the tempting
      condition and asserts X did not happen.
- [ ] The claim's wording does not exceed what the test shows (a single-scenario test does not
      support the word "always" — say what was tested, or broaden the test).

Worked example — a claim that IS backed today. README.md line 33 claims the sync "jumps the
window straight to your **last watched episode** — it doesn't re-download the series from the
pilot". Backing tests, verified 2026-07-02:

| Claim facet | Named test |
|---|---|
| Anchor never slides below the persisted furthest watch | `tests/test_window.py::test_anchor_floored_at_persisted_furthest_watch` (line 578) |
| Incremental sync cannot regress below the recorded max | `tests/test_sync.py::test_incremental_sync_does_not_regress_below_recorded_max` (line 812) |
| Floor survives episodes Sonarr doesn't list | `tests/test_window.py::test_anchor_floor_clamps_to_episode_sonarr_lists` (line 594) |

Worked example — a hypothetical claim that would need a new test first. Suppose a release note
wanted to say: *"monitorr handles shows that only have season-pack releases without repeated
grab/cancel cycles."* This claim MUST NOT ship today: (a) no test in `tests/` exercises a
repeated sync against a season-pack-only fake indexer, and (b) the behavior is currently the
documented grab/cancel oscillation trade-off (see `monitorr-season-pack-campaign`). The rule
blocks it twice over. Shipping it requires the campaign to land a fix *plus* a named multi-cycle
test (e.g. a future `tests/test_season_pack.py::test_pack_only_show_reaches_steady_state`) —
then, and only then, the sentence may appear, citing that test.

Positioning corollary: comparison statements against other tools ("unlike X, monitorr …") count
as capability claims about monitorr and fall under the same rule. Claims about *other* tools'
behavior are out of scope for tests — avoid them entirely; state only what monitorr does, with
its test.

## Frontier items

Each item follows the same structure: why SOTA falls short → the repo asset that makes it
tractable (verified) → the first three steps in this repo → a falsifiable result criterion.
Every item is **candidate/open**. All of them land through `monitorr-change-control` (anything
touching anchor/sync/window code needs adversarial review against the incident list plus a
regression test). New dev dependencies are added with `uv add --group dev <pkg>` from the repo
root — this updates `pyproject.toml` and `uv.lock` together; CI installs with
`uv sync --frozen` (see `monitorr-build-and-env` for the lockfile rule).

### Item 1 (candidate) — Property-based testing of the window invariants with Hypothesis

**Why SOTA falls short.** The 108 tests are examples: fixed episode lists (E1–E7), fixed
policies (GET=1/KEEP=1), fixed watch histories. Four separate incidents (dd889ee, 20d578e,
e572eff, 9a189ef — one-liners in `monitorr-failure-archaeology`) were each an input combination
no example had anticipated. Example tests prove "these cases work"; they cannot prove "no case
breaks". No *arr companion does better.

**Repo asset.** The decision core is pure — plain data in, plain data out, no I/O — so it is
directly generatable, verified in `src/monitorr/engine/window.py` and `engine/policy.py`:

| Function | Signature (verified) | Location |
|---|---|---|
| `_select_ahead` | `(real: list[SonarrEpisode], idx: int, policy: Policy) -> list[SonarrEpisode]` | window.py:59 |
| `_should_delete_behind` | `(real: list[SonarrEpisode], idx: int, policy: Policy, i: int) -> bool` | window.py:66 |
| `keep_protected_keys` | `(real: list[SonarrEpisode], anchor_idx: int, policy: Policy) -> set[tuple[int, int]]` | window.py:73 |
| `_real_episodes` | `(episodes: list[SonarrEpisode]) -> list[SonarrEpisode]` | window.py:53 |
| `matches_always_have` | `(patterns: list[str], season: int, episode: int) -> bool` | policy.py:92 |

`SonarrEpisode` and `Policy` are pydantic models (`sonarr/client.py:36`, `engine/policy.py:24`)
— `hypothesis.strategies.builds` constructs both directly, and `Policy`'s field constraints
(`ge=0`, `Unit = Literal["episodes", "seasons"]`) define the generation bounds for free.

**First three steps.**
1. `uv add --group dev hypothesis` (hypothesis ships type hints; mypy strict covers `tests`, so
   typed strategies are mandatory, not optional).
2. Create `tests/test_window_properties.py` with two strategies: episode lists (sorted, unique
   `(season_number, episode_number)`, seasons 0–5 so S00 exclusion is exercised) and `Policy`
   instances via `st.builds(Policy, get_count=st.integers(0, 10), ...)`.
3. Target `keep_protected_keys` first — it is shared by the window and the grace sweep, so one
   property protects two write paths. Assert, for all generated `(real, anchor_idx, policy)`:
   the anchor's key is always in the returned set; the set never contains a key at an index
   greater than `anchor_idx`; and no key for which `_should_delete_behind` is True is in the set.

Properties must be *relational*, not restatements of the code (asserting
`ahead == real[idx+1 : idx+1+get_count]` is a tautology). Good relational invariants to encode,
in their canonical form (formulas owned by `monitorr-window-engine-reference`): the anchor floor
(`floor = max((w.season, w.episode) for recorded watches that are in real_keys,
default=(season, episode))`; if `floor > (season, episode)` re-anchor at floor), the monitoring
invariant (`monitored ≡ (GET window if armed else ∅) ∪ Always-Have`), the KEEP floor
(protected keys ∩ deleted keys = ∅), Always-Have inviolability (a matched episode never appears
in any delete decision), and S00 exclusion (`_real_episodes` output contains no
`season_number < 1`).

**You have a result when** the property suite runs green in CI over its generated cases
(`uv run pytest tests/test_window_properties.py`) *or* Hypothesis shrinks a minimal
counterexample — either outcome is a result; a found counterexample is a prevented incident.
The item is falsified if the invariants cannot be stated over the pure functions without mocking
I/O (that would mean the decision core is not actually pure — re-check the signatures above).

### Item 2 (candidate) — Plex/Sonarr divergence simulator (model-based testing)

**Why SOTA falls short.** Every serious incident in this repo's history was a *divergence*
between what Plex knows and what Sonarr holds: files deleted under a recorded watch (4245d21),
re-adds with new ratingKeys (5ce7fab), season cascades invalidating snapshots (31fa9d5),
numbering mismatches (9a189ef), server switches with stale watermarks (beb757c). Example tests
replay each known divergence once; none *searches* for unknown interleavings of them. The
meta-pattern (see `monitorr-failure-archaeology`) is that the anchor invariant had to be
re-asserted on four paths before it held — exactly the signature of a system whose event
interleavings were never explored systematically.

**Repo asset.** Two assets. (a) The respx harness already fakes both sides at the HTTP level:
`tests/test_window.py:_mock_sonarr` (line 72) builds a routed fake Sonarr, and `tests/test_sync.py`
fakes the Plex library/history endpoints — a stateful simulator is these fakes with a dict
behind them instead of canned JSON. (b) The forensic incident corpus with commit hashes is a
ready-made vocabulary of divergence event classes to seed the generator: file deletion under a
watch (4245d21), series re-add with new ratingKey (5ce7fab), season cascade (31fa9d5), watch
Sonarr doesn't list / numbering mismatch (9a189ef), history endpoint outage (beb757c), server
switch (beb757c), grace-driven trim vs. sync re-arm (c72357d), watched-but-still-monitored state
(dd889ee). All eight hashes verified in `git log`.

**First three steps.**
1. Create `tests/test_divergence_sim.py` with two stateful fakes: `FakeSonarr` (dict of episodes
   with `monitored`/`has_file`, a queue list; mounted as respx `side_effect` callables so state
   mutates across calls) and `FakePlex` (library items keyed by ratingKey, a history list keyed
   by `grandparentTitle`).
2. Add `hypothesis` (same dependency as Item 1) and write a
   `hypothesis.stateful.RuleBasedStateMachine` whose rules are the eight event classes above
   plus the monitorr triggers: `run_sync`, `run_poller_watch`, `run_grace_sweep`.
3. Implement the machine-checked invariant as the state machine's `invariant()`: **no episode
   whose key has ever been recorded as watched in the run is ever re-searched** — checked by
   capturing every `POST /api/v3/command` (`EpisodeSearch`) payload in `FakeSonarr` and
   intersecting its episode ids with the watched set. Start with dry-run OFF in the fixture so
   writes actually flow through `engine/actions.py`.

**You have a result when** (a) the machine runs its random event sequences on HEAD with zero
invariant violations, AND (b) locally reverting one historical fix (e.g.
`git revert --no-commit e572eff` in a scratch worktree — never commit this) makes the machine
re-find that incident as a failing sequence. (b) is the falsifiability check on the simulator
itself: a simulator that cannot rediscover a known incident when its fix is removed proves
nothing when it passes.

### Item 3 (candidate) — Formal statement + machine-checked proof of the anchor state machine

**Why SOTA falls short.** Anchor monotonicity — the load-bearing invariant, see
`monitorr-architecture-contract` — is enforced on four distinct code paths that were each fixed
*separately across four releases* (live 351bc8a, history 4245d21→5ce7fab, sync e572eff, grace
sweep 9a189ef; hashes verified). Today each path has its own example tests, but nothing states
the invariant once and checks all four paths against the *same* statement. A fifth path added
tomorrow inherits no protection.

**Repo asset.** The four paths converge on identifiable, verified code points, all reading the
same persisted authority (`store.record_watch`/`store.get_watches`):

| Path | Enforcement point | Verified location |
|---|---|---|
| Poller | `process_watch` anchors on `max(recorded watches)` then `apply_window` floors | `src/monitorr/plex/poller.py:35-46` |
| Webhook | shares `process_watch` (docstring: "Shared by poller and webhook") | `src/monitorr/plex/poller.py:35-46` |
| Sync | per-show anchor = max merged watch, then `apply_window` floors | `src/monitorr/sync.py:213-231` |
| Grace sweep | KEEP-floor anchor = max recorded watch clamped to Sonarr-listed keys | `src/monitorr/engine/grace.py:87-95` |

`apply_window` enforces the floor internally "so every caller (sync, poller, webhook) is
protected" (`engine/window.py:121-142`) — the proof target is that this sentence is true and
stays true.

**First three steps.**
1. Create `tests/test_anchor_paths.py` opening with the formal statement as a module docstring:
   *for every entry path P ∈ {poller, webhook, sync, grace} and every set W of recorded watches,
   no Sonarr write issued by P targets an episode whose key is ≤ max(W ∩ real_keys), except
   unmonitoring-while-keeping-file as permitted by the monitoring invariant.*
2. Build ONE shared fixture (respx fake Sonarr + seeded `store.record_watch` rows, patterned on
   `tests/test_window.py:_mock_sonarr`) and parametrize a single test over the four entry
   points: `poller.process_watch(...)`, the webhook route (`POST /webhook/plex/{secret}`, see
   `tests/test_app.py::test_webhook_accepts_valid_secret` for the harness), `sync.run_sync()`,
   and `grace.sweep()`.
3. Assert identically for all four: captured search/delete/monitor payloads never touch keys at
   or below the floor that the statement forbids.

**You have a result when** the parametrized test passes for all four paths from one fixture,
AND deleting the floor block (`engine/window.py:130-142`) plus the grace clamp
(`engine/grace.py:90`) in a scratch tree makes **all four** parametrizations fail. If any path
still passes with the floor removed, that path was not actually covered — the test, not the
code, is wrong. That mutation check is the falsifiability criterion.

### Item 4 (candidate) — Exhaustive state-space test of the sync state machine

**Why SOTA falls short.** The sync's full-vs-incremental decision and its post-state writes form
a small state machine, but today's tests sample ~7 named points of it
(`tests/test_sync.py::test_sync_first_run_is_full_then_incremental_skips_unchanged`,
`::test_force_full_rescans_every_show`, `::test_incremental_promotes_to_full_when_history_unavailable`,
`::test_failed_history_advances_neither_watermark_nor_full_stamp`,
`::test_full_sync_due_tracks_the_rolling_floor`, `::test_migration_clears_last_full_sync_to_force_full`,
plus `tests/test_app.py::test_switching_servers_resets_per_server_sync_state`). Incident beb757c
existed precisely because one *combination* (server switch × surviving watermark) was never
enumerated. Sampling finds the combinations someone imagined; enumeration finds the rest.

**Repo asset.** The state space is genuinely small and fully identifiable in code
(`src/monitorr/sync.py`, `web/routes.py:79-83`, keys in `constants.py`): the decision is
`full = force_full or watermark is None or _floor_elapsed(last_full_sync)` (sync.py:95-99);
history failure promotes incremental→full and suppresses all watermark advancement
(sync.py:110-131); `_persist_watermark` writes depend only on `(full, prev, new_max)`
(sync.py:242-253); a server-identity change deletes both keys (routes.py:79-83). Five binary-ish
dimensions — `force_full` × `watermark ∈ {None, set}` × `last_full_sync ∈ {None, fresh,
elapsed}` × `history ∈ {ok, fail}` × `server ∈ {same, switched}` — at most 48 combinations.

**First three steps.**
1. Create `tests/test_sync_statespace.py`; build the grid with `itertools.product` feeding one
   `pytest.mark.parametrize`.
2. Write the oracle as a small pure function *in the test file*, derived from the canonical sync
   rules (owned by `monitorr-window-engine-reference` / behavior docs), returning the expected
   `(mode, watermark_advanced, last_full_advanced)` triple for each combination — written from
   the rules, NOT by reading `sync.py` (an oracle transcribed from the implementation is a
   tautology).
3. For each grid point, arrange settings via `store.set_setting` (`constants.HISTORY_WATERMARK`,
   `constants.LAST_FULL_SYNC`, `constants.PLEX_SERVER_ID`), run the trigger (`sync.run_sync` /
   the server-save route), and assert the oracle triple against the persisted post-state.

**You have a result when** every grid point passes against the independently written oracle, and
the grid demonstrably contains the pre-fix configurations of beb757c (switched server + stale
watermark) and e572eff-adjacent blind-sweep cases as distinct parametrizations. Every
oracle/code disagreement found on the way is a result in itself: either a live bug or a
documented rule that is wrong (route the doc fix through `monitorr-docs-and-writing`).

### Item 5 (candidate) — Mutation testing as a coverage-honesty check on engine/

**Why SOTA falls short.** Line coverage and green example tests can coexist with weak
assertions: nothing today measures whether the suite would *notice* an off-by-one in
`i < idx - keep_count + 1` (`engine/window.py:70`) or a flipped comparison in the KEEP floor.
Incident 20d578e was exactly a boundary-class bug in grace/KEEP interaction. Mutation testing —
automatically introducing small code changes and checking that some test fails — is the standard
honesty check, and effectively unused in the *arr ecosystem.

**Repo asset.** The decision core is small and fast to re-test: `engine/window.py` (283 lines),
`engine/grace.py` (144), `engine/policy.py` (105); the suite needs no network (respx) and no
services, so a full mutation run over `src/monitorr/engine/` is tractable on a laptop. mypy
strict (`pyproject.toml` lines 59-62) pre-kills the type-invalid mutant classes, shrinking the
triage set.

**First three steps.**
1. Pick the tool: `uv add --group dev mutmut` — **unverified**: mutmut's compatibility with this
   repo's `src/` layout and Python 3.12 must be checked before committing the dependency;
   `cosmic-ray` is the fallback. Whichever is chosen, the lockfile change ships in the same
   commit (see `monitorr-build-and-env`).
2. Scope it in `pyproject.toml` to mutate only `src/monitorr/engine/` and run only the fast
   engine-facing tests (`tests/test_window.py tests/test_grace.py tests/test_policy.py
   tests/test_actions.py`) — full-suite mutation runs waste time on web-route mutants.
3. Run locally (`uv run mutmut run`), then triage survivors in `window.py` and `grace.py` FIRST
   — a surviving mutant in `_should_delete_behind` or `keep_protected_keys` is a hole in the
   re-download/over-delete defenses. Do not wire into CI until the survivor list is stable.

**You have a result when** every surviving mutant in `engine/window.py` and `engine/grace.py` is
either killed by a new named test or written down as semantically equivalent with a one-line
argument. The mutation *score* is explicitly not the goal — an unexamined 95% is worth less than
an examined 80%. The item is falsified for a given tool if it cannot run against the `src/`
layout; switch tools rather than restructuring the package.

### Item 6 (candidate) — External positioning under the claims-evidence rule

**Why SOTA falls short.** Comparable episode-management approaches (*arr companions and scripts
that trim watched media) position on feature lists; none points at machine-checked reliability
guarantees. monitorr's differentiator per the maintainer is reliability — but an *asserted*
reliability advantage without evidence is indistinguishable from marketing, and this repo's own
history (13 releases, 8 of them fixing self-inflicted re-download/over-delete bugs) demands
humility. Positioning claims are allowed ONLY under the claims-evidence rule above.

**Repo asset.** The README already makes testable claims with real backing (the worked example
above), the test suite has stable, citable test names, and the changelog's cause→mechanism→fix
style means every *fixed* weakness is honestly documented — a credibility asset no comparison
table can fake.

**First three steps.**
1. Draft the full claim→test mapping table for every capability sentence in `README.md`
   (Features section, lines 16-37) — as a working document first, not a README edit; the
   audit itself needs no change-control, the resulting edits do.
2. For each unbacked or over-broad claim found, open one of two actions via
   `monitorr-change-control`: write the missing named test, or soften the wording to what the
   existing tests show.
3. Only after Items 1-3 above actually land may positioning language include "property-tested"
   or "divergence-simulated" — each such phrase citing its test file by name. Never compare
   against a named third-party tool's behavior (untestable here); state only monitorr's tested
   behavior.

**You have a result when** every capability sentence in `README.md` maps to a named test in the
table and zero sentences remain unmapped — falsified by any reader finding a shipped capability
sentence with no cited test. Season-pack behavior stays out of positioning entirely until
`monitorr-season-pack-campaign` resolves it.

## Ground rules for all items

- [ ] Nothing here is implemented. In any doc, issue or session note, label these items
      candidate/open until the tests exist in `tests/` on `dev`.
- [ ] Every item lands through `monitorr-change-control`; Items 1-4 touch anchor/sync/window
      territory → adversarial review against the incident list is mandatory.
- [ ] New dev deps: `uv add --group dev <pkg>` from the repo root; commit `pyproject.toml` and
      `uv.lock` together; CI proof is `uv sync --frozen` + `uv run pytest`
      (`.github/workflows/ci.yml` lines 16-20).
- [ ] mypy strict covers `tests` (`pyproject.toml` line 61): new test code must type-check;
      prefer typed strategies and typed fakes from the start.
- [ ] No item may weaken the dry-run default or add a Sonarr write that bypasses
      `engine/actions.py`.
- [ ] When an item finds a real bug: evidence bar and write-up per
      `monitorr-research-methodology`; incident story, if it becomes one, per
      `monitorr-failure-archaeology`.

## When NOT to use

| You want | Go to |
|---|---|
| Run/extend TODAY's test suite, respx patterns, conftest, pre-push gate | `monitorr-validation-and-qa` |
| The evidence bar for a live investigation, predict-numbers worksheet, idea lifecycle | `monitorr-research-methodology` |
| The exact GET/KEEP/anchor/grace formulas with code anchors | `monitorr-window-engine-reference` |
| Why the invariants exist and what must not change | `monitorr-architecture-contract` |
| Full incident stories behind the hashes cited here | `monitorr-failure-archaeology` |
| The season-pack grab/cancel campaign (fix work, not positioning) | `monitorr-season-pack-campaign` |
| Landing any of this (branches, commits, review gates) | `monitorr-change-control` |
| uv/lockfile/CI mechanics | `monitorr-build-and-env` |

## Provenance and maintenance

Written 2026-07-02 against v1.6.1 (commit c580f65). Every fact below can drift; re-verify with
the listed command from the repo root before relying on it.

| Fact stated above | Re-verify with |
|---|---|
| No property/mutation tooling installed yet | `grep -iE "hypothesis|mutmut|cosmic" pyproject.toml uv.lock` (expect no matches) |
| 108 tests (example-based only) | `grep -c "def test_" tests/*.py \| awk -F: '{s+=$2} END {print s}'` |
| Pure decision-core signatures (window.py:53-83) | `grep -n "def _select_ahead\|def _should_delete_behind\|def keep_protected_keys\|def _real_episodes" src/monitorr/engine/window.py` |
| Anchor-floor backing tests still exist under these names | `grep -n "test_anchor_floored_at_persisted_furthest_watch\|test_incremental_sync_does_not_regress_below_recorded_max\|test_anchor_floor_clamps" tests/test_window.py tests/test_sync.py` |
| Four anchor paths and their locations | `grep -rn "apply_window\|keep_protected_keys" src/monitorr/plex/poller.py src/monitorr/sync.py src/monitorr/engine/grace.py` |
| Sync decision + no-advance-on-blind-sweep | `grep -n "force_full\|history_failed\|_persist_watermark" src/monitorr/sync.py` |
| Server-switch resets watermark/last_full | `grep -n "PLEX_SERVER_ID" src/monitorr/web/routes.py src/monitorr/constants.py` |
| Incident hashes cited (dd889ee 20d578e 4245d21 5ce7fab e572eff 31fa9d5 c72357d beb757c 9a189ef 351bc8a) | `git log --oneline --all \| grep -E "^(dd889ee\|20d578e\|4245d21\|5ce7fab\|e572eff\|31fa9d5\|c72357d\|beb757c\|9a189ef\|351bc8a)"` |
| README claim wording at the cited lines | `sed -n '16,37p' README.md` |
| mypy strict over src AND tests; CI commands | `sed -n '59,66p' pyproject.toml && sed -n '14,21p' .github/workflows/ci.yml` |

If a frontier item gets implemented: move it from candidate to done IN THIS FILE in the same
commit that lands its tests (per `.claude/documentation.md`), name the tests here, and update
the claims-evidence worked examples if the new tests back a public claim.
