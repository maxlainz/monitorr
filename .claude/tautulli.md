# Integración Tautulli (alternativa / futura)

**No se implementa en v1.** La fuente de watch status es [Plex directo](plex.md). Tautulli se
documenta como fuente alternativa porque encaja en la misma abstracción de "fuente de
visionado" y es la opción típica para quien usa Plex **sin Plex Pass** y prefiere no depender
del polling propio de monitorr.

## Por qué podría interesar

- Tautulli ya hace polling de Plex y expone eventos "Watched" con umbral de % configurable,
  con lo que monitorr no tendría que hacer su propio polling ni debounce.
- Funciona sin Plex Pass.

## Acceso

- Base: `http://<host>:8181/api/v2?cmd=<comando>`. Auth por `?apikey=`.
- Sesiones en curso: `cmd=get_activity`.
- Historial: `cmd=get_history` (filtros por `user_id`, `rating_key`, `media_type`, fechas…).
- Eventos salientes: **Notification Agents** con trigger "Watched" (% configurable). El
  payload admite parámetros como `{season_num}`, `{episode_num}`, `{grandparent_title}` y, en
  versiones recientes, `{thetvdb_id}` a nivel de serie.

## Pitfalls

- **Nunca combinar webhook de Plex + Tautulli** como fuentes a la vez: ambos disparan el mismo
  procesamiento y causan doble descarga (lo advierte [episeerr](episeerr.md)).
- Los IDs externos a nivel de **episodio** no están garantizados en el payload; al igual que
  con Plex, basta TVDB de la serie + temporada/episodio para correlacionar con
  [Sonarr](sonarr.md).
