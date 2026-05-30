# How and when to document

Maintenance rule for this repo's documentation. **The documentation is part of the
change, not a later step.** If a change invalidates a doc, the doc is updated
**in the same commit**.

## Where what lives

- **`CLAUDE.md`** (root) = cover page: project intro, **Rules** and the **Context** table
  that indexes all the `.claude/` docs with a "when to read". No technical detail here.
- **`.claude/*.md`** = the detail, **one file per domain/concern** (not per layer).
  E.g.: `data-model.md`, `api.md`, `ui-features.md` — never `backend.md` / `frontend.md`.
- **Single source of truth**: each fact lives in a single place. Do not duplicate a rule
  or a command across two docs; link to the canonical doc with a relative link.

## When to update an existing doc

Update the affected doc **in the same commit** as the change, on any of these
triggers:

- Change of architecture, components or data flow → `architecture.md`.
- Stack, dependencies or directory structure → `architecture.md` (or `tech-stack.md` if it exists).
- New development, build, test or deploy command → `workflows.md`.
- New code, git, style or language rule → `rules.md`.
- Change in the data schema or in a route/endpoint → that domain's doc (`data-model.md`, `api.md`).
- Change in the git flow, releases or CI → `rules.md` / `workflows.md` / `versioning.md`.

## When to create a new doc (vs. expanding an existing one)

Create a new `.claude/<domain>.md` when **a domain appears that doesn't exist yet** and
starts having its own rules, formats or pitfalls. If you're just adding a detail to a
domain already covered, **expand the existing doc** — don't fragment.

Planned expansion menu (create when the code justifies it):

| Candidate doc | Create when… |
|---|---|
| `tech-stack.md` | The stack is fixed: versions, layout, technical decisions |
| `data-model.md` | Persistence appears: entities, schema, invariants |
| `api.md` | An API is exposed: routes, contracts, auth, response format |
| `ui-features.md` | There is a UI: catalog of pages, actions, what is configured via web vs env |
| `versioning.md` | Releases are cut: SemVer, conventional commits, bump, tags |
| `<feature>.md` | A feature has enough logic/pitfalls to deserve its own page |

## When adding a new doc

1. Create the file in `.claude/` with the format below.
2. **Register its row** in the *Context* table of `CLAUDE.md` with a clear "when to read".
3. If it replaces content that was in another doc, move it and leave only a link.

## Format of each doc

- `#` title on the first line; sections with `##` per topic.
- Links between docs in relative markdown format: `` [`x.md`](x.md) `` (and `../` for
  files outside `.claude/`, e.g. `` [`ci.yml`](../.github/workflows/ci.yml) ``).
- **Explain the why**, not the what: invariants, trade-offs, pitfalls, external
  constraints. Avoid narrating what the code already says.
- Compact and directive. No filler.

## Language and style

- Documentation, commit messages and UI: **English**.
- Code, identifiers, file names, branches and env vars: **English**.
- Doc style mirrors that of `rules.md`: short sentences, lists, tables for indexes.
