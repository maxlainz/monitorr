---
name: monitorr-docs-and-writing
description: Documentation maintenance for monitorr — the doc map and reading order, docs-in-same-commit rules, when to create vs expand a doc, changelog house style (Keep-a-Changelog + mechanism-explaining entries), skill-library upkeep, and THE STANDING-ERRATA TABLE (sole home of every known doc/code disagreement). Use when editing any doc, writing a changelog entry, or when a doc contradicts the code. NOT for git/merge/release mechanics → monitorr-change-control; NOT for incident stories → monitorr-failure-archaeology.
---

# monitorr docs and writing

Owner of documentation-maintenance rules, the changelog house style, the skill-library upkeep
rules, and the **standing-errata table** (§3) — the single registry of every known place where
a doc of record disagrees with the code. Code is ground truth; when a doc lies, the truth is
stated here until the doc is fixed. Rules of record: `.claude/documentation.md` (how/when to
document) and `.claude/rules.md` (language, style); this skill adds the errata registry, the
changelog anatomy, and the skill-library rules.

## 1. Documentation map (reading order)

Read top-to-bottom on a cold start; jump by the "owns" column when maintaining.

| Doc | Owns |
|---|---|
| `CLAUDE.md` | Cover page/router: project intro, Rules, the Context table indexing `.claude/` docs. No technical detail. |
| `.claude/rules.md` | Code/git/style/language rules — the entry doc, read before any edit. |
| `.claude/documentation.md` | Meta: how and when to maintain all other docs (this skill distills it in §2). |
| `.claude/architecture.md` | Stack summary, components, data flow, technical decisions, pending items. |
| `.claude/tech-stack.md` | Versions, directory layout, dependencies, stack decisions. |
| `.claude/workflows.md` | Dev/build/test/lint commands, env vars, deploy, merge-to-main, releases. |
| `.claude/behavior.md` | Core logic: GET/KEEP window, Always-Have, grace periods, dry-run, override. |
| `.claude/plex.md` | Plex integration: PIN/OAuth login, server discovery, polling/webhook, TVDB correlation. |
| `.claude/sonarr.md` | Sonarr integration: monitor/search/delete via API, pitfalls, accepted trade-offs. |
| `.claude/tautulli.md` | Alternative/future watch-status source (not in v1). |
| `README.md` | User-facing: install, compose, env-var table, feature overview. |
| `CHANGELOG.md` | Release history — the project's memory (house style in §4). |
| `.claude/skills/` | The skill library: deep per-domain operational knowledge (rules in §5). |

New `.claude/` docs must be registered in `CLAUDE.md`'s Context table (see §2).

## 2. Maintenance rules (distilled from `.claude/documentation.md` — verified 2026-07-02)

1. **Docs are part of the change, not a later step.** If a change invalidates a doc, update the
   doc **in the same commit** as the change. Update triggers, per doc: architecture/components/
   data flow → `architecture.md`; stack/deps/layout → `tech-stack.md`; new dev/build/test/deploy
   command → `workflows.md`; new code/git/style/language rule → `rules.md`; schema or route
   change → that domain's doc; git flow/release/CI change → `rules.md`/`workflows.md`.
2. **One home per fact.** Each fact lives in exactly one doc; never duplicate a rule or command
   across two docs — link to the canonical doc with a relative markdown link
   (`` [`x.md`](x.md) ``, `../` for files outside `.claude/`).
3. **Create vs expand.** Create a new `.claude/<domain>.md` only when a domain appears that does
   not exist yet and starts having its own rules/formats/pitfalls. Adding a detail to a covered
   domain → expand the existing doc; do not fragment. One file per **domain/concern**, never per
   layer (no `backend.md`/`frontend.md`).
4. **Register new docs.** On creating a doc: `#` title on line one, `##` sections; add its row to
   `CLAUDE.md`'s Context table with a clear "when to read"; if it absorbs content from another
   doc, move the content and leave only a link.
5. **Explain the why, not the what**: invariants, trade-offs, pitfalls, external constraints.
   Never narrate what the code already says. Compact and directive; no filler.
6. **English only** for docs, comments, commit messages and UI; English identifiers, file names,
   branches, env vars (`.claude/rules.md` "Language"). The known violations (Spanish in the
   Dockerfile, release.yml and one conftest comment) were fixed on 2026-07-02 — retired
   erratum E8 below.

