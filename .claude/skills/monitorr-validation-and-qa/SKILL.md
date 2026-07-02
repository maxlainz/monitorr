---
name: monitorr-validation-and-qa
description: >-
  How to run and write monitorr tests: pytest/uv commands, the pre-push gate (ruff check, ruff
  format --check, mypy, pytest in CI order), the test-suite map, the three faking styles (respx,
  monkeypatch-where-used, pure unit), the engine regression-test recipe, mypy-strict-on-tests,
  coverage gaps. Use when adding/running/fixing tests or validating a change. NOT for
  property-based/Hypothesis test design → monitorr-research-frontier; toolchain/uv/Docker setup
  → monitorr-build-and-env; what the engine SHOULD compute → monitorr-window-engine-reference;
  git/release flow → monitorr-change-control.
---

# monitorr — validation and QA

How to validate any change to this repo: run the suite, read its map, pick the right faking
style, and add the regression test the discipline rules require. All commands run from the repo
root. Suite size: 108 tests across 11 test files + `conftest.py` (counted 2026-07-02, v1.6.1);
re-count with the command in "Provenance" below.

## When NOT to use this skill

| You need… | Go to |
|---|---|
| Installing uv, dependency sync, ruff/mypy/pytest config internals, Docker build, CI job anatomy | `monitorr-build-and-env` |
| What the window/grace/policy engine is SUPPOSED to do (formulas, invariants) | `monitorr-window-engine-reference` |
| Why an invariant exists and what must never change without review | `monitorr-architecture-contract` |
| Commit/branch/release rules, the adversarial-review discipline for anchor changes | `monitorr-change-control` |
| Diagnosing a live symptom (re-download loop, blind sync, silent webhook) | `monitorr-debugging-playbook` |

## 1. Running tests

No environment setup is required. `tests/conftest.py` points `MONITORR_CONFIG_DIR` at a fresh
tempdir at import time (`os.environ.setdefault`, conftest.py:9 — so do NOT have a real
`MONITORR_CONFIG_DIR` exported in your shell, or tests would use it), and the autouse `fresh_db`
fixture deletes the SQLite db + WAL/SHM files and re-runs `init_db` before EVERY test
(conftest.py:15-21). Tests are therefore isolated, order-independent, and never touch a real
Plex/Sonarr: HTTP is faked (see §4) and unmocked requests fail the test.

`asyncio_mode = "auto"` is set in `pyproject.toml` (`[tool.pytest.ini_options]`): write
`async def test_...` directly — NO `@pytest.mark.asyncio` decorator, no event-loop fixtures.

| Goal | Command |
|---|---|
| Whole suite | `uv run pytest` |
| One file | `uv run pytest tests/test_window.py` |
| One test | `uv run pytest tests/test_window.py::test_anchor_floored_at_persisted_furthest_watch` |
| Substring match | `uv run pytest -k anchor` |
| Stop at first failure | `uv run pytest -x` |

## 2. The pre-push gate (exact CI order)

