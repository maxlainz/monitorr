# Code, git and style rules

Entry doc: read **before any edit**. To maintain this and the rest of the
documentation, see [`documentation.md`](documentation.md).

## Git

- Working branch: `dev`. Never commit directly to `main`.
- After each code edit → commit on `dev` → `git push` immediately.
- Merge to `main` only if the user explicitly asks. The message must summarize everything
  new since the previous commit on `main`. A bare/generic subject like `Merge dev` or
  `merge to main` is **not acceptable**: the body must list every feature, fix and change
  that arrives at `main`.
- `CLAUDE.md` and `.claude/` **do** go into `main` in this repo (they are not excluded in the merge).
- Commits in English, short and descriptive message. Conventional prefixes (`feat:`,
  `fix:`, `docs:`, `chore:`, `refactor:`) are welcome but not mandatory.

## Language

- Documentation, comments (when they exist), commit messages and UI: English.
- Code, identifiers, file names, branches and environment variables: English.

## Code style

- Modules by domain (not by horizontal layer).
- Small functions; one function does one thing.
- Explicit imports. No `import *`.
- No debug `print()`/`console.log` in production code; use a logger.
- Configuration (ports, paths, intervals, credentials) always via env vars or config,
  never hardcoded.

## Comments

- By default, no comments.
- Only add one when the WHY is not obvious: external constraint, workaround for a specific
  bug, subtle invariant, non-obvious rate limit.
- Never explain the WHAT — the function name already says it.

## Abstractions

- No premature abstractions. Three similar lines are preferable to a generic helper
  if there is no real reuse.
- Do not add error handling for impossible scenarios.
- Validation only at system boundaries (user input, network responses, external payloads).
- No feature flags or backward-compatibility shims while there are no external users.
  Change the code directly.

## Language and typing

- **Python 3.12**, pinned in [`pyproject.toml`](../pyproject.toml) (`requires-python`) and in
  [`.python-version`](../.python-version). Deps with `uv` and `uv.lock` lockfile.
- **Type hints mandatory**; mypy in **strict mode** (config in `pyproject.toml`). The code
  must pass `mypy .` with no errors.
- **Linter + formatter: Ruff** (config in `pyproject.toml`). Ruff's formatting is the source
  of truth; do not introduce another formatter.
- **I/O validation at boundaries: Pydantic v2** (and `pydantic-settings` for env vars). Validate
  external payloads (Plex, Sonarr, forms) on the way in, not internally.
- Full stack and *why*: [`tech-stack.md`](tech-stack.md).

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on push to `dev`/`main` and on PRs:

- **quality**: `ruff check`, `ruff format --check`, `mypy`, `pytest` (installs with `uv sync --frozen`).
- **image**: builds the multi-arch image (`linux/amd64,linux/arm64`) with buildx (no push).

Single command locally before pushing (see [`workflows.md`](workflows.md)):
`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`.
