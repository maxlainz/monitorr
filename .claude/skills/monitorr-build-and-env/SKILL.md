---
name: monitorr-build-and-env
description: >-
  Toolchain owner for monitorr: uv environment setup, ruff/mypy/pytest configuration, the
  CI quality gates and pre-push command, dependency management (PEP 735 dev group, frozen
  uv.lock rule), Docker two-stage build, CI jobs. Use for "uv sync fails", "CI red",
  "lockfile out of date", "mypy strict errors", "ruff format", "Docker build breaks",
  "add a dependency". NOT for running/operating the app → monitorr-run-and-operate; NOT for
  writing tests → monitorr-validation-and-qa; NOT for releasing → monitorr-change-control.
---

# monitorr — build, toolchain and environment

Everything about how this repo is built and checked: uv, ruff, mypy, pytest, the Docker image
and CI. All commands run from the repo root. Ground truth files: `pyproject.toml`,
`uv.lock`, `.python-version`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`
(build parts), `Dockerfile`, `.dockerignore`.

## 1. Environment setup from zero

```bash
uv sync
```

This creates `.venv/` from `uv.lock` (the committed cross-platform lockfile) and installs the
project in editable mode plus the `dev` dependency group (uv installs dependency-groups by
default). That is the entire setup — no pip, no requirements.txt, no manual venv activation
(`uv run <cmd>` executes inside `.venv` automatically).

Tool versions and Python pins:

| What | Where pinned | Value |
|---|---|---|
| uv (CI) | `.github/workflows/ci.yml` (`astral-sh/setup-uv@v5` → `version:`) | `0.11.7` |
| uv (Docker) | `Dockerfile` builder base image tag | floating (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, no version pin) |
| Python interpreter | `.python-version` | `3.12` (uv picks/downloads this for `.venv`) |
| Python floor | `pyproject.toml` `requires-python` | `>=3.12` |
| Python (Docker) | `Dockerfile` both stages | 3.12 (`uv:python3.12-…` builder, `python:3.12-slim` runtime) |
| mypy target | `pyproject.toml` `[tool.mypy] python_version` | `3.12` |
| ruff target | `pyproject.toml` `[tool.ruff] target-version` | `py312` |

If you bump Python, all six rows above must move together.

## 2. Quality gates — the pre-push command

CI runs four gates (ruff lint, format check, mypy, pytest) after `uv sync --frozen`
(`.github/workflows/ci.yml`, `quality` job). Before every push, run the pre-push gate — the
exact command line and CI order live in `monitorr-validation-and-qa` §2, which mirrors ci.yml.
Expect all four to pass green. If the format gate fails, fix with `uv run ruff format .`
(never hand-format). All tool config lives in `pyproject.toml`:

| Tool | Config (pyproject.toml) | Meaning |
|---|---|---|
| ruff | `src = ["src", "tests"]` | first-party import detection for `I` (isort) rules |
| ruff | `line-length = 100` | applies to lint (E501) and formatter |
| ruff | `select = ["E", "F", "I", "UP", "B", "ASYNC", "SIM"]` | pycodestyle, pyflakes, isort, pyupgrade, bugbear, async, simplify |
| ruff | `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["fastapi.Form", "fastapi.Query", "fastapi.Path"]` | silences B008 for FastAPI's call-as-default-value idiom |
| mypy | `files = ["src", "tests"]`, `strict = true` | strict typing over source AND tests — no untouched test code |
| pytest | `asyncio_mode = "auto"`, `testpaths = ["tests"]` | async tests need no marker; bare `pytest` collects only `tests/` |

## 3. Dependency management

- Runtime deps: 8 packages in `[project] dependencies`, all lower-bound pins (`>=`) — exact
  versions are resolved into `uv.lock`.
- Dev deps (`ruff`, `mypy`, `pytest`, `pytest-asyncio`, `respx`) live in the PEP 735
  `[dependency-groups]` table, **not** in `[project.optional-dependencies]`. Consequence:
  `pip install .[dev]` installs **nothing** extra — dev tooling is only reachable via uv
  (`uv sync` includes the group by default; Docker excludes it with `--no-dev`).
- **Frozen-lockfile rule**: CI installs with `uv sync --frozen` and Docker with
  `uv sync --frozen --no-dev --no-editable`. `--frozen` refuses to run if `uv.lock` no longer
  matches `pyproject.toml`. Therefore: any edit to dependencies in `pyproject.toml` without
  regenerating the lockfile breaks BOTH CI and the Docker build.

To change dependencies, either:

```bash
uv add <package>          # edits pyproject.toml AND uv.lock together
# or, after hand-editing pyproject.toml:
uv lock                   # regenerate uv.lock; commit it in the same commit
```

Commit `pyproject.toml` and `uv.lock` together, always (see monitorr-change-control for the
commit/push discipline).

## 4. Docker build

Two stages in `Dockerfile`:

1. **builder** — `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`. Env flags:
   `UV_COMPILE_BYTECODE=1` (precompile .pyc), `UV_LINK_MODE=copy` (real files, no hardlinks
   across layers), `UV_PYTHON_DOWNLOADS=never` (use the image's Python only). Copies
   `pyproject.toml uv.lock README.md LICENSE` + `src/` (README/LICENSE are required by
   hatchling because `readme` and `license-files` are declared), then
   `uv sync --frozen --no-dev --no-editable` builds a self-contained `/app/.venv` with the
   monitorr wheel installed (not editable — the image ships no `src/`).
2. **runtime** — `python:3.12-slim`. Only `/app/.venv` is copied over;
   `PATH=/app/.venv/bin:$PATH` makes the `monitorr` console script resolvable.

Build-args and version flow (important — three different things):

| Piece | Source | Consumed by |
|---|---|---|
| `ARG VERSION` (default `0.0.0+dev`) | release.yml build-args (metadata-action version) | OCI label `org.opencontainers.image.version` ONLY |
| App's reported version | `pyproject.toml` `version` via package metadata (`src/monitorr/__init__.py`: `importlib.metadata.version("monitorr")`) | `/version` endpoint, UI footer |
| `ARG VCS_REF` / `ARG BUILD_DATE` | release.yml (`github.sha`, metadata-action created label) | OCI labels + baked as env `MONITORR_BUILD_SHA` / `MONITORR_BUILD_DATE` → pydantic `Settings.build_sha/build_date` (`src/monitorr/config.py`, `env_prefix="MONITORR_"`) → `/version` endpoint (`src/monitorr/main.py`) |

So passing `--build-arg VERSION=…` never changes what the app reports — only the label. The
app version changes only by bumping `pyproject.toml` (release process: monitorr-change-control).

Runtime-stage details (verify against `Dockerfile`):

- `apt-get install gosu` + `groupadd/useradd app` + `mkdir /config && chown app:app`. The
  container starts as **root**; `docker-entrypoint.sh` (ENTRYPOINT) remaps `app` to
  `PUID`/`PGID`, chowns `/config`, and drops privileges via `gosu` before exec'ing the CMD
  (`monitorr`, the console script). Operating this is monitorr-run-and-operate's domain.
- `ENV MONITORR_CONFIG_DIR=/config`, `EXPOSE 8080`, `VOLUME /config`.
- `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3` — a Python
  one-liner GET to `http://localhost:8080/health`, exit 0 iff HTTP 200 (no curl in the image).
