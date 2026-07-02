# monitorr

monitorr is a service (designed for Docker) that watches which shows are being viewed in Plex
and, via the Sonarr API, keeps *N* episodes **ahead** of the viewing point
monitored/downloaded and keeps only *N* **behind** (deleting the rest from disk through
Sonarr), protecting key episodes such as the pilot. Everything **API-only**, with no access to
the media disk.

**Stack**: Python 3.12 · FastAPI · HTMX · SQLite · Docker single-image multi-arch (Web UI on
`:8080`). Details in [`.claude/tech-stack.md`](.claude/tech-stack.md). The Plex/Sonarr/window
logic is implemented and tested; published on Docker Hub and GHCR (current version:
`version` in [`pyproject.toml`](pyproject.toml)).

> Before developing, read [`.claude/documentation.md`](.claude/documentation.md) to learn
> **how and when** to maintain this documentation.

## Rules

- Always develop on the `dev` branch; merge to `main` only when explicitly requested.
- After each code edit → commit on `dev` → `git push` immediately.
- When merging to `main`, the message must summarize everything new since the last commit on `main`.
- `CLAUDE.md` and `.claude/` **do** travel to `main` (do not exclude them in the merge).
- Update the `.claude/` docs after any major change to architecture, commands or rules —
  **in the same commit** as the change. How and when: [`.claude/documentation.md`](.claude/documentation.md).
- Language of docs, comments, commits and UI: English. Identifiers, variable names, branches and code in English.
- **Code is ground truth.** When a doc and the code disagree, trust the code, then either fix
  the doc in the same commit or file the disagreement in the **standing-errata table** in
  [`.claude/skills/monitorr-docs-and-writing/SKILL.md`](.claude/skills/monitorr-docs-and-writing/SKILL.md).
- A change that invalidates a fact guarded by a skill's *Provenance and maintenance* section
  updates that skill **in the same commit** (run the provenance re-verification commands).

## Documentation layers (reading order)

1. **This file** — cover page and router.
2. [`.claude/rules.md`](.claude/rules.md) — binding rules for any edit.
3. **Domain docs** in [`.claude/`](.claude/) — architecture, tech stack, workflows, behavior,
   Plex, Sonarr, Tautulli (index below).
4. **Skills** in [`.claude/skills/`](.claude/skills/) — task-oriented runbooks, references and
   playbooks (routing table below). Each skill re-verifies its own facts (Provenance sections).
5. [`README.md`](README.md) (user-facing) and [`CHANGELOG.md`](CHANGELOG.md) (forensic memory:
   every fix explains cause → mechanism → fix).

## Context (read as needed per task)

| File | When to read |
|---|---|
| [`.claude/rules.md`](.claude/rules.md) | Before any edit — git, language, style, comments, abstractions |
| [`.claude/documentation.md`](.claude/documentation.md) | Before touching the documentation — how and when to update it, when to create a new doc |
| [`.claude/architecture.md`](.claude/architecture.md) | Stack, components, data flow, technical decisions and pending items |
| [`.claude/tech-stack.md`](.claude/tech-stack.md) | Versions, directory layout, dependencies and stack decisions |
| [`.claude/workflows.md`](.claude/workflows.md) | Development commands, build/test/lint, deploy, env vars, merge to `main` |
| [`.claude/behavior.md`](.claude/behavior.md) | Core logic: episode window (GET/KEEP), Always-Have, grace periods, dry-run, override |
| [`.claude/plex.md`](.claude/plex.md) | Plex integration: Login with Plex (PIN/OAuth), server discovery, polling/webhook, TVDB correlation |
| [`.claude/sonarr.md`](.claude/sonarr.md) | Sonarr integration: monitor/search/delete episodes via API |
| [`.claude/tautulli.md`](.claude/tautulli.md) | Alternative/future source of watch status (not in v1) |

## Task → skill routing

Skills live in [`.claude/skills/`](.claude/skills/), one directory per skill
(`<name>/SKILL.md`). Route by task:

| Task at hand | Skill |
|---|---|
| Commit/branch/merge/release mechanics; what needs review before merging | [`monitorr-change-control`](.claude/skills/monitorr-change-control/SKILL.md) |
| A live instance misbehaves (re-downloads, no deletions, stuck sync, silent webhook…) | [`monitorr-debugging-playbook`](.claude/skills/monitorr-debugging-playbook/SKILL.md) |
| "Has this bug happened before?" — incident history with commits and lessons | [`monitorr-failure-archaeology`](.claude/skills/monitorr-failure-archaeology/SKILL.md) |
| About to change engine/sync/store code — the invariants you must preserve | [`monitorr-architecture-contract`](.claude/skills/monitorr-architecture-contract/SKILL.md) |
| Exact window/grace/policy formulas and `apply_window`/sweep walkthroughs | [`monitorr-window-engine-reference`](.claude/skills/monitorr-window-engine-reference/SKILL.md) |
| Plex/Sonarr endpoints, auth, correlation, identifier stability, API quirks | [`monitorr-plex-sonarr-reference`](.claude/skills/monitorr-plex-sonarr-reference/SKILL.md) |
| Any env var, Policy field, setting key, default, or form-clamping rule | [`monitorr-config-and-flags`](.claude/skills/monitorr-config-and-flags/SKILL.md) |
| uv/ruff/mypy/pytest configs, CI jobs, lockfile, Docker build | [`monitorr-build-and-env`](.claude/skills/monitorr-build-and-env/SKILL.md) |
| Run locally or in Docker, first-run setup, PUID/PGID, backups, operations | [`monitorr-run-and-operate`](.claude/skills/monitorr-run-and-operate/SKILL.md) |
| Inspect a live/stopped instance's DB safely; runnable diagnostic scripts | [`monitorr-diagnostics-and-tooling`](.claude/skills/monitorr-diagnostics-and-tooling/SKILL.md) |
| Run/write tests, faking styles, regression-test recipe, coverage gaps | [`monitorr-validation-and-qa`](.claude/skills/monitorr-validation-and-qa/SKILL.md) |
| Edit docs, changelog style, skill upkeep, **the standing-errata table** | [`monitorr-docs-and-writing`](.claude/skills/monitorr-docs-and-writing/SKILL.md) |
| The season-pack grab/cancel oscillation (measure it, fix it) | [`monitorr-season-pack-campaign`](.claude/skills/monitorr-season-pack-campaign/SKILL.md) |
| Predict/derive/prove engine behavior; analyze from DEBUG logs | [`monitorr-proof-and-analysis-toolkit`](.claude/skills/monitorr-proof-and-analysis-toolkit/SKILL.md) |
| Plan new correctness work (property tests, simulators); positioning claims | [`monitorr-research-frontier`](.claude/skills/monitorr-research-frontier/SKILL.md) |
| Evidence bar for diagnoses, predict-before-run worksheet, idea lifecycle | [`monitorr-research-methodology`](.claude/skills/monitorr-research-methodology/SKILL.md) |

> As the code grows, **expand** `.claude/` with per-domain docs (DB, API, UI, etc.) following
> the rules in [`.claude/documentation.md`](.claude/documentation.md) and add its row to the
> Context table; new skills follow the library rules in
> [`monitorr-docs-and-writing`](.claude/skills/monitorr-docs-and-writing/SKILL.md) and get a
> row in the routing table above.
