---
name: monitorr-plex-sonarr-reference
description: >-
  Every external API call monitorr makes: Plex endpoint table (PIN login, resources, sessions,
  allLeaves, history pagination, /accounts, webhooks), Sonarr endpoint table (series,
  episode/monitor, EpisodeSearch, episodefile, queue), auth/token model, TVDB/grandparentTitle
  correlation, identifier-stability (ratingKey/sessionKey/accountID), user filters, API quirks.
  Use when adding/changing/debugging a Plex or Sonarr call. NOT for window/grace math →
  monitorr-window-engine-reference; NOT for operating → monitorr-run-and-operate.
---

# Plex & Sonarr integration reference

Every external HTTP call monitorr makes, verified against the code at v1.6.1 (2026-07-02).
Ground truth is `src/monitorr/plex/{auth,client,poller,webhook}.py` and
`src/monitorr/sonarr/client.py`; the prose docs `.claude/plex.md` and `.claude/sonarr.md` give
context but the code wins where they disagree.

Terms used below, defined once:

- **PMS** = Plex Media Server (the user's server, reached at `{serverUri}`; distinct from the
  plex.tv cloud API).
- **ratingKey** = a PMS-local numeric ID for one metadata item (show/season/episode).
  `grandparentRatingKey` = the show's ratingKey as carried on an episode object.
- **allLeaves** = the PMS endpoint listing all episodes of a show *currently in the library*.
- **scrobble** = Plex's "playback passed ~90%, mark watched" event (`media.scrobble` webhook).
- **anchor / GET / KEEP** = the furthest-watched episode and the window of episodes kept
  ahead/behind it. Formulas live in `monitorr-window-engine-reference`; this skill only covers
  the API calls that feed and execute that window.
- **season pack** = one torrent containing a whole season; Sonarr tracks it as one download but
  shows one queue row per episode.

## 1. Auth and token model

Two distinct Plex tokens, both stored in the SQLite `setting` table (keys in
`src/monitorr/constants.py:10-17`):

| Credential | Setting key | Obtained | Used for |
|---|---|---|---|
| Account `authToken` | `plex_account_token` | PIN/OAuth flow (`auth.py`) | plex.tv calls only: `/api/v2/resources`, `/api/v2/user/webhooks` |
| Per-server `accessToken` | `plex_server_token` | field of the chosen resource in `/api/v2/resources` | ALL calls to the PMS (`/status/sessions`, `/library/...`, `/accounts`, history) |

Never send the account token to the PMS: the resource's own `accessToken` is what the server
recognizes (`.claude/plex.md` "Server discovery"; stored by `plex_choose_server` in
`web/routes.py`).

**`X-Plex-Client-Identifier` must be persistent.** It is a UUID generated ONCE
(`auth.generate_client_id`, `src/monitorr/plex/auth.py:15-16`), stored under `plex_client_id` on
the first link (`web/routes.py:318-321`) and sent on every plex.tv and PMS call. If it changes,
plex.tv stops recognizing the login and its associated resources — do not regenerate it.

**Header sets** (exact, from code):

- plex.tv PIN calls (`auth.py:19-24`): `X-Plex-Product: monitorr` (`PLEX_PRODUCT`,
  `constants.py:3`), `X-Plex-Client-Identifier`, `Accept: application/json`.
- PMS + resources calls (`client.py:94-99`): `X-Plex-Token`, `X-Plex-Client-Identifier`,
  `Accept: application/json`.
- Account webhook calls (`client.py:141-142`): the PMS set **plus** `X-Plex-Product`.
- Sonarr (`sonarr/client.py:63-68`): base URL `{sonarr_url}/api/v3`, header `X-Api-Key`.

All HTTP goes through httpx with a 30 s timeout (`_TIMEOUT` in `auth.py:12`, `client.py:23`,
`sonarr/client.py:13`). During a sync cycle a `pooled_session` ContextVar reuses one connection
per side (`plex/client.py:42-51`, `sonarr/client.py:84-93`); outside a cycle each call opens a
fresh client.

## 2. Plex endpoint table

`H` = header set from §1 (PMS set unless noted). All responses JSON (`Accept` header).

