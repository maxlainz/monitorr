# Reglas de código, git y estilo

Doc de entrada: leer **antes de cualquier edición**. Para mantener esta y el resto de la
documentación, ver [`documentation.md`](documentation.md).

## Git

- Rama de trabajo: `dev`. Nunca commitear directamente a `main`.
- Tras cada edición de código → commit en `dev` → `git push` inmediato.
- Merge a `main` solo si el usuario lo pide explícitamente. El mensaje debe resumir todo
  lo nuevo desde el anterior commit en `main`.
- `CLAUDE.md` y `.claude/` **sí** entran a `main` en este repo (no se excluyen en el merge).
- Commits en español, mensaje corto y descriptivo. Prefijos convencionales (`feat:`,
  `fix:`, `docs:`, `chore:`, `refactor:`) bienvenidos pero no obligatorios.

## Idioma

- Documentación, comentarios (cuando existan) y mensajes de commit: español.
- Código, identificadores, nombres de archivos, branches y variables de entorno: inglés.

## Estilo de código

- Módulos por dominio (no por capa horizontal).
- Funciones pequeñas; una función hace una cosa.
- Imports explícitos. No `import *`.
- Sin `print()`/`console.log` de depuración en código de producción; usar un logger.
- Configuración (puertos, paths, intervalos, credenciales) siempre vía env vars o config,
  nunca hardcoded.

## Comentarios

- Por defecto, sin comentarios.
- Solo añadir uno cuando el WHY no es obvio: restricción externa, workaround de un bug
  concreto, invariante sutil, rate limit no evidente.
- Nunca explicar el QUÉ — el nombre de la función ya lo dice.

## Abstracciones

- Sin abstracciones prematuras. Tres líneas similares son preferibles a un helper genérico
  si no hay reutilización real.
- No añadir manejo de errores para escenarios imposibles.
- Validación solo en fronteras del sistema (input de usuario, respuestas de red, payloads externos).
- Sin feature flags ni shims de retrocompatibilidad mientras no haya usuarios externos.
  Cambiar el código directamente.

## Lenguaje y tipado

- **Python 3.12**, pineado en [`pyproject.toml`](../pyproject.toml) (`requires-python`) y en
  [`.python-version`](../.python-version). Deps con `uv` y lockfile `uv.lock`.
- **Type hints obligatorios**; mypy en **modo estricto** (config en `pyproject.toml`). El código
  debe pasar `mypy .` sin errores.
- **Linter + formatter: Ruff** (config en `pyproject.toml`). Formato de Ruff es la fuente de
  verdad; no introducir otro formatter.
- **Validación de I/O en fronteras: Pydantic v2** (y `pydantic-settings` para env vars). Validar
  payloads externos (Plex, Sonarr, forms) al entrar, no por dentro.
- Stack completo y *porqué*: [`tech-stack.md`](tech-stack.md).

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) corre en push a `dev`/`main` y en PRs:

- **quality**: `ruff check`, `ruff format --check`, `mypy`, `pytest` (instala con `uv sync --frozen`).
- **image**: construye la imagen multi-arch (`linux/amd64,linux/arm64`) con buildx (sin push).

Comando único en local antes de pushear (ver [`workflows.md`](workflows.md)):
`uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`.
