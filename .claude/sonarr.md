# Integración Sonarr

Control saliente de monitorr **solo vía API** (sin acceso al disco). Sonarr es quien
monitoriza, busca/descarga y borra ficheros; monitorr solo le da órdenes. La lógica de
cuándo dar esas órdenes vive en [`behavior.md`](behavior.md).

> Implementado en [`src/monitorr/sonarr/client.py`](../src/monitorr/sonarr/client.py).

## Conexión y auth

- Base: `http://<host>:8989/api/v3`. Auth por cabecera `X-Api-Key` (o `?apikey=`).
- **Versión**: Sonarr v3 está EOL; usar **v4**. La ruta `/api/v3` sigue siendo válida en v4.

## Mapear serie y episodios

1. Por TVDB (lo que da Plex, ver [`plex.md`](plex.md)): `GET /api/v3/series?tvdbId={id}` →
   objeto serie con `id` (interno), `monitored`, `seasons[].seasonNumber/monitored`.
2. Episodios de la serie: `GET /api/v3/episode?seriesId={id}`. Campos clave por episodio:
   `id`, `seasonNumber`, `episodeNumber`, `absoluteEpisodeNumber`, `monitored`, `hasFile`,
   `episodeFileId`, `airDateUtc`.

El episodio que viene de Plex se localiza filtrando por `seasonNumber` + `episodeNumber`.

## Monitorizar / desmonitorizar

- Episodios (lote): `PUT /api/v3/episode/monitor` con body
  `{ "episodeIds": [..], "monitored": true|false }`.
- Temporada completa: `PUT /api/v3/series/{id}` editando `seasons[].monitored`. Requiere
  enviar el **objeto serie completo** → hacer antes `GET /api/v3/series/{id}`, modificar y
  reenviar.

## Buscar / descargar

**Monitorizar NO descarga.** Tras monitorizar hay que lanzar la búsqueda:

- `POST /api/v3/command` con `{ "name": "EpisodeSearch", "episodeIds": [..] }`.
- Alternativas: `SeasonSearch` (`{name, seriesId, seasonNumber}`), `SeriesSearch`.

## Borrar del disco

- Listar ficheros: `GET /api/v3/episodefile?seriesId={id}` → `id`, `episodeFileId`, `path`,
  `size`, etc.
- Borrar fichero: `DELETE /api/v3/episodefile/{id}` → **borra el fichero físico del disco**,
  no solo el registro.
- **Evitar re-descarga tras borrar**: activar "Unmonitor deleted episodes" en Sonarr
  (Settings → Media Management) **o** desmonitorizar el episodio por API antes/después de
  borrar. Si el episodio queda monitorizado, Sonarr lo vuelve a buscar.

## Pitfalls

- Monitorizar y buscar son dos pasos: olvidar el `command` deja el episodio monitorizado pero
  sin descargar.
- `PUT /api/v3/series/{id}` con objeto parcial falla → siempre GET-modify-PUT.
- **Season packs**: Sonarr solo acepta el paquete de temporada si **todos** los episodios de
  esa temporada están monitorizados.
- No hay rate limit oficial documentado; aun así, agrupar en lotes (`episodeIds: [..]`) en vez
  de una llamada por episodio.
- `DELETE /api/v3/episode/{id}` no existe; para "soltar" un episodio sin borrar fichero se usa
  `episode/monitor` con `monitored:false`.
