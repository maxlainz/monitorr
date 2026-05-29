# Comportamiento: ventana de episodios

Lógica central de monitorr. Define **qué** hace cuando detecta que se ha visto un episodio.
El *cómo* de cada sistema externo vive en [`plex.md`](plex.md) (detección/correlación) y
[`sonarr.md`](sonarr.md) (monitorizar/buscar/borrar). Prior art en [`episeerr.md`](episeerr.md).

## Disparo

El motor se activa cuando una serie se considera **vista hasta el episodio E** (ancla):
- Por polling: `viewOffset/duration ≥ ~0.9` o la sesión casi completa desaparece.
- Por webhook opcional: evento `media.scrobble`.

Cada disparo recalcula la ventana de **esa serie** alrededor de E. El disparo es idempotente:
volver a recibir el mismo E no debe producir cambios (debounce en la fuente, ver
[`plex.md`](plex.md)).

## Ventana

Dos parámetros, configurables en **episodios o temporadas**:

- **GET (N por delante)**: monitorizar y buscar los N episodios siguientes a E en orden de
  emisión. Implica `episode/monitor` + `EpisodeSearch` en Sonarr.
- **KEEP (N por detrás)**: conservar en disco los N episodios anteriores a E (incluido E). El
  resto, más antiguo que la ventana KEEP, se **borra** (`episodefile` delete) y se
  **desmonitoriza**, salvo que esté protegido por *Always-Have*.

## Always-Have (protección)

Episodios que **nunca** se borran aunque caigan fuera de KEEP o de un grace period. Patrones:
`S01E01` (piloto), `S*E01` (primer episodio de cada temporada), `S*` (temporada completa). Es
la primera comprobación antes de cualquier borrado.

## Grace periods (borrado por inactividad)

Complementan a KEEP con criterio temporal (días sin actividad de la serie):

- **watched**: borra episodios ya vistos pasados X días.
- **unwatched**: borra episodios no vistos pasados X días.
- **dormant**: borra todo lo borrable de la serie si lleva X días sin visionado.

Requiere **persistir estado** por serie/episodio (último visto, primer no visto, última
actividad). Los grace periods también respetan Always-Have.

## Dry-run

Modo global de simulación: el motor calcula los borrados y los **registra sin ejecutarlos**.
Sirve para validar reglas antes de activar el borrado real. No afecta a la monitorización
(GET), solo a los borrados (KEEP/grace).

## Configuración

- **Global única**: una política (GET, KEEP, Always-Have, grace, dry-run) para todas las series.
- **Override por serie**: ajustes manuales que sustituyen la política global en series concretas.

## Unidad temporadas (semántica)

- **GET por temporadas (N)**: monitoriza los episodios tras E con `season ≤ E.season + N`
  (resto de la temporada actual + las N siguientes).
- **KEEP por temporadas (N)**: conserva los episodios con `season ≥ E.season − (N−1)`; borra
  los de temporadas más antiguas.

## Edge cases (resueltos en el MVP)

- **Cruce de temporada**: la ventana razona en **orden de emisión** `(season, episode)`, no por
  temporada aislada; el "siguiente" puede caer en la temporada siguiente.
- **Especiales (`S00`)**: **excluidos** de GET/KEEP/grace.
- **Episodio sin fichero** (`hasFile:false`): no hay nada que borrar; solo monitorización.
- **Orden aired vs absolute (anime)**: el MVP usa siempre orden aired `(season, episode)`.
  Limitación conocida: anime con numeración absoluta puede no ordenarse como se espera.
- **Multiusuario**: fuera de v1 (se asume un consumidor). Hay **filtro opcional de usuarios**
  en Ajustes para limitar qué reproducciones disparan acciones.
