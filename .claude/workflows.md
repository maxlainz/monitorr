# Workflows

> Esqueleto. Rellenar los comandos cuando exista stack. Mantener actualizado según
> [`documentation.md`](documentation.md): cualquier comando nuevo se documenta aquí.

## Ramas y trabajo diario

- **`dev`**: rama de trabajo. **Todo el desarrollo ocurre aquí.** Nunca se commitea
  directamente a `main`.
- **`main`**: rama estable/publicada. Solo recibe merges explícitos desde `dev`
  (ver [Merge a `main`](#merge-a-main)).

Ciclo de trabajo en `dev` (tras cada cambio):

```bash
git checkout dev            # asegurarse de estar en dev antes de editar
# … editar código y docs …
git add -A
git commit -m "mensaje en español"
git push                   # push inmediato tras cada commit
```

Antes de empezar a editar, comprobar siempre la rama actual con `git status`; si no
estás en `dev`, cambia con `git checkout dev`.

## Comandos de desarrollo

> **TODO**: cómo arrancar el proyecto en local (servidor de dev, watch, etc.).

## Build / test / lint

> **TODO**: comandos de build, tests, linter y formatter. Incluir el comando único a
> ejecutar antes de pushear cuando haya CI (ver [`rules.md`](rules.md) → CI).

## Deploy

> **TODO**: cómo y a dónde se despliega.

## Variables de entorno

> **TODO**: tabla de env vars (nombre, propósito, valor por defecto, obligatoria sí/no).

## Merge a `main`

`main` solo recibe merges explícitos. `CLAUDE.md` y `.claude/` **viajan a `main`** (no se
excluyen). El mensaje del merge resume todo lo nuevo desde el anterior commit en `main`.

```bash
git checkout main
git merge dev --no-ff
# editar el mensaje para resumir todo lo nuevo desde el último commit en main
git push
git checkout dev
```