| # | Method + URL | Params / body | Function (file:line) | Returns / notes |
|---|---|---|---|---|
| P1 | `POST https://plex.tv/api/v2/pins` | `?strong=true`; H=PIN set (no token) | `create_pin` `plex/auth.py:27-35` | `(pin id, code)`. PIN expires in a few minutes — regenerate, never reuse an old id |
| P2 | (URL built, not called) `https://app.plex.tv/auth#?` | urlencoded `clientID`, `code`, `context[device][product]=monitorr`, `forwardUrl` | `build_auth_url` `plex/auth.py:38-48` | The browser URL the user authorizes at; `forwardUrl` returns them to monitorr |
| P3 | `GET https://plex.tv/api/v2/pins/{id}` | `?code=<code>`; H=PIN set | `poll_pin` `plex/auth.py:51-59` | `authToken` once the user authorizes, else `None` (poll until claimed or expired) |
| P4 | `GET https://plex.tv/api/v2/resources` | `?includeHttps=1&includeRelay=1`; H=PMS set with **account** token | `discover_servers` `plex/client.py:110-138` | Resources whose `provides` contains `server` → name, `clientIdentifier`, per-server `accessToken`, `connections[]` (`uri`/`local`/`relay`) |
| P5 | `GET https://plex.tv/api/v2/user/webhooks` | H=webhook set (**account** token + `X-Plex-Product`) | `list_account_webhooks` `plex/client.py:145-158` | The account's webhook URL list (account-wide, shared with other integrations) |
| P6 | `POST https://plex.tv/api/v2/user/webhooks` | form body `urls[]=<u>...`, or `urls=` (empty) to clear; H=webhook set | `set_account_webhooks` `plex/client.py:161-168` | **DESTRUCTIVE: POST replaces the WHOLE list.** Always GET → filter → append → POST the full set (see §3) |
| P7 | `GET {serverUri}/status/sessions` | — | `get_sessions` `plex/client.py:201-224` | Active sessions; keeps `type=="episode"`: `grandparentTitle`, `grandparentRatingKey`, `parentIndex` (season), `index` (episode), `viewOffset`, `duration`, `sessionKey`, `User.title` |
| P8 | `GET {serverUri}/library/metadata/{grandparentRatingKey}` | `?includeGuids=1` | `resolve_tvdb_id` `plex/client.py:227-240` | Show metadata; TVDB id extracted from the `Guid[]` array via regex `tvdb://(\d+)` (`client.py:24`) |
| P9 | `GET {serverUri}/library/sections` | — | `list_show_libraries` `plex/client.py:243-254` | Section keys where `type=="show"` |
| P10 | `GET {serverUri}/library/sections/{key}/all` | `?type=2&includeGuids=1` | `list_shows` `plex/client.py:257-274` | Shows of the section: `ratingKey`, `title`, tvdb id from `Guid[]` |
| P11 | `GET {serverUri}/library/metadata/{showRatingKey}/allLeaves` | — | `get_watched_episodes` `plex/client.py:277-310` | Episodes currently in the library; watched = `viewCount > 0`; `lastViewedAt` → `viewed_at` (falls back to now if absent). VOLATILE: reflects only files still present |
| P12 | `GET {serverUri}/status/sessions/history/all` | `?sort=viewedAt:desc` + pagination `X-Plex-Container-Start` / `X-Plex-Container-Size` **as query params**, page size 1000 (`client.py:380`) | `get_watch_history_by_show` `plex/client.py:350-437` | Global play history swept newest-first. With `since` (ISO watermark) it stops at the first row older than `since` — the incremental sweep. Groups by `normalize_title(grandparentTitle)`; keeps most recent `viewedAt` per (title, season, episode). **Never scope with `metadataItemID={ratingKey}`** — see §4, ratingKey row |
| P13 | `GET {serverUri}/accounts` | — | `get_accounts` `plex/client.py:324-337` | `MediaContainer.Account[]` → map of normalized account name → local `accountID`. Owner is `OWNER_ACCOUNT_ID = 1` (`client.py:321`) |