Committing a doc change follows the normal dev-branch flow → `monitorr-change-control`.

## 3. THE STANDING-ERRATA TABLE

The **sole registry** of known doc/code disagreements. Rules of the table itself:

- An erratum lives **only here**. Other docs and skills may say at most
  "stale — see the errata table in `monitorr-docs-and-writing`" and must state the CODE's truth,
  never the stale claim.
- When touching a topic listed below, do **not** propagate the stale claim; fix the doc if the
  edit is in scope, and update this table in the same commit.
- When a doc is fixed: fill **fixed-in** with the commit hash and flip **status** to
  `fixed <date>`, then move the row to the "Fixed errata" subsection below. Rows are retired,
  **never deleted** — they are memory (a future session must be able to see what used to lie).
- Drift that cannot or will not be fixed now gets status `accepted` **with a written reason** in
  the row (e.g. an upstream constraint, or a deliberate simplification in user-facing docs).
- New drift discovered anywhere → add a row here (same commit as the discovery, if committing).

All quotes and file evidence below re-verified against the repo on 2026-07-02 (v1.6.1, c580f65).
Later the same day, **all 14 errata were fixed** — E4 in commit `219cd2e` ("docs: upgrade
CLAUDE.md into the single router"), the other 13 in `f9f3689` ("docs+chore: fix all 14 verified
doc/code errata; English-only sweep") — and each fix was verified against the current files.

### Open errata

| ID | Doc + stale claim (quoted) | Code truth (file evidence) | Severity | Status | Fixed-in |
|---|---|---|---|---|---|

**No open errata as of 2026-07-02.** New drift discovered anywhere → add a row above, per the
rules at the top of §3.

### Fixed errata

Retired rows — kept as memory per the table rules (never deleted). The "stale claim" column
quotes what the doc **used to** say; its `file:line` anchors refer to the **pre-fix** files.

| ID | Doc + stale claim (quoted, pre-fix) | Code truth (file evidence) | Severity | Status | Fixed-in |
|---|---|---|---|---|---|
| E1 | `.claude/architecture.md:32` — window engine does `"force to Pilot" (opt-in)`; `:73-74` decision #9 `"Force to Pilot opt-in (manual)"` … `"it's done only when the user requests it"` | Normalize-to-Pilot is **automatic**: `src/monitorr/sync.py:317` calls `actions.normalize_to_pilot` every sync for every managed show with no recorded viewing; no manual route/button in `src/monitorr/web/routes.py`; `.claude/behavior.md:138` correctly says "There is no manual action". Fixed: decision 9 rewritten as "Normalize to Pilot is automatic", component bullet updated | HIGH | fixed 2026-07-02 | `f9f3689` |
| E2 | `.claude/architecture.md:81` (Pending decisions) — `"Editing the per-series policy (override) from the UI; today the override only enables/disables."` | Full per-series policy editing shipped in v1.4.0: `src/monitorr/web/routes.py:442` `save_series_policy`, `src/monitorr/engine/policy.py:51` `effective_policy`. Fixed: the pending-decisions bullet removed | HIGH | fixed 2026-07-02 | `f9f3689` |
| E3 | `.claude/sonarr.md:15` — `'the **"Normalize to Pilot"** button … sets it that way via API'` | No such button exists; the manual button was removed in commit `9468870` (only automatic normalize remains). Fixed: sonarr.md now says "automatic Normalize to Pilot" | MEDIUM | fixed 2026-07-02 | `f9f3689` |
| E4 | `CLAUDE.md` — `"v1.0.0 published on Docker Hub and GHCR"` | Version was already 1.6.1 (`pyproject.toml:3`). Fixed: `CLAUDE.md` no longer hardcodes a version; it points at `version` in `pyproject.toml` | MEDIUM | fixed 2026-07-02 | `219cd2e` |
| E5 | `.claude/architecture.md:3-4` header — `"A runnable skeleton exists; the Plex/Sonarr/window logic is in place as a contract (signatures + TODO)"` | Fully implemented and tested since v1.0.0; zero TODO/FIXME in `src/`. Fixed: header now says the logic is fully implemented and tested | MEDIUM | fixed 2026-07-02 | `f9f3689` |
| E6 | `README.md` env table and `.env.example` omitted `MONITORR_FULL_SYNC_INTERVAL` | Exists: `src/monitorr/config.py:30` `full_sync_interval: int = Field(default=2592000, ge=0)` (30 d; 0 disables); was documented only in `.claude/workflows.md:87`. Fixed: added to the README env table and `.env.example` | MEDIUM | fixed 2026-07-02 | `f9f3689` |
| E7 | `README.md:124` + `.env.example:24` — `MONITORR_SYNC_ON_STARTUP`: `"Sync once on startup if it never ran"` | Since 1.5.0 it syncs on startup only when a FULL is overdue (never ran OR rolling floor elapsed): `src/monitorr/main.py` + `sync.full_sync_due()`. Fixed: README + `.env.example` now use the overdue wording, matching `.claude/workflows.md` | MEDIUM | fixed 2026-07-02 | `f9f3689` |
| E8 | `.claude/rules.md` English-only rule vs reality | `Dockerfile` comments (e.g. line 1) AND its public OCI `description` label (`Dockerfile:19`) were Spanish; `.github/workflows/release.yml` comments/step names/messages were Spanish (e.g. line 3); `tests/conftest.py:7-8` had one Spanish comment block. `src/`, entrypoint and compose were already clean English. Fixed: English-only sweep of all three files (incl. the OCI label) | MEDIUM | fixed 2026-07-02 | `f9f3689` |
| E9 | `CHANGELOG.md` bottom link references stopped at `[1.2.1]` | Sections exist to 1.6.1; link refs for 1.3.0–1.6.1 were missing. Fixed: refs now cover 1.0.0–1.6.1 (see §4, link convention) | LOW | fixed 2026-07-02 | `f9f3689` |
| E10 | `README.md:96` — image published `"with tags :1, :1.0, :1.0.0 and :latest"` | metadata-action produces `:X.Y.Z`, `:X.Y`, `:X`, `:latest` per release. Fixed: README now generalizes as `:X.Y.Z`, `:X.Y`, `:X` and `:latest`, matching `.claude/workflows.md` | LOW | fixed 2026-07-02 | `f9f3689` |
| E11 | `MONITORR_HOST` documented nowhere (README, `.env.example`, workflows.md) | Exists: `src/monitorr/config.py:18` `host: str = "0.0.0.0"`, used in `src/monitorr/main.py`. Fixed: documented in all three | LOW | fixed 2026-07-02 | `f9f3689` |
| E12 | `.claude/behavior.md:59` Always-Have pattern list — `"S01E01 (pilot), S*E01 (first episode of each season), S* (full season)"` omitted `S01` (single whole season) | `src/monitorr/engine/policy.py:89` regex `^S(\d+|\*)(?:E(\d+|\*))?$` supports `S01E01`, `S*E01`, `S01`, `S*`; `README.md:22` lists all four. Fixed: behavior.md now lists all four | LOW | fixed 2026-07-02 | `f9f3689` |
| E13 | `.claude/tech-stack.md:28` — `"src/monitorr/engine/ — window.py and grace.py"`; `:19-20` — `"Structure in README.md"` | `engine/` also contains `actions.py` and `policy.py` (both central); `README.md` has no structure section. Fixed: the engine/ line lists all four modules; the dangling README-structure ref removed | LOW | fixed 2026-07-02 | `f9f3689` |
| E14 | `README.md:126` + `.env.example:31` — TZ `"affects grace periods"` / `"affects the grace-period calculation"` | Grace/age math is TZ-independent: `age_days` (`src/monitorr/engine/policy.py:15-21`) computes against `datetime.now(UTC)` over UTC-stored timestamps; `TZ` only changes log/display timestamp rendering. Fixed: README, `.env.example` and `.claude/workflows.md` now say log/display only, grace math UTC | LOW-MEDIUM | fixed 2026-07-02 | `f9f3689` |

### Corrections to earlier agent reports (do not repeat these errors)

- Git tags `v1.0.0`–`v1.6.1` **DO exist on the remote**. Verify with
  `git ls-remote --tags origin`. A fresh clone may simply not have fetched them — run
  `git fetch origin --tags` before concluding tags are missing.

## 4. Changelog house style (`CHANGELOG.md`)

Format: **Keep a Changelog** (1.1.0) + **SemVer** — declared in the file header. Sections per
release: `## [X.Y.Z] - YYYY-MM-DD` with `### Added` / `### Changed` / `### Fixed` as applicable,
newest first.

### The mechanism rule (discipline rule c — canonical home: `monitorr-change-control`)

**Changelog entries must explain the MECHANISM: cause → mechanism → fix.** The changelog is the
project's memory; a future session diagnoses regressions from it. "Fixed a bug where X" is not
acceptable for any Fixed entry.

Anatomy of one real exemplar — the 1.5.1 Fixed entry (`CHANGELOG.md`, quoted verbatim):

> - **Watched series no longer re-download themselves (sync anchor regression)**: the incremental sync
>   derived its anchor only from the cycle's live Plex read (`allLeaves` on disk + the history delta),
>   ignoring the persisted watch store. When a watched episode's file had already been trimmed **and**
>   its play predated the history watermark, it was in neither source, so the anchor fell back to the
>   furthest *on-disk* watch and the window slid backward — re-monitoring/searching already-watched
>   back-catalog. Each re-download re-appeared in `allLeaves`, advancing the anchor again, so a
>   fully-watched series re-downloaded itself N episodes per sync cycle. `apply_window` now **floors
>   the anchor at the furthest episode ever recorded as watched** (clamped to one Sonarr lists),
>   enforced for every path (sync, poller, webhook): the persisted, monotonic watch state — not the
>   volatile on-disk state — is the authority for where the window anchors.

Label its parts, and reproduce them in every Fixed entry you write:

| Part | Where in the exemplar |
|---|---|
| **Symptom** (bold lead, user-visible, past tense of the bug) | "Watched series no longer re-download themselves (sync anchor regression)" |
| **Cause** (the wrong assumption/input) | anchor derived only from the cycle's live Plex read, ignoring the persisted watch store |
| **Mechanism** (step-by-step how the cause produced the symptom, incl. amplification) | trimmed file + pre-watermark play → in neither source → anchor fell back → window slid backward → each re-download re-entered `allLeaves` → self-amplifying, N episodes per cycle |
| **Fix** (what the code does now, naming the function) | `apply_window` floors the anchor at the furthest recorded watch (clamped to one Sonarr lists), enforced on every path |
| **Migration note** (when upgrade behavior changes — under `### Changed`) | the same release's Changed entry: a one-time migration drops `last_full_sync` so the first cycle runs a full reconciliation |

The 1.6.1 Fixed entries follow the same shape; use them as further exemplars.

### Bottom link-reference convention

Every `## [X.Y.Z]` section heading gets a matching link reference at the bottom of the file:
`[X.Y.Z]: https://github.com/maxlainz/monitorr/releases/tag/vX.Y.Z`. Add the link ref **in the
same commit** as the new release section. Past drift: refs used to stop at `[1.2.1]` while
sections existed to 1.6.1 — retired erratum **E9**, fixed 2026-07-02 (refs now cover every
section, 1.0.0–1.6.1).

## 5. Skill-library maintenance rules

- Skills live at `.claude/skills/<name>/SKILL.md`. YAML frontmatter `name:` **must equal the
  directory name**; `description:` is a single trigger-rich string with explicit
  "NOT for X → use <sibling>" routing so sibling triggers never collide.
- Every skill has a **Provenance and maintenance** section listing, for each drift-prone fact, a
  one-line re-verification command. When a code change invalidates a skill's fact, update the
  skill **in the same commit/PR** as the code change — or, if that is not possible, file an
  erratum in §3 in the same commit.
- **Prefer commands over frozen counts** (`ls tests/ | wc -l` ages better than "12 files");
  date-stamp any stated number ("108 tests as of v1.6.1, 2026-07-02").
- **Formulas appear only in canonical form**, owned by `monitorr-window-engine-reference`. Other
  skills and docs state the invariant and cross-reference; they never paraphrase a formula into
  a different form.
- One home per fact across the library: incident stories → `monitorr-failure-archaeology`;
  errata → this skill; env-var/Policy tables → `monitorr-config-and-flags`; API endpoint tables
  → `monitorr-plex-sonarr-reference`; release/git flow → `monitorr-change-control`. Needing a
  sibling's fact → give at most two sentences plus a cross-reference by exact directory name.
- Skills are docs: they travel to `main` with `.claude/` and are English-only, like everything
  in §2.

## When NOT to use

- Committing, merging, releasing, version bumps, tag mechanics, or the discipline gates for
  behavior-changing edits → `monitorr-change-control` (this skill only tells you *what* to write;
  that one tells you *when and how* it lands).
- Full incident narratives with commit hashes → `monitorr-failure-archaeology`.
- The window/grace/policy formulas themselves → `monitorr-window-engine-reference`.
- Env-var and Policy-field reference → `monitorr-config-and-flags`.
- Plex/Sonarr endpoint details → `monitorr-plex-sonarr-reference`.

## Provenance and maintenance

All facts verified against the repo at v1.6.1 (commit `c580f65`) on 2026-07-02; the 14 errata
fixes (`f9f3689`, `219cd2e`, same day) re-verified against the fixed files. The E-row commands
below now verify that each doc **stays** fixed — any output matching the retired row's stale
claim means the drift is BACK: reopen it as a new row in "Open errata".

| Fact | Command (from repo root) |
|---|---|
| Doc map completeness | `ls .claude/*.md .claude/skills/` and compare with §1 + `CLAUDE.md` Context table |
| Maintenance rules unchanged | `cat .claude/documentation.md` (§2 must still distill it faithfully) |
| E1 fix holds (auto normalize, docs agree) | `grep -n "normalize_to_pilot" src/monitorr/sync.py src/monitorr/web/routes.py; grep -n -i "automatic" .claude/architecture.md` (architecture.md must describe normalize as automatic) |
| E2 fix holds (policy editing shipped, not "pending") | `grep -n "save_series_policy" src/monitorr/web/routes.py; grep -n -A4 "Pending decisions" .claude/architecture.md` (no per-series-policy-editing bullet) |
| E3 fix holds (no button, sonarr.md says automatic) | `grep -rni "normalize" src/monitorr/web/templates/ src/monitorr/web/routes.py .claude/sonarr.md` (no "button"; sonarr.md says "automatic Normalize to Pilot") |
| E4 fix holds (no hardcoded version in CLAUDE.md) | `sed -n '3p' pyproject.toml; grep -n "pyproject" CLAUDE.md` (CLAUDE.md points at pyproject's `version`, no `vX.Y.Z` literal) |
| E5 fix holds (no TODOs; header says implemented) | `grep -rn "TODO\|FIXME" src/ \|\| echo clean; sed -n '3,4p' .claude/architecture.md` (header says fully implemented and tested) |
| E6/E7/E11 fixes hold (env vars documented) | `grep -n "FULL_SYNC_INTERVAL\|SYNC_ON_STARTUP\|MONITORR_HOST" README.md .env.example .claude/workflows.md src/monitorr/config.py` (all three vars present in README + .env.example; SYNC_ON_STARTUP worded as "full overdue") |
| E8 fix holds (English-only) | `grep -n "instala\|imagen\|Publica\|Monitoriza" Dockerfile .github/workflows/release.yml tests/conftest.py \|\| echo clean` (expect NO matches — clean) |
| E9 fix holds (changelog links complete) | `grep -c "^\[1\." CHANGELOG.md` vs `grep -c "^## \[" CHANGELOG.md` (counts must match, one ref per section) |
| E10 fix holds (generalized image tags) | `grep -n ":X.Y.Z" README.md; grep -n "type=semver" .github/workflows/release.yml` (README uses `:X.Y.Z`/`:X.Y`/`:X`/`:latest`, no frozen `:1.0.0`) |
| E12 fix holds (pattern list complete) | `grep -n "S\*E01" .claude/behavior.md README.md; grep -n '\^S' src/monitorr/engine/policy.py` (behavior.md lists all four incl. `S01`) |
| E13 fix holds (engine layout) | `ls src/monitorr/engine/; grep -n "policy.py\|actions.py" .claude/tech-stack.md` (tech-stack.md lists window/grace/policy/actions; no "Structure in README.md" ref) |
| E14 fix holds (TZ wording) | `grep -n -i "grace" README.md .env.example .claude/workflows.md; grep -n "datetime.now(UTC)" src/monitorr/engine/policy.py` (TZ lines say log/display only, grace math UTC) |
| Changelog exemplar intact | `sed -n '71,90p' CHANGELOG.md` (1.5.1 section) |
| Remote tags exist | `git ls-remote --tags origin` (fetch with `git fetch origin --tags`) |
| Skill frontmatter = dir name | `for d in .claude/skills/*/; do echo "$d"; head -2 "$d/SKILL.md"; done` |

When any command's output contradicts this file: the repo wins — update this skill (and the
errata table) in the same commit as whatever change caused the drift.