CI (`.github/workflows/ci.yml`, `quality` job) runs these after `uv sync --frozen`; run the same
four before every push, in this order — all four must pass:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```

If `ruff format --check` fails, run `uv run ruff format .` and re-check. CI pins uv 0.11.7 and
uses `uv sync --frozen`; the lockfile rule and toolchain details live in `monitorr-build-and-env`.

## 3. Test-suite map

Counts as of 2026-07-02 (v1.6.1); recount per file with
`grep -Ec '^(async )?def test_' tests/<file>`.

| File | Subsystem under test | Tests | Faking style | Representative tests |
|---|---|---|---|---|
| `tests/test_window.py` | `engine/window.apply_window` end-to-end vs Sonarr API | 22 | respx | `test_anchor_floored_at_persisted_furthest_watch`, `test_cancels_season_pack_surplus_from_queue`, `test_refetches_episodes_after_season_cascade` |
| `tests/test_sync.py` | `sync.run_sync` full/incremental, watermark, migration 4 | 17 | respx (Plex + Sonarr) | `test_sync_anchors_on_history_when_files_deleted`, `test_failed_history_advances_neither_watermark_nor_full_stamp`, `test_migration_clears_last_full_sync_to_force_full` |
| `tests/test_app.py` | FastAPI routes, forms, webhook endpoint, link flows | 21 | ASGI client + monkeypatch + respx | `test_pages_render`, `test_policy_form_tolerates_bad_numbers_and_clamps_threshold`, `test_switching_servers_resets_per_server_sync_state` |
| `tests/test_grace.py` | `engine/grace.sweep` (completed/watched/unwatched, KEEP floor) | 10 | respx | `test_completed_purges_when_caught_up_and_inactive`, `test_keep_floor_clamps_anchor_to_episodes_sonarr_lists`, `test_unwatched_respects_keep_floor` |
| `tests/test_webhook_register.py` | Plex account-webhook auto-registration | 8 | respx (+ ASGI for routes) | `test_register_appends_and_replaces_monitorr_entry`, `test_route_register_pushes_to_plex` |
| `tests/test_actions.py` | `engine/actions` (normalize_to_pilot, cancel_downloads) | 6 | respx | `test_normalize_real_deletes_downloaded_and_monitors_pilot`, `test_cancel_downloads_removes_from_queue_keeping_seed` |
| `tests/test_poller.py` | `plex/poller` state machine + `process_watch` anchoring | 5 | monkeypatch-where-used | `test_binge_fires_each_episode_same_session_key`, `test_near_complete_session_disappearing_counts_as_watched`, `test_process_watch_anchors_on_furthest_watched` |
| `tests/test_plex_history.py` | `plex/client.get_watch_history_by_show` + accounts | 5 | respx | `test_get_watch_history_recovers_readded_show_by_title`, `test_history_filters_by_account_before_dedup` |
| `tests/test_policy.py` | Always-Have pattern matcher (`matches_always_have`) | 5 | pure unit | `test_exact_episode`, `test_whole_season`, `test_invalid_pattern_ignored` |
| `tests/test_store.py` | Deletion previews/history in `store` | 5 | pure unit (DB only) | `test_pending_previews_are_deduplicated`, `test_disabling_dry_run_clears_pending` |
| `tests/test_pooling.py` | Per-sync-cycle HTTP client pooling identity | 4 | pure unit | `test_plex_pooled_session_reuses_one_client`, `test_sonarr_client_is_fresh_without_session` |

## 4. The three faking styles — pick by what you are testing

### (a) respx httpx-transport mocking — the dominant style; use for anything that talks HTTP

Decorate with `@respx.mock`, register a route table for every endpoint the code will hit, then
assert on `route.called` / `route.call_count` / `route.calls` (captured requests). The house
pattern is a `_mock_sonarr()` helper returning a `dict[str, respx.Route]` (see
`tests/test_window.py:72-90`):

```python
"monitor": respx.put(f"{SONARR}/episode/monitor").mock(
    return_value=httpx.Response(200, json=[])
),
"delete": respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
    return_value=httpx.Response(200)
),
```

Inspect write payloads by decoding captured requests
(`json.loads(c.request.content)` over `routes["monitor"].calls` — test_window.py:209).
For SUCCESSIVE sync cycles or a response that must change between two fetches in one call, use
`side_effect` with a list of responses, consumed in order:

- `respx.get(f"{PLEX}/status/sessions/history/all").mock(side_effect=history)` feeds one history
  response per `run_sync()` call (`tests/test_sync.py:659`, used by the watermark tests).
- `episodes.side_effect = [pre_cascade_json, post_cascade_json]` makes the second episode fetch
  show Sonarr's season-cascade result
  (`tests/test_window.py:536-544`, `test_refetches_episodes_after_season_cascade`).

Any request with no matching route fails the test (respx default) — so a new endpoint call in
src code shows up as a test failure, which is intended.

### (b) monkeypatch.setattr on the USING module's name — for poller/webhook/routes collaborators

Patch the name where it is USED, not where it is defined. `plex/poller.py` does
`from monitorr.plex.client import get_sessions, resolve_tvdb_id` and
`from monitorr.engine.window import apply_window` (poller.py:14-15), so tests patch
`poller.get_sessions`, `poller.resolve_tvdb_id`, `poller.apply_window`
(`tests/test_poller.py:42-51,98`). Likewise `web/routes.py` imports `process_watch` by name →
patch `routes.process_watch` (test_app.py:94-95); but routes calls `sync.run_sync` through the
module attribute → patch `sync.run_sync` (test_app.py:183). Replacement functions are small
typed async closures appending to a captured list:

```python
async def fake_process_watch(tvdb_id: int, season: int, episode: int) -> None:
    calls.append((tvdb_id, season, episode))
