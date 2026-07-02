---
name: monitorr-research-methodology
description: >-
  HOW to investigate and decide: the evidence bar (one mechanism must explain ALL observations
  incl. negatives and survive adversarial refutation), the predict-numbers-before-running
  worksheet, the idea lifecycle (adopt or documented retirement; no zombie ideas). Owns the
  Retired-ideas ledger. Use for root-cause diagnosis, design evaluation, or deciding if a
  hypothesis is proven. NOT for: symptom runbooks → monitorr-debugging-playbook; engine
  prediction recipes → monitorr-proof-and-analysis-toolkit; frontier items →
  monitorr-research-frontier; incident stories → monitorr-failure-archaeology.
---

# monitorr research methodology

This skill defines the METHOD: how a claim ("the bug is caused by X", "design Y is safe") earns
acceptance in this repo, how to make evidence gathering falsifiable, and how ideas move from
spark to adoption or documented retirement. The examples are drawn from monitorr's real incident
history (hashes verified against `git log` and `CHANGELOG.md` at v1.6.1, 2026-07-02), but the
content is domain-independent method — the domain references live in the sibling skills.

Terms used below, defined once: **anchor** = the furthest-watched episode, the point the
GET/KEEP window is computed around. **GET** = the N episodes ahead of the anchor kept
monitored/downloaded; **KEEP** = the N behind kept on disk. **allLeaves** = Plex's listing of a
show's episodes *currently in the library* — it forgets episodes whose files were deleted.
**watermark** = the newest play timestamp already swept from Plex history; an **incremental
sync** reads only history newer than it, a **full sync** re-reads everything. **ratingKey** =
Plex's per-item id, reassigned when an item is removed and re-added. **dry-run** = the master
switch (default ON) that turns all Sonarr writes into no-op previews.

## 1. The evidence bar

A diagnosis or design claim is **accepted** only when ALL of the following hold:

1. **One mechanism explains all observations.** A single causal story accounts for every
   positive observation (what happened, with the right magnitudes and timing).
2. **It explains the negatives.** The same mechanism must predict what did NOT happen — which
   configurations, code paths, or shows were unaffected. A mechanism that only explains the
   positives is a curve fit, not a diagnosis.
3. **It survived adversarial refutation.** You must genuinely try to kill your own hypothesis:
   write down at least two rival mechanisms, derive an observation that would discriminate
   ("if the rival were true, we would see X"), check it, and DOCUMENT the attempts (in the
   investigation notes, the commit message, or the changelog entry — the changelog's
   cause → mechanism → fix format, per `monitorr-change-control` discipline rule (c), is where
   the surviving mechanism ends up).
4. **No coincidences required.** If the mechanism needs two independent rare events to line up,
   it is almost certainly wrong — keep looking (see anti-patterns, §5).

Until all four hold, the claim's status is HYPOTHESIS, and no fix or design built on it may
merge (route through `monitorr-change-control`).

### Worked example: the 1.5.1 anchor regression (commit e572eff)

Facts verified against `CHANGELOG.md` §[1.5.1] and `git show e572eff`. Full incident story:
`monitorr-failure-archaeology`; this section shows the bar being applied.

**Symptom**: after v1.5.0 introduced incremental sync (c315cda), an already-watched series
re-monitored and re-downloaded its own back-catalog, N = `get_count` episodes per sync cycle.

**Accepted mechanism**: the incremental sync derived the anchor only from the cycle's *live*
Plex read (on-disk allLeaves + the history delta since the watermark), ignoring the persisted
`episode_watch` store. An episode whose file had been trimmed AND whose play predated the
watermark was invisible to both live sources, so the anchor fell back to the furthest *on-disk*
watch and the window slid backward.

**The bar, applied**:

| Observation | Does the mechanism explain it? |
|---|---|
| POSITIVE: exactly N episodes re-downloaded per cycle | Yes — the slid-back anchor makes the window math request `GET = get_count` episodes ahead of the wrong point, once per sync |
| POSITIVE: the loop is self-amplifying | Yes — each wrongly re-downloaded episode re-enters allLeaves, re-advancing the live-derived anchor, so the next cycle marches on |
| NEGATIVE (implied by the mechanism; not independently recorded): a full-sync cycle anchors correctly — but the regression resumes on the next incremental cycle | Yes — a full sync reads the ENTIRE play history (`since=None` in `src/monitorr/sync.py`), so pre-watermark plays are visible and the sweep repopulates the persisted store; the anchor computes correctly that cycle. The regression resumes afterwards because the incremental derivation itself ignores the store |
| NEGATIVE: only shows with BOTH a trimmed file AND a pre-watermark play hit it | Yes — file still on disk → allLeaves carries the watch and anchors correctly; play newer than the watermark → the incremental history delta carries it. Either source alone is enough; only the intersection of the two blind spots fails |

