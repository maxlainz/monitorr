# monitorr

monitorr es un servicio (pensado para Docker) que observa qué series se están viendo en Plex
y, vía API de Sonarr, mantiene monitorizados/descargados *N* episodios **por delante** del
punto de visionado y conserva solo *N* **por detrás** (borrando el resto del disco a través de
Sonarr), protegiendo episodios clave como el piloto. Todo **solo vía API**, sin acceso al
disco de media. Es una alternativa enfocada a [episeerr](.claude/episeerr.md).

**Stack**: Python 3.12 · FastAPI · HTMX · SQLite · Docker single-image multi-arch (Web UI en
`:8080`). Detalle en [`.claude/tech-stack.md`](.claude/tech-stack.md). La lógica de
Plex/Sonarr/ventana está implementada y testeada; v1.0.0 publicada en Docker Hub y GHCR.

> Antes de desarrollar, lee [`.claude/documentation.md`](.claude/documentation.md) para saber
> **cómo y cuándo** mantener esta documentación.

## Reglas

- Desarrollo siempre en rama `dev`; merge a `main` solo cuando se pida explícitamente.
- Tras cada edición de código → commit en `dev` → `git push` inmediato.
- Al mergear a `main`, el mensaje debe resumir todo lo nuevo desde el último commit en `main`.
- `CLAUDE.md` y `.claude/` **sí** viajan a `main` (no excluir en el merge).
- Actualizar los docs de `.claude/` tras cualquier cambio mayor en arquitectura, comandos
  o reglas — **en el mismo commit** que el cambio. Cómo y cuándo: [`.claude/documentation.md`](.claude/documentation.md).
- Idioma de docs y commits: español. Identificadores, nombres de variables, ramas y código en inglés.

## Contexto (leer según tarea)

| Archivo | Cuándo leer |
|---|---|
| [`.claude/rules.md`](.claude/rules.md) | Antes de cualquier edición — git, idioma, estilo, comentarios, abstracciones |
| [`.claude/documentation.md`](.claude/documentation.md) | Antes de tocar la documentación — cómo y cuándo actualizarla, cuándo crear un doc nuevo |
| [`.claude/architecture.md`](.claude/architecture.md) | Stack, componentes, flujo de datos, decisiones técnicas y pendientes |
| [`.claude/tech-stack.md`](.claude/tech-stack.md) | Versiones, layout de directorios, dependencias y decisiones del stack |
| [`.claude/workflows.md`](.claude/workflows.md) | Comandos de desarrollo, build/test/lint, deploy, env vars, merge a `main` |
| [`.claude/behavior.md`](.claude/behavior.md) | Lógica central: ventana de episodios (GET/KEEP), Always-Have, grace periods, dry-run, override |
| [`.claude/plex.md`](.claude/plex.md) | Integración Plex: Login with Plex (PIN/OAuth), descubrimiento de servidor, polling/webhook, correlación TVDB |
| [`.claude/sonarr.md`](.claude/sonarr.md) | Integración Sonarr: monitorizar/buscar/borrar episodios vía API |
| [`.claude/tautulli.md`](.claude/tautulli.md) | Fuente alternativa/futura de watch status (no en v1) |
| [`.claude/episeerr.md`](.claude/episeerr.md) | Prior art: qué hace episeerr en lo relevante y qué conserva/mejora monitorr |

> Este es un esqueleto inicial. A medida que el código crezca, **amplía** `.claude/`
> con docs por dominio (DB, API, UI, etc.) siguiendo las normas de
> [`.claude/documentation.md`](.claude/documentation.md) y añade su fila a esta tabla.