monkeypatch.setattr(poller, "process_watch", fake_process_watch)
```

Use this style to isolate ONE layer (e.g. the poller's debounce/near-complete logic) without
building the whole HTTP world underneath it.

### (c) Pure unit tests — for pure functions and local state

No mocking at all: `tests/test_policy.py` calls `matches_always_have` directly;
`tests/test_pooling.py` asserts client identity (`a is b`) inside/outside `pooled_session`
without any network I/O; `tests/test_store.py` exercises the store against the per-test SQLite
db. Prefer this style whenever the behavior has no collaborator — cheapest to write and read.

Web routes are exercised two ways in `tests/test_app.py`: sync `TestClient(app)` for simple
GET/POST smoke, and `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` when the test
body must also await store calls (test_app.py:97-102).

## 5. Recipe — regression test for an engine (window/anchor) change

Template: `tests/test_window.py::test_anchor_floored_at_persisted_furthest_watch`
(test_window.py:578). Copy its shape:

1. **Configure**: set Sonarr link + policy via the store/policy setters (no env vars):
   ```python
   await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
   await store.set_setting(constants.SONARR_API_KEY, "key")
   await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[]))
   ```
   (or reuse the file's `_configure()` helper). Dry-run is ON by default; call
   `await set_dry_run(False)` only if you must assert real Sonarr writes — in dry-run you assert
   the recorded previews instead, which is often the cleaner regression oracle.
2. **Seed watch history**: `await store.record_watch(TVDB, season, episode)` — pass
   `watched_at=...` (ISO string, see `_days_ago` at test_window.py:400) when the grace/arming
   clock matters. The persisted watch store is what anchor regressions are about.
3. **Mock Sonarr** with respx (reuse `_mock_sonarr(...)` or `_mock_sonarr_seasons(...)`), giving
   it an episode list that reproduces the bad state (files present/missing, monitored flags).
4. **Act**: `await apply_window(TVDB, season=S, episode=E)` with the anchor the buggy path
   would supply.
5. **Assert on captured calls** — which episode IDs were monitored/unmonitored
   (`json.loads(c.request.content)` over the `monitor` route's calls), searched (`command`
   route's `episodeIds`), deleted (`delete` route call count / URLs), or in dry-run:
   `await store.list_deletions(dry_run=True)` as a set of `(season, episode)` tuples.

For sync-level regressions, template on
`tests/test_sync.py::test_incremental_sync_does_not_regress_below_recorded_max` (adds Plex
mocks + watermark seeding around the same skeleton). For grace-sweep regressions, template on
`tests/test_grace.py::test_keep_floor_clamps_anchor_to_episodes_sonarr_lists`.

## 6. Strictness gates on test code itself

- **mypy strict covers tests**: `pyproject.toml` `[tool.mypy]` has `files = ["src", "tests"]`,
  `strict = true`. Consequences: every test needs `-> None`; fixtures are typed
  (`monkeypatch: pytest.MonkeyPatch`, `AsyncIterator[None]` for the conftest fixture); helper
  factories declare returns (`-> dict[str, object]` for JSON fixtures, test_window.py:158);
  capture lists are typed (`list[tuple[int, int, int]]`). An untyped helper fails CI even if
  pytest passes.
- **ruff format is a hard gate**: `ruff format --check .` in CI; line length 100
  (`[tool.ruff]`), lint set `E,F,I,UP,B,ASYNC,SIM` over `src` and `tests`.
- **The noqa pattern** (all three suppressions in the suite): `# noqa: E402` on the two conftest
  imports that must run AFTER the `MONITORR_CONFIG_DIR` env line (conftest.py:11-12) — keep that
  pattern if you add imports there; and one `# noqa: E501` pinning a deliberately split signature
  for an over-long descriptive test name (`tests/test_sync.py:762`) — the trailing comment also
  stops `ruff format` from re-joining the lines. Descriptive-but-long test names are house style;
  suppress narrowly like this rather than shortening the name.

