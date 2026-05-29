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

```bash
uv sync                    # crea/actualiza .venv desde uv.lock
uv run monitorr            # arranca la app (Web UI en http://localhost:8080)
```

Por defecto la BD se crea en `/config`; en local exporta `MONITORR_CONFIG_DIR=./config` para
no necesitar permisos en `/config`.

## Build / test / lint

```bash
uv run ruff check .            # lint
uv run ruff format .           # formatear (o --check para validar sin tocar)
uv run mypy .                  # type checking estricto
uv run pytest                  # tests
```

Comando único pre-push (lo mismo que corre CI):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Imagen Docker:

```bash
docker build -t monitorr .                                          # build local (arch actual)
docker buildx build --platform linux/amd64,linux/arm64 -t monitorr . # multi-arch
```

## Deploy

Imagen única en Docker. Ejemplo en [`docker-compose.yml`](../docker-compose.yml): mapea
`8080:8080` y monta un volumen en `/config` (SQLite + identidad de cliente Plex).

```bash
docker compose up -d
```

## Variables de entorno

Solo infraestructura; la config de la app (Sonarr, ventana, grace, overrides) vive en SQLite y
se edita por la Web UI. Definidas en [`config.py`](../src/monitorr/config.py).

| Var | Propósito | Default | Obligatoria |
|---|---|---|---|
| `MONITORR_CONFIG_DIR` | Dir de datos (SQLite, identidad cliente) | `/config` | no |
| `MONITORR_PORT` | Puerto de escucha | `8080` | no |
| `MONITORR_LOG_LEVEL` | Nivel de log | `INFO` | no |
| `MONITORR_PLEX_POLL_INTERVAL` | Segundos entre polls de sesiones | `30` | no |
| `MONITORR_WEBHOOK_SECRET` | Token del endpoint webhook opcional | (vacío) | no |
| `TZ` | Zona horaria (grace periods) | `UTC` | no |

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