- `.dockerignore` trims the build context (`.git`, `.github`, `.claude`, `.venv`, `config`,
  caches, `*.db*`, `tests`, `CLAUDE.md`); the Dockerfile also copies only explicit paths.
- Known erratum: `Dockerfile` comments and its OCI description label are Spanish, as are
  release.yml comments — violates the English-only rule (`.claude/rules.md` is authoritative;
  the standing-errata table lives in monitorr-docs-and-writing).

Local builds:

```bash
docker build -t monitorr .                                            # current arch
docker buildx build --platform linux/amd64,linux/arm64 -t monitorr .  # multi-arch
```

Multi-arch needs QEMU/binfmt set up (CI uses `docker/setup-qemu-action@v3`). All runtime deps
have prebuilt wheels, so the arm64 build compiles nothing. Local builds get
`VERSION=0.0.0+dev` and empty `VCS_REF`/`BUILD_DATE` — that is expected.

## 5. CI structure

`.github/workflows/ci.yml` — triggers: `push` to branches `dev` and `main`, plus **all** pull
requests. Two independent jobs, both on `ubuntu-latest`:

| Job | Steps | Proves |
|---|---|---|
| `quality` | checkout → `setup-uv@v5` (uv `0.11.7`) → `uv sync --frozen` → the four gates of §2 in that order | lint, format, types, tests — and that `uv.lock` matches `pyproject.toml` |
| `image` | checkout → setup-qemu → setup-buildx → `build-push-action@v6` with `platforms: linux/amd64,linux/arm64`, `push: false` | the multi-arch Docker build compiles; nothing is published |

