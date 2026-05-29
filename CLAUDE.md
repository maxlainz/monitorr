# monitorr

> **TODO**: describir en 2-3 líneas qué es monitorr, qué problema resuelve y cómo
> se despliega. Rellenar cuando se defina el propósito y el stack.

Esqueleto de documentación recién sembrado: aún **no hay código ni stack elegido**.
Antes de empezar a desarrollar, lee [`.claude/documentation.md`](.claude/documentation.md)
para saber **cómo y cuándo** mantener esta documentación.

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
| [`.claude/workflows.md`](.claude/workflows.md) | Comandos de desarrollo, build/test/lint, deploy, env vars, merge a `main` |

> Este es un esqueleto inicial. A medida que el código crezca, **amplía** `.claude/`
> con docs por dominio (DB, API, UI, etc.) siguiendo las normas de
> [`.claude/documentation.md`](.claude/documentation.md) y añade su fila a esta tabla.
