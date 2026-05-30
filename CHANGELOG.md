# Changelog

Todos los cambios notables de monitorr. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el versionado es
[SemVer](https://semver.org/lang/es/).

## [1.0.0] - 2026-05-30

Primera versión pública. monitorr observa el visionado en Plex y gestiona los episodios en
Sonarr **solo vía API**, manteniendo una ventana deslizante alrededor de lo que ves.

### Añadido

- **Ventana de episodios** GET (por delante) / KEEP (por detrás) con unidad por episodio o
  temporada; opción de lanzar la búsqueda en Sonarr para los episodios por delante.
- **Always-Have**: protección de episodios clave por patrones (`S01E01`, `S*E01`, `S01`, `S*`).
- **Grace periods**: borrado diferido con barridos periódicos (watched / unwatched / dormant).
- **Dry-run** como interruptor maestro, activado por defecto.
- **Detección de visionado** por sondeo de sesiones de Plex y webhook opcional `media.scrobble`,
  con disparo al ~90% visto y debounce.
- **Sincronización/reconciliación** periódica del estado visto; auto-normalización a piloto de
  las series sin visionado.
- **Login with Plex** (PIN/OAuth), descubrimiento de servidor y correlación Plex↔Sonarr por TVDB.
- **Cliente de Sonarr** (v3/v4): monitorizar/desmonitorizar, buscar, borrar y gestión de cola.
- **Web UI** server-rendered (HTMX + Jinja2): estado, series, historial de borrados y ajustes,
  con config global y overrides por serie.
- **Persistencia** en SQLite (aiosqlite, WAL) con migraciones por versión de esquema.
- **Imagen Docker** única multi-arch (amd64/arm64), endpoints `/health` y `/version`, y
  publicación automatizada en Docker Hub y GHCR mediante tags `vX.Y.Z`.

[1.0.0]: https://github.com/maxlainz/monitorr/releases/tag/v1.0.0
