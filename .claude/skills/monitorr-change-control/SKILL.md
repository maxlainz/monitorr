---
name: monitorr-change-control
description: Git/branch/release discipline for monitorr — dev-branch flow, immediate commit+push, merge-to-main message rules, docs-in-same-commit, the release checklist (version bump, tag, version guard, changelog extraction), the change-control gates and the three discipline rules (adversarial review for anchor changes, never trust volatile Plex state, mechanism-explaining changelog). NOT for changelog/doc house style → monitorr-docs-and-writing; NOT for running CI/tests locally → monitorr-build-and-env / monitorr-validation-and-qa; NOT for incident stories → monitorr-failure-archaeology.
---

# monitorr change control

Owner of the git/branch/release discipline and the change-control gates. Read this BEFORE any
commit, merge, release, or behavior-changing edit. Rules of record: `.claude/rules.md` (Git
section) and `.claude/workflows.md` (Merge to `main`, Publishing and release); this skill adds
the release checklist, the gate table, and the three discipline rules that were previously
unwritten — this file is their canonical home.

## 1. Branch model and daily cycle

- **`dev`** is the working branch. ALL development happens there. Never commit directly to
  `main` (`.claude/rules.md` "Git").
- After **each** code edit: commit on `dev` → `git push` **immediately**. Not batched at the end
  of a session.

```bash
git status                  # confirm you are on dev BEFORE editing
git checkout dev            # if not
# … edit code AND the affected docs (see §3) …
git add -A
git commit -m "message in English"
git push
```

- **`main`** is the stable/published branch. It receives ONLY explicit merges from `dev`, and
  only when the user explicitly asks.
- `CLAUDE.md` and `.claude/` (including this skill) **travel to `main`** — do not exclude them
  in the merge.

## 2. Merge to `main`: message rules