Publishing happens only in `.github/workflows/release.yml` on a `v*.*.*` tag: it adds a
tag-vs-pyproject version guard, Docker Hub + GHCR logins, `metadata-action` tags
(`:X.Y.Z`/`:X.Y`/`:X`/`:latest`), a pushed multi-arch build with
`provenance: true`/`sbom: true` and the §4 build-args, then a GitHub Release from the
matching `CHANGELOG.md` section. The release PROCESS (when/how to tag, version bumps,
changelog) is owned by monitorr-change-control — this skill only owns the build mechanics.

## 6. Gotchas checklist

- [ ] **`ruff format` is the formatting source of truth.** CI enforces
  `ruff format --check .`; no black/isort configs exist. Never hand-align code — run
  `uv run ruff format .` and accept its output.
- [ ] **mypy strict covers `tests/` too** (`files = ["src", "tests"]`), and the suite has three
  deliberate targeted `# noqa` suppressions — test-writing conventions and the noqa inventory
  live in `monitorr-validation-and-qa` §6; do not "fix" those spots or add blanket ignores.
- [ ] **Packaging is hatchling with src-layout**: `[tool.hatch.build.targets.wheel]
  packages = ["src/monitorr"]`, console script `monitorr = "monitorr.main:run"`. New
  top-level packages must live under `src/monitorr/` or they won't ship in the wheel/image.
- [ ] **Changed a dep in `pyproject.toml`?** Run `uv lock` and commit `uv.lock` in the same
  commit, or CI (`uv sync --frozen`) and Docker both fail.
- [ ] **Dev tools are a PEP 735 group** — `pip install .[dev]` yields nothing; always use uv.

## When NOT to use

| Task | Go to |
|---|---|
| Run/operate the app (local dev run, compose, PUID/PGID, first-run, logs) | `monitorr-run-and-operate` |
| Write or debug tests, respx patterns, coverage gaps | `monitorr-validation-and-qa` |
| Release a version, tag, merge to main, changelog discipline | `monitorr-change-control` |
| Env vars / Policy fields / settings semantics | `monitorr-config-and-flags` |
| Fix stale documentation / errata | `monitorr-docs-and-writing` |

## Provenance and maintenance

Verified against the repo at v1.6.1 on 2026-07-02. Re-verify any fact before relying on it:

| Fact | Re-verification command |
|---|---|
| CI uv pin + gate order | `grep -n -A2 'setup-uv' .github/workflows/ci.yml && grep -n 'uv run' .github/workflows/ci.yml` |
| Python pins | `cat .python-version && grep -n 'requires-python\|python_version\|target-version' pyproject.toml` |
| Ruff/mypy/pytest config | `sed -n '/\[tool.ruff\]/,$p' pyproject.toml` |
| Dev deps are PEP 735 | `grep -n -A7 'dependency-groups' pyproject.toml` (and confirm no `optional-dependencies`) |
| Frozen installs | `grep -rn 'uv sync --frozen' .github/workflows/ Dockerfile` |
| Docker stages, args, healthcheck | `grep -n 'FROM\|ARG\|HEALTHCHECK\|ENTRYPOINT\|CMD\|EXPOSE\|VOLUME' Dockerfile` |
| App version source | `cat src/monitorr/__init__.py` |
| Build-sha flow | `grep -n 'MONITORR_BUILD' Dockerfile && grep -n 'build_sha\|build_date' src/monitorr/config.py src/monitorr/main.py` |
| CI triggers/jobs | `sed -n '1,15p' .github/workflows/ci.yml` |
| noqa spots | `grep -rn 'noqa' tests/ src/` |
| Test count (108 as of v1.6.1) | `grep -rEc "^(async )?def test_" tests/*.py \| awk -F: '{s+=$2} END {print s}'` |
