# Sonarr integration

monitorr's outbound control **API-only** (no disk access). Sonarr is the one that
monitors, searches/downloads and deletes files; monitorr only gives it orders. The logic of
when to give those orders lives in [`behavior.md`](behavior.md).

> Implemented in [`src/monitorr/sonarr/client.py`](../src/monitorr/sonarr/client.py).

## Monitoring when adding shows (recommended)

Shows managed by monitorr should be added in Sonarr with **Monitor: "Pilot"** (or "None"),
**never "All"**: monitorr decides what gets monitored (the N ahead) and what gets deleted (the N
behind); if Sonarr monitors "All" it will try to download the whole show on its own and fight with
monitorr. With "Pilot", Sonarr grabs `S01E01` (which is also the default `Always-Have`) and monitorr
takes over forward. Alternative without touching Sonarr by hand: the **"Normalize to Pilot"** button
(see [`behavior.md`](behavior.md)) sets it that way via API. Also leave **"Unmonitor deleted episodes"**
enabled (Settings → Media Management) as a safety net; monitorr already unmonitors on delete.

## Connection and auth

- Base: `http://<host>:8989/api/v3`. Auth via `X-Api-Key` header (or `?apikey=`).
- **Version**: Sonarr v3 is EOL; use **v4**. The `/api/v3` route is still valid in v4.

## Map show and episodes

1. By TVDB (what Plex gives, see [`plex.md`](plex.md)): `GET /api/v3/series?tvdbId={id}` →
   show object with `id` (internal), `monitored`, `seasons[].seasonNumber/monitored`.
2. Episodes of the show: `GET /api/v3/episode?seriesId={id}`. Key fields per episode:
   `id`, `seasonNumber`, `episodeNumber`, `absoluteEpisodeNumber`, `monitored`, `hasFile`,
   `episodeFileId`, `airDateUtc`.

The episode coming from Plex is located by filtering on `seasonNumber` + `episodeNumber`.

## Monitor / unmonitor

- Episodes (batch): `PUT /api/v3/episode/monitor` with body
  `{ "episodeIds": [..], "monitored": true|false }`.
- Full season: `PUT /api/v3/series/{id}` editing `seasons[].monitored`. Requires
  sending the **complete show object** → first do `GET /api/v3/series/{id}`, modify and
  resend. monitorr **enforces** this flag on every window/normalize (see
  [`behavior.md`](behavior.md) "Season-level monitoring"): new episodes inherit
  their season's `monitored`, so leaving a season monitored would make Sonarr
  auto-download them. Implemented in `set_seasons_monitored` (idempotent, GET-modify-PUT).

## Search / download

**Monitoring does NOT download.** After monitoring, the search must be triggered:

- `POST /api/v3/command` with `{ "name": "EpisodeSearch", "episodeIds": [..] }`.
- Alternatives: `SeasonSearch` (`{name, seriesId, seasonNumber}`), `SeriesSearch`.

## Delete from disk

- List files: `GET /api/v3/episodefile?seriesId={id}` → `id`, `episodeFileId`, `path`,
  `size`, etc.
- Delete file: `DELETE /api/v3/episodefile/{id}` → **deletes the physical file from disk**,
  not just the record.
- **Avoid re-download after deleting**: enable "Unmonitor deleted episodes" in Sonarr
  (Settings → Media Management) **or** unmonitor the episode via API before/after
  deleting. If the episode stays monitored, Sonarr searches for it again.

## Pitfalls

- Monitoring and searching are two steps: forgetting the `command` leaves the episode monitored but
  not downloaded.
- `PUT /api/v3/series/{id}` with a partial object fails → always GET-modify-PUT.
- **Season packs**: Sonarr only accepts the season pack if **all** the episodes of
  that season are monitored.
- There is no officially documented rate limit; even so, batch them (`episodeIds: [..]`) instead
  of one call per episode.
- `DELETE /api/v3/episode/{id}` doesn't exist; to "drop" an episode without deleting the file use
  `episode/monitor` with `monitored:false`.
