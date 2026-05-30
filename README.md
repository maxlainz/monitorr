# monitorr

[![Release](https://img.shields.io/github/v/release/maxlainz/monitorr?sort=semver)](https://github.com/maxlainz/monitorr/releases)
[![CI](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml/badge.svg)](https://github.com/maxlainz/monitorr/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

monitorr observa qué series estás viendo en **Plex** y, vía la API de **Sonarr**, mantiene
monitorizados/descargados *N* episodios **por delante** de tu punto de visionado y conserva solo
*N* **por detrás** (borrando el resto del disco a través de Sonarr), protegiendo episodios clave
como el piloto. Todo **solo vía API**: nunca toca el disco de media.

Es una alternativa enfocada a [episeerr](.claude/episeerr.md): en vez de gestionar episodios a
mano, monitorr aplica una **ventana deslizante** alrededor de lo que realmente estás viendo.

## Características

- **Ventana por delante (GET)** — mantiene monitorizados *N* episodios por delante del último
  visto; opcionalmente lanza la búsqueda en Sonarr para descargarlos.
- **Ventana por detrás (KEEP)** — conserva solo *N* episodios por detrás y **borra el resto vía
  Sonarr**, liberando disco automáticamente.
- **Always-Have** — protege episodios clave con patrones (`S01E01`, `S*E01`, `S01`, `S*`): nunca
  se borran aunque queden fuera de la ventana.
- **Grace periods** — el borrado es diferido, no inmediato tras ver un episodio; barridos
  periódicos aplican los borrados cuando vence el plazo.
- **Unidad configurable** — la ventana se mide por **episodios** o por **temporadas**.
- **Dry-run por defecto** — interruptor maestro **activado de fábrica**: monitorr no toca nada
  en Sonarr hasta que lo desactivas explícitamente. Pruébalo sin miedo.
- **Detección en vivo + reconciliación** — sondeo de sesiones de Plex (con webhook opcional
  `media.scrobble`) y una sincronización periódica que reconcilia el estado visto.
- **Login with Plex** — vinculación por PIN/OAuth y descubrimiento automático del servidor.
- **Config global + overrides por serie**, editable desde la Web UI.
- **Imagen única multi-arch** (amd64/arm64), SQLite, sin servicios externos.

## Cómo funciona

```
Plex (poll de sesiones / webhook)  ──►  correlación por TVDB  ──►  Sonarr (API)
   detecta visionado (~90% visto)        serie Plex ↔ Sonarr        monitor / búsqueda / borrado
```

Cuando terminas (o casi) un episodio, monitorr recalcula la ventana de esa serie: monitoriza y
busca lo que falta por delante, y marca para borrado lo que sobra por detrás (respetando
Always-Have y los grace periods). Una sincronización periódica reconcilia todo por si se perdió
algún evento.

> _(Captura de la Web UI pendiente.)_

## Quick start

### docker run

```bash
docker run -d \
  --name monitorr \
  -p 8080:8080 \
  -v "$(pwd)/config:/config" \
  -e TZ=Europe/Madrid \
  --restart unless-stopped \
  ghcr.io/maxlainz/monitorr:latest
```

### docker compose

Usa el [`docker-compose.yml`](docker-compose.yml) de ejemplo:

```bash
docker compose up -d
```

Imágenes disponibles en **GHCR** (`ghcr.io/maxlainz/monitorr`) y **Docker Hub**
(`maxlainz/monitorr`), con tags `:1`, `:1.0`, `:1.0.0` y `:latest` para amd64 y arm64.

### Primeros pasos

1. Abre la Web UI en `http://localhost:8080`.
2. **Vincula Plex** (Login with Plex) y elige tu servidor.
3. Configura **Sonarr**: URL y API key (botón de test incluido).
4. Ajusta la **política**: episodios por delante (GET), por detrás (KEEP), patrones Always-Have,
   grace periods y unidad (episodio/temporada).
5. Con todo a punto, **desactiva el dry-run** para que monitorr empiece a actuar.

## Configuración

La configuración de la app (Sonarr, parámetros de ventana, grace, dry-run, overrides) vive en
SQLite y se edita **desde la Web UI**. Las variables de entorno solo cubren infraestructura:

| Variable | Propósito | Default |
|---|---|---|
| `MONITORR_CONFIG_DIR` | Directorio de datos (SQLite, identidad de cliente Plex) | `/config` |
| `MONITORR_PORT` | Puerto de escucha | `8080` |
| `MONITORR_LOG_LEVEL` | Nivel de log (`DEBUG`/`INFO`/`WARNING`/`ERROR`) | `INFO` |
| `MONITORR_PLEX_POLL_INTERVAL` | Segundos entre sondeos de sesiones de Plex | `30` |
| `MONITORR_GRACE_SWEEP_INTERVAL` | Segundos entre barridos de grace periods | `3600` |
| `MONITORR_SYNC_INTERVAL` | Segundos entre sincronizaciones (`0` la desactiva) | `21600` |
| `MONITORR_SYNC_ON_STARTUP` | Sincronizar una vez al arrancar si nunca se hizo | `true` |
| `MONITORR_WEBHOOK_SECRET` | Token para proteger el webhook de Plex | (vacío) |
| `TZ` | Zona horaria (afecta a los grace periods) | `UTC` |

Plantilla en [`.env.example`](.env.example).

## ⚠️ Seguridad

monitorr **no incluye autenticación propia** en v1: asume que corre en una **LAN de confianza**.
**No lo expongas directamente a internet.** Si necesitas acceso remoto, ponlo detrás de un
**reverse proxy con autenticación** (Authelia, Authentik, basic-auth, etc.). El único endpoint
pensado para exponerse, el webhook opcional de Plex, se protege con `MONITORR_WEBHOOK_SECRET`.

## Desarrollo

```bash
uv sync
uv run monitorr            # Web UI en http://localhost:8080
```

Comandos de build/test/lint, despliegue y proceso de release:
[`.claude/workflows.md`](.claude/workflows.md). Arquitectura y decisiones técnicas en
[`.claude/`](.claude/).

## Licencia

[GPL-3.0-or-later](LICENSE).