The merge message must summarize **everything new since the previous commit on `main`**. A bare
or generic subject like `Merge dev` / `merge to main` is **not acceptable**: the body must
enumerate every feature, fix and change reaching `main` (Added/Changed/Fixed, mirroring the
changelog). This was codified in commit `c00d130` ("docs(rules): forbid bare 'merge to main'
commit messages", 2026-05-31) after early merges violated it; `git show 63421c8` is a compliant
example (subject + full Changed/Release body).

```bash
git checkout main
git merge dev --no-ff
# edit the message: descriptive subject + body listing every change since the last main commit
git push
git checkout dev            # ALWAYS return to dev
```

## 3. Docs-in-same-commit rule

"The documentation is part of the change, not a later step" (`.claude/documentation.md`). If a
change invalidates a doc under `.claude/` or a user-facing doc (`README.md`, `CHANGELOG.md`),
update it **in the same commit** as the change. Trigger table (which change updates which doc)
is in `.claude/documentation.md` "When to update an existing doc".

**Known deviation (exception, NOT a precedent):** the v1.6.1 fix batch (2026-06-09/10) landed
`31fa9d5`, `9a189ef`, `beb757c`, `76b97c3`, `99ba5fc` as code+tests only, with the changelog and
doc updates consolidated afterwards in `eb9c1c1` ("docs: changelog for the deep-dive fixes;
document two accepted trade-offs"). Verify with `git show --stat <hash>` — none of those five
touch `.claude/` or `CHANGELOG.md`. The rule stands as binding: treat that batch as the known
historical exception, not as permission to defer docs. (Two commits in the same batch, `c72357d`
and `31a92b9`, DID carry their doc updates in-commit — the rule was achievable.)

## 4. Release procedure (checklist)

Releases are cut by pushing a semver tag `vX.Y.Z`; `.github/workflows/release.yml` does the
publishing. Everything below runs from the repo root.

1. **On `dev`: bump the version.** Edit `pyproject.toml` → `version = "X.Y.Z"` (line 3 as of
   v1.6.1). `uv.lock` records the project version too — run `uv lock` (or `uv sync`) so it
   follows (past release commits touch `uv.lock`; see `git show 7bd4095 --stat`).
2. **On `dev`: add the changelog section.** `CHANGELOG.md` must gain a section whose header is
   exactly `## [X.Y.Z] - YYYY-MM-DD` — the release workflow extracts the notes by matching
   `^## \[X.Y.Z\]` (see §5). No matching section ⇒ the changelog-derived release body is EMPTY.
   Entry style: §7.
3. **Commit and push the bump on `dev`:**
   ```bash
   git commit -am "chore: release vX.Y.Z" && git push
   ```
4. **Wait for CI green on `dev`** (`.github/workflows/ci.yml`: quality gates + multi-arch
   image build, no push). Local equivalent: the pre-push gate in `monitorr-validation-and-qa`
   §2, which mirrors ci.yml's order.
5. **Merge to `main`** per §2 (descriptive message; user must have asked).
6. **Tag from `main` and push the tag** — this is the publish trigger:
   ```bash
   git checkout main
   git tag vX.Y.Z && git push origin vX.Y.Z
   git checkout dev
   ```
7. **Verify the run**: the `Release` workflow must pass its **version guard** — it fails if the
   tag (minus `v`) does not equal `version` in `pyproject.toml`. If it fails, fix the version on
   `dev`, re-merge, delete and re-push the tag.
8. **Verify the artifacts**: multi-arch (`linux/amd64,linux/arm64`) image pushed to Docker Hub
   `maxlainz/monitorr` AND GHCR `ghcr.io/maxlainz/monitorr` with tags `:X.Y.Z`, `:X.Y`, `:X` and
   `:latest` (`:latest` only for non-prerelease tags, i.e. no `-` in the tag name); a GitHub
   Release exists with the changelog section as its body.

Required repo secrets (one-time setup): `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`; GHCR uses the
automatic `GITHUB_TOKEN` (`.claude/workflows.md` "Publishing and release").

## 5. What release.yml actually does (verified against the file)

Trigger: `push` of tags `v*.*.*`. Steps, in order:

| Step | Fact |
|---|---|
| Version guard | `tag="${GITHUB_REF_NAME#v}"` vs `grep -m1 '^version = ' pyproject.toml`; hard-fails on mismatch |
| Image tags | `docker/metadata-action@v5` semver patterns → `{{version}}`, `{{major}}.{{minor}}`, `{{major}}`, plus `latest` when the ref name contains no `-` |
| Build+push | `docker/build-push-action@v6`, `linux/amd64,linux/arm64`, provenance + SBOM, build-args `VERSION`/`VCS_REF`/`BUILD_DATE` (feed OCI labels and the `/version` endpoint) |
| Release notes | `awk` prints the lines between `^## \[X.Y.Z\]` and the next `^## \[` in `CHANGELOG.md` into `release_notes.md` |
| GitHub Release | `softprops/action-gh-release@v2` with `body_path: release_notes.md` and `generate_release_notes: true` (auto-generated notes are appended; if the changelog section is missing, ONLY those remain) |

Notes: `release.yml`'s comments/step names were Spanish until 2026-07-02 — fixed in the
English-only sweep (retired erratum E8 in `monitorr-docs-and-writing`); keep new edits English.
The bottom link-reference list of `CHANGELOG.md` was stale (stopped at 1.2.1) until 2026-07-02 —
fixed, refs now cover every section (retired erratum E9); it never affected note extraction,
which matches section headers only.

## 6. What requires change-control (gate table)

"Change-control" = the change must pass the applicable gate below before merge, on top of the
always-on CI gate (§4 step 4). Definitions: the *anchor* is the furthest-watched episode the
window is computed around; *GET/KEEP* are the windows ahead/behind it; *dry-run* is the master
switch (ON by default) that turns all Sonarr writes into no-ops.

| Change kind | Examples | Gate |
|---|---|---|
| Behavior change | window/GET/KEEP math, grace sweeps, sync anchoring, season-pack guard, webhook/poller detection | Discipline rules A and B (§6.1, §6.2) + regression test + changelog entry per rule C (§7) + doc update in same commit (§3) |
| Schema / migration | anything in `MIGRATIONS` in `src/monitorr/db.py` | **Append-only**: add a new entry at the END of `MIGRATIONS`; NEVER edit an already-published entry (index+1 = `PRAGMA user_version`; editing one desyncs every existing install). Migration test required (see `tests/test_db.py`; e.g. migration 4 forced a full sync in v1.5.1) |
| Defaults change | Policy defaults (grace days, GET/KEEP counts), config defaults in `src/monitorr/config.py`, dry-run default | Changelog **upgrade note** describing the effect on existing installs (exemplar: the 1.2.0 "Upgrade note" for the new `completed` grace with dry-run OFF) + README/`.claude/` tables updated in same commit; defaults surface is owned by `monitorr-config-and-flags` |
| Release-flow / CI change | `release.yml`, `ci.yml`, merge/tag procedure | Update `.claude/workflows.md` (and `.claude/rules.md` if it changes a rule) in the same commit; keep §4/§5 of this skill in sync |
| Docs-only change | wording, errata | No gate beyond §2/§3; style is owned by `monitorr-docs-and-writing` |

### 6.1 Discipline rule A — anchor changes need adversarial review

Trigger: ANY change touching anchor/sync/window code —

- `src/monitorr/engine/window.py` (anchor floor, GET/KEEP, `_is_armed`)
- `src/monitorr/sync.py` (full/incremental sweep, watermark, re-search)
- `src/monitorr/engine/grace.py` (KEEP floor, sweep order)
- `src/monitorr/plex/client.py` history code (play-history reads that feed the anchor)

Checklist (all items required before merge):

- [ ] Argue the change against **every** incident in `monitorr-failure-archaeology`'s catalog:
      for each one, state why this change cannot recreate that mechanism. The re-download loop is
      SELF-AMPLIFYING (each wrong re-download re-enters the on-disk state and re-advances the
      anchor), so "probably fine" is not a passing answer.
- [ ] Check the fix-spawns-fix pattern: `4245d21` (v1.4.1) itself caused the v1.4.2 bug
      (`5ce7fab`). Ask: what NEW volatile input does this change introduce, and what happens
      when it is empty/stale/renumbered?
- [ ] Add a **regression test** reproducing the scenario the change addresses (see the v1.5.1
      release commit `b40c596` body for the expected shape: window + sync + migration tests).
- [ ] Confirm rule B below holds on every destructive path the change touches.

### 6.2 Discipline rule B — never trust volatile Plex state

The persisted monotonic watch store (`episode_watch` table, written by poller/webhook/sync) is
the **only** authority for destructive decisions (delete file, unmonitor, cancel queue).
Volatile — and therefore NEVER sufficient on their own to drive deletion/unmonitoring:

- `allLeaves` (Plex's current on-disk episode list; trimmed files vanish from it),
- Plex `ratingKey`s (reassigned when a series is removed and re-added),
- pre-cascade Sonarr episode snapshots (Sonarr cascades a season's `monitored` flag to its
  episodes, invalidating any snapshot taken before the write).

Checklist for any change to a destructive path:

- [ ] Every anchor/floor used by the path is derived from (or clamped against) `episode_watch`.
- [ ] No decision keys on a `ratingKey`; correlation uses TVDB id or `grandparentTitle`
      (details: `monitorr-plex-sonarr-reference`).
- [ ] Sonarr episode state is re-fetched after any season-level `monitored` write before it is
      read again (`31fa9d5`).
- [ ] A failed/empty external read degrades to "do nothing / stay full", never to "advance
      state" (`beb757c`: a blind sweep advances nothing).

### 6.3 Discipline rule C — changelog entries must explain the mechanism

Every user-visible fix/change gets a `CHANGELOG.md` entry narrating **cause → mechanism → fix**,
not just the symptom. The changelog is the project's memory — the next session reconstructs
intent from it. See §7 for the anatomy.

## 7. Commit-message and changelog-entry style

Commits: English, short descriptive subject; conventional prefixes (`feat:`, `fix:`, `docs:`,
`chore:`, `refactor:`) welcome, not mandatory (`.claude/rules.md`). For nontrivial fixes, put
the mechanism in the body (exemplar: `git show e572eff` / release body `git show b40c596`).

Changelog anatomy — exemplar: the `[1.5.1]` entry in `CHANGELOG.md`. It leads with the
user-visible outcome in bold ("Watched series no longer re-download themselves"), then narrates
the exact mechanism (which two sources missed the watch, why the anchor slid, why the loop
self-amplified) and names the enforced fix and its scope ("floors the anchor at the furthest
episode ever recorded as watched… enforced for every path"). Match that bar; full house style
(tense, bolding, section order, link refs) is owned by `monitorr-docs-and-writing`.

## 8. When NOT to use this skill

| Need | Go to |
|---|---|
| Changelog/doc wording, house style, the standing errata table | `monitorr-docs-and-writing` |
| Running lint/type/tests locally, uv/CI toolchain details | `monitorr-build-and-env` |
| Writing/running the test suite, respx patterns, pre-push gate details | `monitorr-validation-and-qa` |
| Full incident stories behind the discipline rules | `monitorr-failure-archaeology` |
| The invariants themselves (formulas, why) | `monitorr-architecture-contract`, `monitorr-window-engine-reference` |
| Env vars / Policy defaults surface | `monitorr-config-and-flags` |

## 9. Provenance and maintenance

Facts here verified against the repo at v1.6.1 (commit `c580f65`, 2026-07-02). Re-verify before
relying on them:

| Fact | Re-verify with |
|---|---|
| Branch/merge rules text | `sed -n '6,20p' .claude/rules.md` |
| Merge-rule codification commit | `git show c00d130 --stat` |
| Docs-in-same-commit rule | `sed -n '1,10p' .claude/documentation.md` |
| v1.6.1 doc-deviation commits | `git show --stat 31fa9d5 9a189ef beb757c 76b97c3 99ba5fc eb9c1c1` |
| Current version / guard input | `grep -m1 '^version = ' pyproject.toml` |
| Version guard + awk extraction + tag patterns | `cat .github/workflows/release.yml` |
| CI gate commands | `cat .github/workflows/ci.yml` |
| Changelog section headers present | `grep -n '^## \[' CHANGELOG.md` |
| Tags exist on the remote | `git ls-remote --tags origin` (a fresh clone may not have fetched them) |
| Migrations append-only comment + list | `sed -n '1,10p' src/monitorr/db.py` |
| Release checklist steps | `sed -n '99,141p' .claude/workflows.md` |

If any command's output disagrees with this skill, the repo wins — fix this skill in the same
commit as the change that moved it (§3).
