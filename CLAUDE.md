# monitorr

monitorr is a service (designed for Docker) that watches which shows are being viewed in Plex
and, via the Sonarr API, keeps *N* episodes **ahead** of the viewing point
monitored/downloaded and keeps only *N* **behind** (deleting the rest from disk through
Sonarr), protecting key episodes such as the pilot. Everything **API-only**, with no access to
the media disk.

**Stack**: Python 3.12 · FastAPI · HTMX · SQLite · Docker single-image multi-arch (Web UI on
`:8080`). Details in [`.claude/tech-stack.md`](.claude/tech-stack.md). The Plex/Sonarr/window
logic is implemented and tested; v1.0.0 published on Docker Hub and GHCR.

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

> This is an initial skeleton. As the code grows, **expand** `.claude/`
> with per-domain docs (DB, API, UI, etc.) following the rules in
> [`.claude/documentation.md`](.claude/documentation.md) and add its row to this table.