**Connection choice** (`choose_connection`, `plex/client.py:171-178`): prefer direct **local**
(`local` and not `relay`), then direct **remote** (not `relay`), then **relay** as last resort.
First candidate of the first non-empty tier wins.

## 3. Account webhook registration (the destructive POST, tamed)

The webhook list (P5/P6) is account-wide and shared with other integrations (Home Assistant,
Tautulli, ...). Because P6 replaces the whole list, every writer in
`src/monitorr/plex/webhook.py` follows GET → drop monitorr's own entries → append → POST:

- "Ours" = any URL whose path contains `/webhook/plex/` (`MONITORR_WEBHOOK_PATH`,
  `webhook.py:18`, `_is_monitorr` `webhook.py:77-78`).
- `register_webhook` (`webhook.py:81-92`): keep others, swap in the new monitorr URL.
- `unregister_webhooks` (`webhook.py:95-100`): keep only others; POST only if something changed.
- `resync_webhook` (`webhook.py:103-114`): replace monitorr's entry only if one already exists
  (secret rotation never silently opts a user in).

The inbound endpoint is `POST /webhook/plex/{secret}` (`web/routes.py:539-575`). It parses only
`media.scrobble` events of episodes from the multipart `payload` JSON field (`parse_scrobble`,
`webhook.py:28-43`) and reuses `process_watch` — the same path as the poller.

## 4. Identifier-stability table (read before trusting any ID)

| Identifier | Scope | Stability | monitorr uses it for | Trap |
|---|---|---|---|---|
| `tvdbId` | Global (TheTVDB) | Stable | THE cross-system join: Plex `Guid[]` → Sonarr `series?tvdbId` | Episode-level GUIDs are unreliable; only the SHOW's tvdb + (season, episode) numbers correlate (`.claude/plex.md` "Correlation") |
| `ratingKey` / `grandparentRatingKey` | One PMS, one metadata item | **Volatile**: remove + re-add the series → NEW ratingKey | Resolving tvdb (P8), fetching allLeaves (P11), per-poll dedup of unresolvable shows | Incident 5ce7fab (→ v1.4.2): a per-show history query `metadataItemID={ratingKey}` returned EMPTY after a re-add (history orphaned to the old key) → anchor fell back → re-downloads. Never persist a ratingKey as identity; never filter history by it |
| `grandparentTitle` | One PMS, display title | Stable across remove/re-add | History↔library join via `normalize_title` = `title.strip().casefold()` (`plex/client.py:313-317`) | Two shows whose titles casefold-collide merge their histories (e.g. year-suffixed remakes named identically); a title RENAME in Plex silently splits old history from the show |
| `sessionKey` | One playback session | **Reused across a binge**: Plex keeps the same sessionKey on auto-play of the next episode | Poller debounce | Debounce key MUST be `(session_key, season, episode)` (`plex/poller.py:21`), never sessionKey alone |
| `accountID` | **Server-local** numeric id | Stable per server; meaningless on another PMS | Sync-side user filter on history rows (P12/P13) | Owner is always `1` (`OWNER_ACCOUNT_ID`); ids do not survive relinking a different server |
| Plex display name (`User.title` / `Account.title`) | Plex account | User-editable | Poller/webhook user filter (exact string match) and the names stored in `plex_user_filter` | Renaming the Plex account silently breaks the filter; see §5 for the exact-vs-casefold divergence |
| Sonarr series `id` | One Sonarr DB | Volatile: remove + re-add in Sonarr → new id | All per-series Sonarr calls (S4–S6) | Always re-resolve from `tvdbId`; never persist |
| Sonarr episode `id` | One Sonarr DB | Volatile with the series | monitor/search batches | Same: derived fresh from `GET /episode?seriesId=` each cycle |
| `episodeFileId` | One Sonarr DB, one file | **Changes on every re-download/upgrade**; `0` = no file | `DELETE /episodefile/{id}` | A stale snapshot's fileId can point at nothing (or the wrong file after an upgrade); fetch episodes immediately before deleting |

Discipline consequence (owned by `monitorr-change-control`, restated in one line): destructive
decisions must be driven by the persisted watch store, never by volatile identifiers or
`allLeaves` alone.

