# Integración Plex

Fuente de watch status elegida para v1. monitorr lee de Plex **solo vía API**, nunca del
disco. Cubre cuatro cosas: vinculación (login), descubrimiento del servidor, detección de
visionado y correlación con Sonarr.

> Restricción de diseño: Plex pone difícil obtener el `X-Plex-Token` a mano. Por eso la
> vinculación es **"Login with Plex"** (flujo PIN/OAuth), no un token pegado en config.

## Identidad del cliente

monitorr genera **una vez** un `X-Plex-Client-Identifier` (UUID estable) y lo **persiste**.
Debe ser el mismo en todas las llamadas a plex.tv y al PMS; si cambia, el login y los
recursos asociados dejan de reconocerse. Va siempre junto con `X-Plex-Product` (nombre de la
app) en las cabeceras.

## Vinculación: Login with Plex (flujo PIN/OAuth)

Evita el token manual. El usuario inicia sesión en plex.tv y monitorr recibe el token.

1. **Crear PIN**: `POST https://plex.tv/api/v2/pins?strong=true` con cabeceras
   `X-Plex-Product` y `X-Plex-Client-Identifier`. Respuesta: `{ id, code, ... }`.
   El PIN **caduca a los pocos minutos** → si expira, reiniciar el flujo.
2. **Mandar al usuario a autorizar**: abrir en navegador
   `https://app.plex.tv/auth#?clientID=<clientId>&code=<code>&context[device][product]=<product>&forwardUrl=<callback>`
   (parámetros URL-encoded). El usuario hace login y autoriza.
3. **Polling del PIN**: `GET https://plex.tv/api/v2/pins/{id}` con `code` y
   `X-Plex-Client-Identifier`. Mientras no esté reclamado, `authToken` es `null`; al
   autorizar llega el `authToken`. **Persistir** ese token (es la credencial de cuenta).

## Descubrimiento del servidor

Con el `authToken` de cuenta, listar servidores en vez de pedir IP/puerto a mano:

- `GET https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1` con `X-Plex-Token`
  (el authToken) y `X-Plex-Client-Identifier`.
- Filtrar los recursos cuyo `provides` contenga `server`.
- Cada servidor trae su propio `accessToken` y un array `Connection` con
  `protocol` / `address` / `port` / `uri` / `local` / `relay`.

**Elección de conexión**: preferir `local` (LAN), luego conexión directa remota, y `relay`
como último recurso (lento). Para hablar con ese PMS se usa el **`accessToken` del servidor**
como `X-Plex-Token`, no necesariamente el de cuenta.

## Detección de visionado (mecanismo principal: polling)

Polling de sesiones activas, porque funciona solo con el token del login: **sin Plex Pass y
sin configuración manual**.

- `GET {serverUri}/status/sessions` con `X-Plex-Token`.
- Por cada sesión de tipo episodio: serie en `grandparentTitle`, temporada en `parentIndex`,
  episodio en `index`, id interno en `ratingKey` / `grandparentRatingKey`, progreso en
  `viewOffset` / `duration`.
- **"Visto"** cuando `viewOffset / duration ≥ ~0.9`, o cuando la sesión que estaba casi
  completa desaparece entre dos ciclos de polling.
- **Debounce obligatorio**: guardar estado del último episodio disparado por sesión para no
  re-procesar el mismo episodio en cada ciclo. El intervalo de polling es configurable.

## Webhook `media.scrobble` (opcional, menor latencia)

Mejora opcional para usuarios con **Plex Pass**. No se automatiza con el login: el usuario
debe añadir la URL del webhook en Plex (Settings → Webhooks).

- Evento de "visto" = `media.scrobble` (dispara al completar, ~90%; umbral no configurable).
- Payload `multipart` con JSON; `Metadata` de un episodio incluye: `type:"episode"`,
  `grandparentTitle`, `parentIndex` (temporada), `index` (episodio), `ratingKey`,
  `grandparentRatingKey`, `guid` y un array `Guid` con IDs externos (`tvdb://`/`tmdb://`/`imdb://`).
- No usar a la vez que [Tautulli](tautulli.md) como fuente: causaría doble procesamiento.

## Correlación con Sonarr (a TVDB)

Sonarr indexa por `tvdbId`. El array `Guid` del webhook **no siempre** trae el TVDB de la
serie, así que el camino fiable es resolver el metadato de la serie:

- `GET {serverUri}/library/metadata/{grandparentRatingKey}?includeGuids=1` y extraer el
  `tvdb://<id>` del array `Guid`.
- Con ese `tvdbId` + `parentIndex` (temporada) + `index` (episodio) se localiza el episodio
  exacto en Sonarr (ver [`sonarr.md`](sonarr.md)).

## Pitfalls

- **El PIN caduca rápido**: no reutilizar un `id` viejo; regenerar si el polling no resuelve.
- **`viewOffset` durante reproducción vs persistente**: en `/status/sessions` se actualiza en
  vivo; en `/library/metadata/{id}` solo tras cerrar la sesión. Para detectar "visto" usar el
  de la sesión.
- **Relay es lento**: si solo hay conexión relay, el polling y los metadatos van con latencia.
- **IDs externos a nivel de episodio** no son fiables; basta TVDB de la serie + números de
  temporada/episodio para correlacionar.