The negatives are what made the diagnosis certain: any rival mechanism had to explain why full
syncs were clean and why the two preconditions were both required.

**Adversarial refutation attempts a rigorous investigator runs** (each is a prediction the
rival mechanism makes, checked against reality). The following probes are reconstructed from
the accepted mechanism — no record of the original probes survives in-repo; each "Observed" is
what the mechanism entails, not recorded history:

1. *Rival: watermark timing bug* ("the watermark was stamped too new, burying recent plays").
   If true, one manual full sync should cure a show permanently, because the missed plays would
   be re-swept and the watermark corrected. Observed: the regression RESUMES on the next
   incremental cycle even after a full sync — because the anchor derivation itself ignores the
   persisted store. Rival killed; also confirms the fix must change the derivation, not the
   watermark.
2. *Rival: season-pack upgrade grabs resurfacing* (the dd889ee-class bug: over-broad monitoring
   lets Sonarr grab pack "upgrades"). If true, episodes would arrive in whole-season chunks with
   an all-episodes-monitored fingerprint, independent of `get_count`. Observed: exactly
   `get_count` episodes re-monitored per cycle — the count fingerprint matches the window
   formula, not pack grabs. Rival killed.
3. *Rival: watch data lost from the DB* ("the plays are gone, so no code could anchor right").
   If true, a read-only dump of `episode_watch` would show the rows missing. Observed: the rows
   are present — the persisted store is complete; the code just never consulted it. Rival
   killed, and this attempt located the fix: floor the anchor at the persisted furthest watch
   (`apply_window`, formula in `monitorr-window-engine-reference`).

Only after the rivals died was the mechanism accepted and the fix designed — and the fix
narrates exactly this mechanism in `CHANGELOG.md` §[1.5.1] and the e572eff commit message.

## 2. The predict-numbers-before-running worksheet

Every evidence-gathering run must be falsifiable: write the predicted numbers/observables
BEFORE running the command. **A prediction written after seeing the output is not a
prediction** — it is a description, and it counts for nothing at the decision gate.

This skill owns the general form. The engine-specific instantiation — which counts to predict
for `apply_window`, grace sweeps, and sync cycles, with worked examples — lives in
`monitorr-proof-and-analysis-toolkit`; use its recipes to fill in the "predicted" row for
window-engine questions.

Copyable template (one worksheet per run; keep filled worksheets in the investigation notes):

```markdown
### Worksheet <id> — <date>

**Hypothesis**: <one falsifiable sentence>
**Mechanism**: <the causal story that makes the prediction; 2-4 sentences>

**Predicted BEFORE running** (exact numbers, log lines, DB rows — no ranges wider than the
mechanism justifies):
- <e.g. "sync log shows mode=incremental and exactly 0 shows re-scanned">
- <e.g. "SELECT COUNT(*) FROM episode_watch WHERE tvdb_id=X returns 42">
- <e.g. "log line 'anchor floor correction' appears exactly once, for S03E07">

**Command** (single, copy-pasteable from repo root):
`<command>`

**Actual**: <paste the relevant output lines verbatim>

**Verdict**: MATCH | PARTIAL | REFUTED
**If PARTIAL/REFUTED — what the mismatch teaches**: <which assumption in the mechanism was
wrong; what the next worksheet must test. Never explain a mismatch away after the fact —
revise the mechanism and predict again.>
```

Rules:

- One variable per worksheet. If you change the code AND the config between runs, no verdict is
  attributable (anti-pattern, §5).
- Predict numbers, not directions. "It will re-download fewer episodes" is weak; "it will
  re-download exactly 0 and the log will show one anchor-floor correction" is a real test.
- A MATCH on positives alone is not acceptance — run the negative-prediction worksheet too
  ("shows without trimmed files show NO correction line").
- Read-only evidence first: prefer log inspection and read-only DB queries (safe access
  patterns: `monitorr-diagnostics-and-tooling`) before any run that writes. Dry-run (default
  ON) is the safety net for anything touching Sonarr writes.

