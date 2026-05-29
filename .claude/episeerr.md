# Prior art: episeerr

monitorr nace como alternativa **enfocada** a [episeerr](https://github.com/Vansmak/episeerr).
Este doc registra **solo** la parte de episeerr que monitorr replica/mejora —la gestión
automática de episodios en Sonarr según visionado— e **ignora** el resto (gestión de
peticiones, integración Overseerr/Jellyseerr, su UI y su stack).

## Qué hace episeerr (lo relevante)

- **Detección de visionado** vía webhook de Plex (`media.scrobble`), Jellyfin, o Tautulli
  ("Watched" con % configurable); también polling como fallback. Avisa de **no** usar dos
  fuentes a la vez (doble procesamiento).
- **Reglas por serie** asignadas con **tags** `episeerr_<regla>` en Sonarr (detección de "tag
  drift"; el tag es temporal).
- Parámetros de regla:
  - `GET` — monitorizar/buscar **N por delante** (`"3 episodes"`, `"1 season"`, `"all"`).
  - `KEEP` — conservar **N por detrás**; el resto se borra.
  - `Always Have` — episodios que **nunca** se borran (`S01E01`, `S*E01`, `S*`…).
  - **Grace periods**: `watched` (borra vistos tras X días de inactividad), `unwatched`
    (borra no vistos tras X días), `dormant` (borra todo si la serie lleva X días inactiva).
  - **Storage gate**: la limpieza solo corre si el espacio libre baja de un umbral.
  - **Dry-run global**: simula borrados y los muestra como "pending deletions".
- **Borrado** vía API de Sonarr; espera que esté activo "Unmonitor deleted episodes".

## Qué conserva monitorr

`GET` / `KEEP` / `Always Have` / grace `watched`/`unwatched`/`dormant` / dry-run. Es el núcleo
funcional, documentado en [`behavior.md`](behavior.md).

## Qué cambia / mejora monitorr

- **Vinculación sin token manual**: "Login with Plex" (PIN/OAuth) + descubrimiento de
  servidor, en vez de pedir token/URL a mano (ver [`plex.md`](plex.md)).
- **Fuente única y simple en v1**: Plex directo, con **polling como mecanismo principal**
  (sin Plex Pass ni setup de webhook) y webhook `media.scrobble` solo como mejora opcional.
- **Configuración global + override por serie**, en lugar del modelo de tags por serie de
  episeerr. Más simple de arrancar; el override cubre los casos especiales.

## Qué se descarta (no objetivos)

Gestión de peticiones, integración con Overseerr/Jellyseerr, tags de Sonarr como mecanismo de
configuración, y soporte multi-backend (Jellyfin/Emby) en v1.
