# Plex integration

The watch-status source chosen for v1. monitorr reads from Plex **API-only**, never from
disk. It covers four things: linking (login), server discovery, viewing detection
and correlation with Sonarr.

> Design constraint: Plex makes it hard to get the `X-Plex-Token` by hand. That's why
> linking is **"Login with Plex"** (PIN/OAuth flow), not a token pasted in config.

> Implemented in [`src/monitorr/plex/`](../src/monitorr/plex) (`auth.py`, `client.py`,
> `poller.py`, `webhook.py`).

## Client identity

monitorr generates **once** an `X-Plex-Client-Identifier` (stable UUID) and **persists** it.
It must be the same in all calls to plex.tv and to the PMS; if it changes, the login and
associated resources stop being recognized. It always goes together with `X-Plex-Product` (the
app name) in the headers.

## Linking: Login with Plex (PIN/OAuth flow)

Avoids the manual token. The user signs in on plex.tv and monitorr receives the token.

1. **Create PIN**: `POST https://plex.tv/api/v2/pins?strong=true` with headers
   `X-Plex-Product` and `X-Plex-Client-Identifier`. Response: `{ id, code, ... }`.
   The PIN **expires within a few minutes** → if it expires, restart the flow.
2. **Send the user to authorize**: open in the browser
   `https://app.plex.tv/auth#?clientID=<clientId>&code=<code>&context[device][product]=<product>&forwardUrl=<callback>`
   (URL-encoded parameters). The user logs in and authorizes.
