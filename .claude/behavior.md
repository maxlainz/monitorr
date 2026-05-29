# Comportamiento: ventana de episodios

Lógica central de monitorr. Define **qué** hace cuando detecta que se ha visto un episodio.
El *cómo* de cada sistema externo vive en [`plex.md`](plex.md) (detección/correlación) y
[`sonarr.md`](sonarr.md) (monitorizar/buscar/borrar). Prior art en [`episeerr.md`](episeerr.md).

## Disparo

El motor se activa cuando una serie se considera **vista hasta el episodio E** (ancla):
- Por polling: `viewOffset/duration ≥ ~0.9`, o una sesión que estaba **casi completa**
  (`progress ≥ NEAR_COMPLETE_PROGRESS`, 0.85 en `constants.py`) **desaparece** entre sondeos
  (el usuario terminó y la sesión se cerró antes de cruzar el umbral).
- Por webhook opcional: evento `media.scrobble`.

Cada disparo recalcula la ventana de **esa serie** alrededor de E. El disparo es idempotente:
el debounce del poller usa la clave `(sessionKey, temporada, episodio)`, no solo `sessionKey`
(Plex puede reutilizar el `sessionKey` al auto-reproducir el siguiente episodio de un binge, así
que avanzar de episodio dispara, pero re-sondear el mismo no).

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

- **watched**: borra episodios ya vistos pasados X días. *Default: 7.*
- **unwatched**: borra episodios no vistos pasados X días. *Default: 365.*
- **dormant**: borra todo lo borrable de la serie si lleva X días sin visionado. *Default: sin
  asignar (`None`) → desactivado: una serie inactiva nunca se purga en bloque.*

Cada grace es independiente; dejar uno **sin asignar** lo desactiva. Requiere **persistir estado**
por serie/episodio (último visto, primer no visto, última actividad). Respetan Always-Have.

## Dry-run (interruptor maestro)

`dry_run` es el **interruptor maestro de seguridad**, **ON por defecto**. Con dry-run ON,
monitorr **no realiza ninguna escritura en Sonarr**: ni monitorizar, ni buscar, ni
[forzar a Pilot](#forzar-a-pilot-opt-in), ni borrar. Solo registra/loguea lo que haría; los
borrados quedan como **pendientes** para revisar. Aplica a todo (lógica en vivo y
[sincronización](#sincronización--reconciliación)). Centralizado en `engine/actions.py`. Cuando
confías en el comportamiento, lo desactivas en Ajustes y todo pasa a ejecutarse de verdad.

Los **previews** pendientes se **deduplican** (una fila por episodio: el barrido de grace y la
sync re-previsualizan en cada ciclo sin acumular duplicados) y se **auto-limpian**: al borrar el
episodio de verdad se retira su preview, y al desactivar dry-run se vacían todos. El historial de
borrados reales se conserva siempre.

## Forzar a Pilot (opt-in)

Acción **manual** por serie ("Normalizar a Pilot"): deja monitorizado solo el piloto (`S01E01`),
buscándolo si le falta fichero. Los episodios **ya descargados** que quedan desmonitorizados se
**borran** (salvo Always-Have) — desmonitorizar un episodio en disco implica borrarlo. A partir de
ahí la ventana (GET) monitoriza hacia delante episodio a episodio. Quita la necesidad de configurar
"Monitor: Pilot" a mano en Sonarr. Nunca es automática; respeta dry-run.

**Episodios desmonitorizados que aún se están descargando** (no importados): se sacan de la cola de
Sonarr (`DELETE /queue/{id}` con `removeFromClient=false`) para que **no se importen**; el torrent
se queda en el cliente sembrando hasta su ratio, que lo elimina el propio cliente de descargas (no
monitorr). Sin desmonitorizar antes, Sonarr importaría igualmente la descarga ya iniciada.

## Sincronización / reconciliación

La detección en vivo (poller + webhook) solo dispara al ver un episodio. La **sync** reconcilia el
estado visto leyendo la biblioteca de Plex (ver [`plex.md`](plex.md)): por cada serie gestionada por
Sonarr, registra los episodios vistos con su fecha real (alimenta los grace periods) y aplica la
ventana para el **último visto**. Cubre tres casos que el modo en vivo no ve: series ya empezadas al
instalar, episodios marcados a mano en Plex, y visionados con monitorr apagado. Se dispara con el
botón "Sincronizar ahora", al arrancar (una vez) y periódicamente. Hereda el dry-run del motor.

## Configuración

- **Global única**: una política (GET, KEEP, Always-Have, grace) para todas las series + el
  interruptor dry-run.
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
- **Sync resiliente**: una serie con error (404, timeout, episodio inexistente) se loguea y se
  salta; no aborta el resto de la sincronización ni deja `last_sync` sin actualizar.
- **Sesión sin TVDB**: se avisa una vez y se cachea para no re-resolver (ni re-avisar) en cada
  sondeo; se reintenta cuando esa serie vuelve a reproducirse.
