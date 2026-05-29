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

## TODO: Lenguaje y tipado

> Rellenar al fijar el stack. Debe cubrir: versión mínima del lenguaje y dónde se pinea,
> política de type hints / tipado estricto, linter + formatter elegidos y dónde está su
> config, librería de validación de I/O en fronteras.

## TODO: CI

> Rellenar al montar CI. Debe cubrir: los steps que corren (lint, format check, type check,
> tests, smoke end-to-end), el comando único para correrlos en local antes de pushear, y la
> gestión del lockfile de dependencias. Enlazar a `.github/workflows/ci.yml` cuando exista.