3. **Poll the PIN**: `GET https://plex.tv/api/v2/pins/{id}` with `code` and
   `X-Plex-Client-Identifier`. While it's not claimed, `authToken` is `null`; on
   authorizing the `authToken` arrives. **Persist** that token (it's the account credential).

## Server discovery

With the account `authToken`, list servers instead of asking for IP/port by hand:

- `GET https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1` with `X-Plex-Token`
  (the authToken) and `X-Plex-Client-Identifier`.
- Filter the resources whose `provides` contains `server`.
- Each server brings its own `accessToken` and a `Connection` array with
  `protocol` / `address` / `port` / `uri` / `local` / `relay`.

**Connection choice**: prefer `local` (LAN), then direct remote connection, and `relay`
as a last resort (slow). To talk to that PMS, use the **server's `accessToken`**
as `X-Plex-Token`, not necessarily the account one.

## Viewing detection (primary mechanism: polling)

Polling of active sessions, because it works with only the login token: **no Plex Pass and
no manual configuration**.

- `GET {serverUri}/status/sessions` with `X-Plex-Token`.
- For each episode-type session: show in `grandparentTitle`, season in `parentIndex`,
  episode in `index`, internal id in `ratingKey` / `grandparentRatingKey`, progress in
  `viewOffset` / `duration`.
- **"Watched"** when `viewOffset / duration ≥ ~0.9`, or when the session that was almost
  complete disappears between two polling cycles.
- **Mandatory debounce**: save the state of the last triggered episode per session so as not to
  re-process the same episode each cycle. The polling interval is configurable.

## `media.scrobble` webhook (optional, lower latency)

Optional improvement for users with **Plex Pass**. The inbound endpoint is `POST
/webhook/plex/{secret}` ([`web/routes.py`](../src/monitorr/web/routes.py) `plex_webhook`).

**Auto-configuration in Plex (on link).** monitorr registers this URL in Plex for the user via
the **account-level webhooks API** — no manual paste needed:

- `GET https://plex.tv/api/v2/user/webhooks` lists the URLs; `POST` to the same endpoint with form
  body `urls[]=<u>` **replaces the whole list** (`urls=` clears it). Auth: the **account** token
  (`X-Plex-Token`) + `X-Plex-Client-Identifier` + `X-Plex-Product`. See
  [`client.py`](../src/monitorr/plex/client.py) (`list_account_webhooks`, `set_account_webhooks`).
- The list is **account-wide and shared** with other integrations (Home Assistant, etc.), so
  monitorr never clobbers it: **GET → drop monitorr's own entries → append → POST** the full set.
  "Ours" = any URL whose path contains `/webhook/plex/`. See
  [`webhook.py`](../src/monitorr/plex/webhook.py) (`register_webhook`, `unregister_webhooks`,
  `resync_webhook`, `is_webhook_registered`).
- **When**: best-effort right after a server is stored on link (`_try_register_webhook` in
  [`web/routes.py`](../src/monitorr/web/routes.py)); a Settings button re-registers/removes it;
  Regenerate re-syncs only if already registered; Unlink removes monitorr's entry.
- **Caveats**: webhooks only **fire** for Plex Pass accounts (polling stays the always-on primary,
  so registration never blocks login). The webhook is sent by the **PMS** to the URL, so it must be
  reachable from the server — monitorr defaults to `request.base_url` (how the browser reached it)
  and the Settings field is **editable** to fix it (e.g. localhost / reverse proxy).

- **Secret**: auto-generated (`secrets.token_urlsafe`) and stored in SQLite
  (`constants.WEBHOOK_SECRET`); `get_or_create_webhook_secret()` in
  [`webhook.py`](../src/monitorr/plex/webhook.py) creates it on first access. The Settings UI
  shows the full ready-to-copy URL (with a Regenerate button). `MONITORR_WEBHOOK_SECRET` is an
  optional override to pin the same secret across instances; while set, Regenerate is disabled.
  The endpoint compares with `secrets.compare_digest`.
- "Watched" event = `media.scrobble` (triggers on completion, ~90%; threshold not configurable).
- `multipart` payload with JSON; an episode's `Metadata` includes: `type:"episode"`,
  `grandparentTitle`, `parentIndex` (season), `index` (episode), `ratingKey`,
  `grandparentRatingKey`, `guid` and a `Guid` array with external IDs (`tvdb://`/`tmdb://`/`imdb://`).
  The top-level `Account.title` is the Plex user, used to apply the same `user_filter` as the
  poller (a scrobble from a filtered-out user returns `{"status": "filtered"}`).
- Reuses `process_watch()` (record + apply window), the same path as the poller.
- Don't use it together with [Tautulli](tautulli.md) as a source: it would cause double processing.

## Correlation with Sonarr (to TVDB)

Sonarr indexes by `tvdbId`. The webhook's `Guid` array **doesn't always** bring the show's
TVDB, so the reliable path is to resolve the show's metadata:

- `GET {serverUri}/library/metadata/{grandparentRatingKey}?includeGuids=1` and extract the
  `tvdb://<id>` from the `Guid` array.
- With that `tvdbId` + `parentIndex` (season) + `index` (episode) the exact episode
  is located in Sonarr (see [`sonarr.md`](sonarr.md)).

## Library scan (sync)

The [sync](behavior.md) reconstructs "up to which episode each show has been watched" without
waiting for a playback, by traversing the library:

- `GET {serverUri}/library/sections` → sections; keep `type=="show"` (TV).
- `GET {serverUri}/library/sections/{key}/all?type=2&includeGuids=1` → shows with `ratingKey`,
  `title` and `Guid[]` (→ tvdb).
- `GET {serverUri}/library/metadata/{showRatingKey}/allLeaves` → episodes currently **in the
  library** with `viewCount`, `parentIndex`, `index`, `lastViewedAt`. Watched = `viewCount>0`.
- `GET {serverUri}/library/metadata/{showRatingKey}/allLeaves` is fetched **only for the shows with
  new plays** on an incremental sync (and for every managed show on a full one) — see
  [Incremental vs full](behavior.md#incremental-vs-full-avoid-re-scraping-plex).
- `GET {serverUri}/status/sessions/history/all` (sorted `viewedAt:desc`, paginated, **no**
  `metadataItemID`) → the **global play history** (`grandparentTitle`, `parentIndex`, `index`,
  `viewedAt`), swept **once per sync** and grouped by **normalized `grandparentTitle`**. Each show
  looks up its plays by title.
  - **Incremental sweep**: `get_watch_history_by_show(..., since=<watermark>)` stops paginating at
    the first row older than the stored `history_watermark` (rows are newest-first), so only the new
    tail is fetched instead of the entire history. The watermark advances to the newest `viewedAt`
    seen across the whole sweep (even unmanaged shows) so they're never re-fetched.
  - **Do not** scope it with `metadataItemID={showRatingKey}`: that server-side filter only matches
    history whose metadata items still resolve under the show's **current** ratingKey. A series
    **removed and re-added** in Plex gets a **new ratingKey**, so all its prior history (bound to the
    old, now-orphaned ids) returns **empty** — the exact re-add case where the anchor then falls back
    to disk and re-downloads old episodes. `grandparentTitle` is **stable across re-add**, so
    correlating by it recovers the history regardless of ratingKey changes (and it's one call instead
    of one per show). The bulk history rows often carry **no** `grandparentRatingKey`, which is why a
    ratingKey-based filter can't be trusted here.

monitorr **unions both** sources: the anchor is the maximum `(season, episode)` watched across
them, and the most recent date seeds the real date for the grace periods.

This is the **retroactive** path: monitorr trusts Plex's reported watch state, not disk/Sonarr.
`allLeaves` reflects the **current library**, so episodes whose files were deleted may **disappear
from it** (depends on the Plex library settings) — their viewing would be invisible and the anchor
would fall back to an earlier episode still present (which then triggers a re-download, e.g. a
season-pack grab). The **play history** does not depend on the files, so unioning it recovers the
true last-watched and lets the sync jump the window to it for a show **added (or re-added) after**
you'd already watched it, without re-downloading from the pilot (see [`behavior.md`](behavior.md)).

## Pitfalls

- **The PIN expires fast**: don't reuse an old `id`; regenerate if the polling doesn't resolve.
- **`viewOffset` during playback vs persistent**: in `/status/sessions` it updates
  live; in `/library/metadata/{id}` only after closing the session. To detect "watched" use the
  session's one.
- **Relay is slow**: if there's only a relay connection, polling and metadata run with latency.
- **External IDs at the episode level** are not reliable; the show's TVDB + season/episode
  numbers is enough to correlate.
- **Deleting files can hide episodes from `allLeaves`**: if the Plex library drops episodes that
  no longer have a file, their `viewCount` disappears from `allLeaves` and the anchor would fall
  back to an earlier, still-present episode. monitorr mitigates this by **also** reading the **play
  history** (`/status/sessions/history/all`), which survives file deletion. Removing the **whole
  series** from Plex (history included) still leaves no viewing → the next [sync](behavior.md)
  falls back to **normalize-to-pilot**.
- **Re-adding a series changes its ratingKey**: a removed+re-added show gets a **new ratingKey**, so
  a per-show history query (`metadataItemID={newRatingKey}`) returns **nothing** — the old history is
  orphaned to the previous ids. This re-added a watched series anchored on the furthest **still-on-disk**
  episode and re-downloaded old ones. Fixed by sweeping the **global** history once and correlating by
  **`grandparentTitle`** (stable across re-add), never by ratingKey (see [Library scan](#library-scan-sync)).