## 3. The idea lifecycle

Every idea — bug hypothesis, design change, new capability — moves through these stages. No
stage may be skipped, and every idea must END in one of the two terminal states.

| Stage | What it is | Exit criterion |
|---|---|---|
| 1. Spark | The raw idea, one sentence | Written down at all |
| 2. Scoped question | Reframed as a FALSIFIABLE question ("does X cause Y?", "would Z hold under W?") with explicit success/kill criteria | A rival answer would be distinguishable by an observation |
| 3. Evidence gathering | One or more §2 worksheets; adversarial refutation attempts (§1.3) for anything reaching a claim | Evidence bar (§1) met, or the idea is killed by a worksheet |
| 4. Decision gate | Explicit verdict against the bar and against cost/risk (for anchor/sync/window ideas, the adversarial review of `monitorr-change-control` discipline rule (a) applies here) | ADOPTED or RETIRED — nothing else |
| 5a. ADOPTED | Goes through `monitorr-change-control`: implementation + regression tests + docs in the SAME change; changelog narrates the mechanism | Merged per change-control |
| 5b. DOCUMENTED RETIREMENT | A dated entry in the Retired-ideas ledger (§4): the idea, why it was killed, and what evidence would reopen it | Ledger entry written |

**No zombie ideas.** An idea that is neither adopted nor retired when its investigation ends is
explicitly PARKED: a ledger entry (§4) with status `PARKED` and a concrete reopen condition
("reopen if/when X is observed" or "after Y ships"). Parked without a reopen condition = zombie
= not allowed. Retired ideas are memory, not garbage: the ledger is what stops a future session
from re-investigating a dead end from scratch — or from missing that its preconditions changed.

New-capability ideas additionally check `monitorr-research-frontier` first (the inventory of
frontier items and the claims-evidence rule) so the same idea is not tracked in two places: the
frontier skill owns WHAT is on the frontier; this skill owns HOW any one item is investigated.

## 4. Retired ideas (ledger)

This section is the single home of retired/parked idea entries (chosen over the frontier skill
so lifecycle state and lifecycle rules live together; `monitorr-research-frontier` tracks open
items only). Append entries here, newest first, in the same commit as the decision (per
`monitorr-change-control`). Entry format: date · idea · status · why killed/parked · reopen
condition.

### 2026-05-30 — Manual "Normalize to Pilot" button — RETIRED (exemplar)

- **Idea**: a per-series UI button + `POST /series/{tvdb_id}/normalize` route letting the user
  manually reset an unwatched managed show's window to the pilot. Shipped in the MVP.
- **Why killed**: made redundant one day later — normalization runs automatically on every sync
  for every managed show with no recorded viewing, so the manual trigger had no remaining use
  case and was pure UI/route surface to maintain. Removed in commit 9468870 (2026-05-30):
  route, button, and the Series-page "Pilot" column deleted; `normalize_to_pilot` kept as an
  internal sync action. (Note: `.claude/architecture.md` is stale here — it still describes
  normalize as manual/opt-in; the code truth is automatic-only. See `monitorr-docs-and-writing`.)
- **Reopen if**: a policy is added where auto-normalize is disabled per series
  (`auto_normalize=false`) AND users demonstrably need a one-off reset without re-enabling it —
  i.e. the redundancy argument breaks. Until then, the automatic path covers every case.

This is what a correct retirement looks like: the idea was real, shipped, evaluated against
reality, killed for a stated reason, and the kill is reversible under a named condition.

## 5. Anti-patterns

Each row is a way investigations go wrong in this repo, with the historical near-miss that
motivates it where one exists (hashes: `monitorr-failure-archaeology` for the full stories).