## 5. Sonarr endpoint table

Base `{sonarr_url}/api/v3`, header `X-Api-Key` (`sonarr/client.py:63-68`). Sonarr v3 is EOL —
target v4, where the `/api/v3` route remains valid (`.claude/sonarr.md` "Connection and auth").

| # | Method + URL | Params / body | Function (file:line) | Returns / notes |
|---|---|---|---|---|
| S1 | `GET /system/status` | — | `system_status` `sonarr/client.py:96-105` | Version string, or `None` on any HTTP/parse error (the Settings "test" button) |
| S2 | `GET /series` | `?tvdbId={id}` | `find_series_by_tvdb` `sonarr/client.py:108-115` | First matching series (`id`, `title`, `tvdbId`, `seriesType`, `seasons[]`), or `None` |
| S3 | `GET /series` | — | `list_series` `sonarr/client.py:118-122` | All series (the sync's "managed" set) |
| S4 | `GET /episode` | `?seriesId={id}` | `get_episodes` `sonarr/client.py:125-129` | All episodes: `id`, `seasonNumber`, `episodeNumber`, `monitored`, `hasFile`, `episodeFileId`, `airDateUtc` |
| S5 | `PUT /episode/monitor` | body `{"episodeIds": [...], "monitored": bool}` | `set_monitored` `sonarr/client.py:132-142` | Batch monitor/unmonitor; no-op on empty list |
| S6 | `GET /series/{id}` then `PUT /series/{id}` | PUT body = the **complete** series object with `seasons[].monitored` edited | `set_seasons_monitored` `sonarr/client.py:145-169` | GET-modify-PUT (a partial PUT fails). Idempotent: PUT only if a flag changes; returns whether it wrote. **Sonarr may CASCADE a season flag to its episodes** — any episode snapshot taken before the PUT is stale; re-fetch (incident 31fa9d5 → v1.6.1) |
| S7 | `POST /command` | body `{"name": "EpisodeSearch", "episodeIds": [...]}` | `search_episodes` `sonarr/client.py:172-180` | Triggers the indexer search. **Monitoring alone downloads NOTHING** — this is the separate, mandatory second step |
| S8 | `DELETE /episodefile/{id}` | — | `delete_episode_file` `sonarr/client.py:183-186` | Deletes the PHYSICAL file from disk (through Sonarr), not just the record |
| S9 | `GET /queue` | `?pageSize=1000` | `get_queue` `sonarr/client.py:196-201` | `records[]` → `(queue id, episodeId)`. **NO pagination loop — verified latent limitation: a queue with >1000 rows is silently truncated**, so the season-pack guard and the "already downloading" re-search skip would miss rows past 1000 |
| S10 | `DELETE /queue/{id}` | `?removeFromClient=false&blocklist=false` (both default False in code) | `delete_queue_item` `sonarr/client.py:204-221` | Sonarr stops tracking and won't import; the torrent stays seeding in the client. **One queue row is per-episode but maps to ONE torrent: cancelling a surplus episode cancels the WHOLE pack**, in-window episodes included — the mechanism behind the grab/cancel oscillation (see `monitorr-season-pack-campaign`) |

## 6. The two user-filter mechanisms (and how they diverge)

The optional filter `plex_user_filter` (a JSON list of Plex display names,
`engine/policy.py:81-86`) is applied by TWO different mechanisms:

1. **Poller and webhook — by display name, exact match.** `session.user not in user_filter`
   (`plex/poller.py:69`) and `event.user not in user_filter` (`web/routes.py:557-559`, returns
   `{"status": "filtered"}`). Case- and whitespace-sensitive.
2. **Sync — by resolved accountID.** `_account_filter` (`src/monitorr/sync.py:147-167`) resolves
   the filter's names against `GET /accounts` (P13) using `normalize_title` (casefold + strip),
   then the history sweep drops rows whose `accountID` is not in the set — BEFORE the
   per-episode dedup, so a newer play by a filtered user can't shadow an allowed one. The
   watermark still advances past the newest row SCANNED, pre-filter (`plex/client.py:340-348`).
   **Owner rule:** `allLeaves` (P11) contributes only when `OWNER_ACCOUNT_ID = 1` is in the
   resolved set (`sync.py:104-105`), because `viewCount` reflects the server token's account —
   the owner.

Divergences to expect:

- A filter name with wrong case/whitespace fails the poller/webhook's exact match (that user's
  live plays are DROPPED) but still resolves in the sync (casefold) — so their plays appear only
  after the next sync sweep.