## 7. Coverage gaps — where to add tests first (honest, as of v1.6.1)

| Area | Current coverage | First test to add |
|---|---|---|
| `src/monitorr/plex/auth.py` | Thin: only the PIN-create and poll/resources paths, indirectly via routes tests (`test_plex_link_returns_auth_url`, `test_single_server_link_triggers_full_sync`) | Direct tests for `poll_pin` outcomes (pending vs authorized vs error) and `build_auth_url` contents |
| `plex/poller.py` state machine | `_poll_once` has 5 scenarios; `poll_loop` (interval loop, error handling, `unresolved` cache across polls, `fired` set growth over long sessions) untested | A multi-poll scenario exercising the unresolved-ratingKey cache and error recovery |
| `sonarr/client.py` | No dedicated file: covered only transitively through respx in window/grace/actions/sync tests, plus pooling identity in `test_pooling.py` | Direct tests for non-2xx handling per endpoint (what raises vs returns None/empty) |
| `web/templates/` | Smoke-only: `test_pages_render` asserts status 200 + substrings | Fragment-level assertions for the HTMX partials (`_policy_fields.html`, `_plex_link.html`) |

The longer-term ambition — property-style tests and Plex/Sonarr divergence simulation toward
provable anchor correctness — is owned by `monitorr-research-frontier`; treat the table above as
the pragmatic first rung toward it.

## 8. QA discipline hooks (non-negotiable)

- **Any change touching anchor/sync/window code REQUIRES a new regression test before merge**
  (use the §5 recipe). This is discipline rule (a); its rationale and the adversarial-review
  procedure live in `monitorr-change-control`. The incident history shows anchor bugs are
  self-amplifying — a test is the only durable proof the loop stays closed.
- **Never edit a published migration** in `src/monitorr/db.py` — `MIGRATIONS` is append-only.
  `tests/test_sync.py::test_migration_clears_last_full_sync_to_force_full` depends on the list's
  stable indices and total length (it applies `MIGRATIONS[0..2]` by index and asserts
  `PRAGMA user_version == len(db.MIGRATIONS)`); editing a shipped entry silently changes what
  that test simulates, and would corrupt real users' upgrade paths.
- New behavior ships with its test in the SAME commit as the change (see `.claude/rules.md` and
  `monitorr-change-control` for the commit flow).

## Provenance and maintenance

Verified against the repo at v1.6.1, 2026-07-02. Re-verify before trusting:

| Fact | Re-verification command |
|---|---|
| Test count (108, total) | `grep -rEc "^(async )?def test_" tests/*.py \| awk -F: '{s+=$2} END {print s}'` |
| Per-file test counts | `for f in tests/test_*.py; do echo "$f: $(grep -Ec '^(async )?def test_' $f)"; done` |
| Test file list (11 + conftest) | `ls tests/` |
| asyncio auto mode, testpaths | `grep -A2 'tool.pytest' pyproject.toml` |
| mypy strict over tests | `grep -A3 'tool.mypy' pyproject.toml` |
| ruff line length / lint set | `grep -A6 'tool.ruff' pyproject.toml` |
| CI gate order | `cat .github/workflows/ci.yml` |
| conftest env + fresh_db behavior | `cat tests/conftest.py` |
| Representative test names still exist | `grep -rn "def test_anchor_floored_at_persisted_furthest_watch" tests/` |
| noqa inventory | `grep -rn noqa tests/` |
| MIGRATIONS length the migration test pins | `grep -n MIGRATIONS src/monitorr/db.py tests/test_sync.py` |
| Coverage-gap claims (no dedicated sonarr client test file) | `ls tests/ \| grep sonarr` (expect no match) |