| Anti-pattern | What it looks like | Historical motivation |
|---|---|---|
| Fix-without-mechanism | "It works now" — shipping a fix whose OWN mechanism was never adversarially reviewed | The 4245d21 → 5ce7fab chain: 4245d21 correctly fixed back-catalog detection by adding a play-history query, but keyed it per show on `metadataItemID={ratingKey}`; nobody asked "what kills THIS mechanism?" — ratingKey changes on re-add, the query returned empty, and the fix spawned the v1.4.2 bug (5ce7fab). Every fix gets refutation attempts too |
| Trusting one observation source | Deriving a conclusion (or an anchor) from a single view of the world | The allLeaves lesson (4245d21): watch detection used only allLeaves, which reflects the CURRENT library — deleted files made viewing invisible and the window slid back. Corollary: volatile sources (allLeaves, ratingKeys, pre-cascade snapshots) never drive destructive decisions alone (`monitorr-change-control` rule (b)) |
| Changing two variables at once | Editing code AND config (or two code sites) between worksheet runs, then attributing the delta | No single incident, but the four-path anchor saga (351bc8a, 4245d21→5ce7fab, e572eff, 9a189ef) shows how hard attribution already is with one variable; two makes every verdict void |
| Skipping the negative observations | Accepting a mechanism because it explains what happened, never asking why the unaffected cases were unaffected | 1.5.1 (§1): "full syncs are clean" and "both preconditions required" were the observations that discriminated the true mechanism from the three rivals |
| Accepting a mechanism that requires a coincidence | "It only breaks when A and B independently happen at the same moment" with no causal link between A and B | 1.5.1 again, as a near-miss in reverse: the two required preconditions (trimmed file + pre-watermark play) LOOK like a coincidence, but the mechanism links them — they are the two blind spots of the same live-only derivation. If your mechanism cannot link its preconditions, it is incomplete |

## 6. How this skill composes with the siblings

| Situation | Order of operations |
|---|---|
| Live bug | `monitorr-debugging-playbook` first (symptom → runbook); when the runbook reaches a root-cause claim, THIS skill's evidence bar (§1) decides whether the diagnosis is accepted; the worksheet (§2) structures each probe |
| New capability idea | `monitorr-research-frontier` for the inventory and positioning rules; THIS skill's lifecycle (§3) for how the item is investigated and closed out |
| Any adoption | `monitorr-change-control`, always — implementation + regression tests + docs in the same change; anchor/sync/window changes additionally get its adversarial review |
| Predicting engine behavior | `monitorr-proof-and-analysis-toolkit` for the concrete recipes; this skill only mandates that the prediction precede the run |
| Writing the surviving mechanism down | `monitorr-docs-and-writing` for changelog house style; the mechanism narration itself is required by §1.3 |

## When NOT to use

- Diagnosing a live symptom step by step → `monitorr-debugging-playbook` (come back here for
  the acceptance decision).
- What actually happened in a past incident → `monitorr-failure-archaeology`.
- The exact window/grace formulas or code anchors → `monitorr-window-engine-reference`.
- Which frontier items exist and what may be claimed publicly → `monitorr-research-frontier`.
- Engine-specific "predict this sync's numbers" recipes → `monitorr-proof-and-analysis-toolkit`.
- Committing/merging/releasing an adopted idea → `monitorr-change-control`.
- Safe read-only DB inspection commands for worksheets → `monitorr-diagnostics-and-tooling`.

## Provenance and maintenance

Verified against v1.6.1 (commit c580f65) on 2026-07-02. Re-verification commands (repo root):

| Fact | Re-verify with |
|---|---|
| 1.5.1 mechanism wording (§1 example) | `git show e572eff --stat` and `grep -A 20 '\[1.5.1\]' CHANGELOG.md` |
| Full sync reads entire history (`since=None`) | `grep -n 'since = None if full' src/monitorr/sync.py` |
| Anchor-floor fix lives in `apply_window` | `grep -n 'floor' src/monitorr/engine/window.py` |
| Manual normalize button removal (§4 entry) | `git show 9468870 --stat` (route + button + docs removed) |
| No manual normalize route today | `grep -n '/normalize' src/monitorr/web/routes.py` (expect no output; plain `normalize` hits only the `auto_normalize` policy field) |
| 4245d21 → 5ce7fab chain | `git log --format='%h %s' 4245d21 -1; git log --format='%h %s' 5ce7fab -1` and CHANGELOG §[1.4.1]/§[1.4.2] |
| Incremental sync introduced in v1.5.0 | `git log --format='%h %s' c315cda -1` |
| Sibling skill names still exist | `ls .claude/skills/` |

Maintenance: append every new retirement/parking to §4 in the same commit as the decision. If a
retired idea is reopened, do not delete its entry — add a dated `REOPENED` line under it citing
the reopen condition that fired. If the evidence bar itself is ever weakened or strengthened,
that is a discipline change: route it through `monitorr-change-control` and update the composing
skills' cross-references in the same commit.