- If NO filter name resolves against `/accounts`, or `/accounts` itself fails, the sync degrades
  to **unfiltered** with a warning (`sync.py:156-166`) — while the poller/webhook keep filtering
  strictly. Sync can therefore record plays the live path rejects.
- Managed-user (Plex Home) display names in sessions vs account names in `/accounts` may differ;
  only names present in `/accounts` can gate the sync.

## 7. Quirks and pitfalls table

| Quirk | Detail | Where verified |
|---|---|---|
| PIN expires fast | A few minutes; if `poll_pin` never resolves, create a NEW pin — never reuse the old `id` | `.claude/plex.md` "Pitfalls"; flow in `web/routes.py:316-346` |
| `viewOffset` live vs persistent | Updates live only in `/status/sessions`; `/library/metadata/{id}` reflects it only after the session closes. Watched-detection must use the session's value | `.claude/plex.md` "Pitfalls" (doc claim; code uses the session value, `poller.py`) |
| Relay is slow | If only a relay connection exists, every PMS call runs with high latency; `choose_connection` uses it only as last resort | `plex/client.py:171-178` |
| Episode-level GUIDs unreliable | Do not correlate by an episode's own external IDs; use the show's tvdb + (season, episode) | `.claude/plex.md` "Correlation" |
| Webhooks fire only for Plex Pass | Registration succeeds for anyone; the PMS just never SENDS without Plex Pass. Polling stays the always-on primary | `.claude/plex.md` "Webhook" caveats |
| Webhook endpoint ALWAYS returns HTTP 200 | Bad secret → body `{"status": "forbidden"}` with status 200; likewise `filtered`/`ignored`/`unlinked`/`no-tvdb`/`error`. Deliberate: Plex must not retry. So curl exit codes prove nothing — read the JSON body | `web/routes.py:539-575` |
| History pagination is query-param based | `X-Plex-Container-Start`/`X-Plex-Container-Size` are sent as QUERY PARAMS (Plex accepts either form), page size 1000, `sort=viewedAt:desc`; incremental sweep stops at the first row older than `since` | `plex/client.py:384-424` |
| Account webhook POST is destructive | Replaces the whole account-wide list; always GET → filter → POST full set | `plex/client.py:161-168`, §3 |
| Sonarr v3 EOL | Target v4; `/api/v3` path remains valid there | `.claude/sonarr.md` |
| Monitoring ≠ downloading | `PUT /episode/monitor` marks; only `POST /command` `EpisodeSearch` (S7) searches. Forgetting it leaves episodes monitored-but-missing forever | `.claude/sonarr.md` "Search / download" |
| Season-pack acceptance needs all-monitored | Sonarr accepts a season pack only when EVERY episode of that season is monitored; monitorr deliberately keeps watched/kept episodes unmonitored so that state is never reached (Always-Have episodes are the accepted exception since v1.6.0) | `.claude/sonarr.md` "Pitfalls"; incident dd889ee → v1.2.0 (story in `monitorr-failure-archaeology`) |
| Queue row cancels the whole torrent | S10: one row per episode, one torrent per pack — cancelling one surplus row drops in-window episodes too | `sonarr/client.py:204-221` docstring, `.claude/sonarr.md` "Pitfalls" |
| Queue read caps at 1000 rows | S9 sends `pageSize=1000` with no pagination loop; larger queues silently truncate | `sonarr/client.py:196-201` (verified latent limitation) |
| `DELETE /episode/{id}` does not exist | To drop an episode without touching the file, use `episode/monitor` with `monitored: false` | `.claude/sonarr.md` "Pitfalls" |
| No documented Sonarr rate limit | Batch anyway (`episodeIds: [...]`), never one call per episode | `.claude/sonarr.md` "Pitfalls" |

