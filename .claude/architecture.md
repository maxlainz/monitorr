# Arquitectura

> Fase de investigación: el **stack aún no está fijado**. Este doc describe propósito,
> componentes conceptuales, flujo y decisiones; los detalles de implementación se rellenan
> al elegir stack. Mantener actualizado según [`documentation.md`](documentation.md).

## Propósito

monitorr observa qué series se están viendo en Plex y, vía API de Sonarr, mantiene
monitorizados/descargados *N* episodios **por delante** del punto de visionado y conserva
solo *N* **por detrás** (borrando el resto del disco a través de Sonarr), protegiendo
episodios clave (p.ej. el piloto). Todo **solo vía API**, sin acceso al disco de media.

## Componentes

Organizados por dominio (no por capa):

- **Vinculación/auth (Plex)** — "Login with Plex" (flujo PIN/OAuth), descubrimiento del
  servidor y persistencia del token e identidad de cliente. Ver [`plex.md`](plex.md).
- **Detector de visionado** — polling de `/status/sessions` (principal) con debounce, y
  webhook `media.scrobble` (opcional). Emite el evento "serie vista hasta el episodio E".
- **Motor de ventana** — aplica GET / KEEP / Always-Have / grace / dry-run. Núcleo funcional,
  ver [`behavior.md`](behavior.md).
- **Cliente Sonarr** — monitorizar/desmonitorizar, lanzar búsquedas y borrar ficheros. Ver
  [`sonarr.md`](sonarr.md).
- **Estado/persistencia** — tokens (cuenta + servidor), identidad de cliente, y estado por
  serie/episodio para los grace periods y el debounce.
- **Configuración** — política global + overrides por serie.

## Flujo de datos

1. Login with Plex → token + servidor descubierto (una vez).
2. Polling de `/status/sessions` (o webhook `media.scrobble`) → "visto hasta E".
3. Correlación a TVDB vía `/library/metadata/{grandparentRatingKey}?includeGuids=1`.
4. Motor de ventana calcula GET (monitorizar+buscar adelante) y KEEP/grace (borrar atrás,
   salvo Always-Have); en dry-run solo registra.
5. Cliente Sonarr ejecuta: `episode/monitor` + `EpisodeSearch` y `episodefile` delete.

## Decisiones técnicas

1. **Fuente: Plex directo** (abstracción de fuente; Tautulli documentado como alternativa).
   *Por qué*: es el caso de uso del usuario y evita la dependencia de un segundo servicio.
2. **Vinculación por Login with Plex (PIN/OAuth)**. *Por qué*: Plex no expone el token
   fácilmente; el login evita pegarlo a mano y permite descubrir el servidor.
3. **Detección por polling primario + webhook opcional**. *Por qué*: el polling funciona solo
   con el token del login (sin Plex Pass ni setup manual); el webhook da menor latencia a
   quien tenga Plex Pass.
4. **Trigger "visto" a ~90%**. *Por qué*: coincide con el fin real del episodio (scrobble) y
   es replicable por polling con `viewOffset/duration`.
5. **Borrado combinado: conteo + grace + dry-run**. *Por qué*: el conteo es predecible, el
   grace cubre inactividad, y el dry-run permite validar antes de borrar de verdad.
6. **Config global + override por serie**. *Por qué*: arranque simple, con escape para casos
   especiales sin el modelo de tags por serie de episeerr.

## Decisiones pendientes

- Stack (lenguaje, framework, persistencia, despliegue) — `tech-stack.md` cuando se fije.
- Mecanismo de persistencia de tokens y estado de grace.
- Intervalo de polling y estrategia de debounce.
- Orden aired vs absolute (anime) y tratamiento de especiales `S00`.
- Soporte multiusuario (riesgo de borrar lo que otro no ha visto) — fuera de v1.
