# Tautulli integration (alternative / future)

**Not implemented in v1.** The watch-status source is [Plex directly](plex.md). Tautulli is
documented as an alternative source because it fits the same "viewing source" abstraction
and is the typical option for someone using Plex **without Plex Pass** who prefers not to depend
on monitorr's own polling.

## Why it might be of interest

- Tautulli already polls Plex and exposes "Watched" events with a configurable % threshold,
  so monitorr wouldn't have to do its own polling or debounce.
- It works without Plex Pass.

## Access

- Base: `http://<host>:8181/api/v2?cmd=<command>`. Auth via `?apikey=`.
- Ongoing sessions: `cmd=get_activity`.
- History: `cmd=get_history` (filters by `user_id`, `rating_key`, `media_type`, dates…).
- Outbound events: **Notification Agents** with the "Watched" trigger (configurable %). The
  payload accepts parameters such as `{season_num}`, `{episode_num}`, `{grandparent_title}` and, in
  recent versions, `{thetvdb_id}` at the show level.

## Pitfalls

- **Never combine the Plex webhook + Tautulli** as sources at the same time: both trigger the same
  processing and cause double downloading.
- External IDs at the **episode** level are not guaranteed in the payload; just like
  with Plex, the show's TVDB + season/episode is enough to correlate with
  [Sonarr](sonarr.md).