## 8. Recommended Sonarr-side settings (operator guidance)

These are settings in SONARR's UI, recommended by `.claude/sonarr.md` ("Monitoring when adding
shows") — monitorr cannot enforce them; treat them as operator guidance:

- **Monitor: "Pilot"** (or "None") when adding a show Sonarr will share with monitorr — never
  "All", or Sonarr fights monitorr by downloading the whole show. With "Pilot", Sonarr grabs
  S01E01 (the default Always-Have) and monitorr takes over forward. No manual step is needed for
  already-added shows: monitorr's normalize-to-pilot runs AUTOMATICALLY each sync for managed
  shows with no recorded viewing. (`.claude/sonarr.md` used to mention a "Normalize to Pilot"
  *button* — removed from the code in commit 9468870; the doc was stale until 2026-07-02 and now
  says "automatic Normalize to Pilot"; retired erratum E3 in `monitorr-docs-and-writing`.)
- **"Unmonitor deleted episodes"** enabled (Sonarr Settings → Media Management) as a safety net
  against re-downloads after deletion; monitorr already unmonitors on delete via API.

## 9. When NOT to use this skill

- Window/GET/KEEP/grace/Always-Have math and the anchor formulas →
  `monitorr-window-engine-reference`.
- Running the app, first-run sequence, Docker, sync modes in operation →
  `monitorr-run-and-operate`.
- Full incident stories behind the traps cited here (5ce7fab, dd889ee, 31fa9d5) →
  `monitorr-failure-archaeology`.
- The grab/cancel oscillation campaign built on S9/S10 → `monitorr-season-pack-campaign`.
- Env vars / Policy fields / setting keys as a config surface → `monitorr-config-and-flags`.
- Changing any of this behavior → `monitorr-change-control` first (anchor/sync-adjacent code
  needs adversarial review plus a regression test).

## 10. Provenance and maintenance

Everything above was verified against v1.6.1 on 2026-07-02 by reading the code. Re-verify before
trusting a drift-prone fact:

| Fact | Re-verification command (from repo root) |
|---|---|
| Plex endpoint URLs/params | `grep -n 'client.get\|client.post\|_URL' src/monitorr/plex/auth.py src/monitorr/plex/client.py` |
| History page size / pagination | `grep -n 'page_size\|Container' src/monitorr/plex/client.py` |
| Connection preference order | `grep -n -A 8 'def choose_connection' src/monitorr/plex/client.py` |
| Webhook list-replacing POST | `grep -n -B 2 -A 8 'def set_account_webhooks' src/monitorr/plex/client.py` |
| Webhook always-200 statuses | `grep -n 'status' src/monitorr/web/routes.py \| grep -n 'forbidden\|filtered\|ignored'` |
| Poller debounce key | `grep -n 'WatchKey' src/monitorr/plex/poller.py` |
| Owner account id | `grep -n 'OWNER_ACCOUNT_ID' src/monitorr/plex/client.py src/monitorr/sync.py` |
| Sync user-filter degradation | `grep -n -A 20 'def _account_filter' src/monitorr/sync.py` |
| Sonarr endpoints & bodies | `grep -n 'client.get\|client.put\|client.post\|client.delete' src/monitorr/sonarr/client.py` |
| Queue pageSize cap (S9) | `grep -n -A 5 'def get_queue' src/monitorr/sonarr/client.py` |
| Queue delete defaults (S10) | `grep -n -A 8 'def delete_queue_item' src/monitorr/sonarr/client.py` |
| Season cascade caveat (S6) | `grep -n -B 2 -A 10 'def set_seasons_monitored' src/monitorr/sonarr/client.py` |
| Header sets / PLEX_PRODUCT | `grep -n '_headers\|PLEX_PRODUCT' src/monitorr/plex/auth.py src/monitorr/plex/client.py src/monitorr/constants.py` |
| Sonarr-side operator guidance | `grep -n -A 8 'Monitoring when adding shows' .claude/sonarr.md` |
| Normalize button removal | `git log --oneline -1 9468870` |
