import json

import httpx
import respx

from monitorr import constants, store
from monitorr.engine.policy import Policy, set_dry_run, set_global_policy
from monitorr.engine.window import apply_window
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

SONARR = "http://sonarr:8989/api/v3"
TVDB = 999

EPISODES = [
    {
        "id": 101,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "Pilot",
        "hasFile": True,
        "episodeFileId": 201,
    },
    {
        "id": 102,
        "seasonNumber": 1,
        "episodeNumber": 2,
        "title": "E2",
        "hasFile": True,
        "episodeFileId": 202,
    },
    {
        "id": 103,
        "seasonNumber": 1,
        "episodeNumber": 3,
        "title": "E3",
        "hasFile": True,
        "episodeFileId": 203,
    },
    {
        "id": 104,
        "seasonNumber": 1,
        "episodeNumber": 4,
        "title": "E4",
        "hasFile": False,
        "episodeFileId": 0,
    },
    {
        "id": 105,
        "seasonNumber": 1,
        "episodeNumber": 5,
        "title": "E5",
        "hasFile": False,
        "episodeFileId": 0,
    },
]


async def _configure(always_have: list[str]) -> None:
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    await set_global_policy(
        Policy(get_count=1, keep_count=1, always_have=always_have, search_on_get=True)
    )


def _mock_sonarr(episodes: list[dict[str, object]] | None = None) -> dict[str, respx.Route]:
    return {
        "series": respx.get("http://sonarr:8989/api/v3/series").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
        ),
        "episodes": respx.get("http://sonarr:8989/api/v3/episode").mock(
            return_value=httpx.Response(200, json=EPISODES if episodes is None else episodes)
        ),
        "monitor": respx.put(f"{SONARR}/episode/monitor").mock(
            return_value=httpx.Response(200, json=[])
        ),
        "command": respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={})),
        "delete": respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
            return_value=httpx.Response(200)
        ),
        "queue": respx.get(f"{SONARR}/queue").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
    }


@respx.mock
async def test_dry_run_previews_without_touching_sonarr() -> None:
    await _configure(always_have=[])  # dry-run ON by default
    routes = _mock_sonarr()

    await apply_window(TVDB, season=1, episode=3)

    # Master switch: in dry-run nothing is written to Sonarr.
    assert not routes["monitor"].called
    assert not routes["command"].called
    assert not routes["delete"].called
    # But the deletion it would do is recorded as pending (KEEP=1: E1 and E2).
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 2)}


@respx.mock
async def test_always_have_protects_pilot_in_preview() -> None:
    await _configure(always_have=["S01E01"])
    _mock_sonarr()

    await apply_window(TVDB, season=1, episode=3)

    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2)}


@respx.mock
async def test_real_mode_monitors_searches_deletes() -> None:
    await _configure(always_have=[])
    await set_dry_run(False)
    routes = _mock_sonarr()

    await apply_window(TVDB, season=1, episode=3)

    assert routes["monitor"].called  # E4 monitored ahead
    assert routes["command"].called  # E4 searched (no file)
    assert routes["delete"].call_count == 2  # E1 and E2 deleted
    done = await store.list_deletions(dry_run=False)
    assert {(d.season, d.episode) for d in done} == {(1, 1), (1, 2)}


@respx.mock
async def test_passed_series_and_episodes_match_fetched_path() -> None:
    """The sync passes already-fetched series/episodes: the window must produce the same Sonarr
    writes as test_real_mode_monitors_searches_deletes (anchor S01E03 → monitor/search E4, delete
    E1+E2) while skipping the per-show /series?tvdbId lookup and the /episode fetch."""
    await _configure(always_have=[])
    await set_dry_run(False)
    routes = _mock_sonarr()

    series = SonarrSeries(id=1, title="X", tvdbId=TVDB)
    episodes = [SonarrEpisode.model_validate(e) for e in EPISODES]
    await apply_window(TVDB, season=1, episode=3, series=series, episodes=episodes)

    assert not routes["series"].called  # series provided → no find_series_by_tvdb
    assert not routes["episodes"].called  # episodes provided → no get_episodes
    assert routes["monitor"].called and routes["command"].called
    assert routes["delete"].call_count == 2
    assert {(d.season, d.episode) for d in await store.list_deletions(dry_run=False)} == {
        (1, 1),
        (1, 2),
    }


def _ep(num: int, *, has_file: bool, monitored: bool = True) -> dict[str, object]:
    return {
        "id": 100 + num,
        "seasonNumber": 1,
        "episodeNumber": num,
        "title": f"E{num}",
        "hasFile": has_file,
        "episodeFileId": (200 + num) if has_file else 0,
        "monitored": monitored,
    }


# E1-E5 on disk, E6 monitored without file, E7 neither monitored nor on disk.
AHEAD_EPISODES = [
    _ep(1, has_file=True),
    _ep(2, has_file=True),
    _ep(3, has_file=True),
    _ep(4, has_file=True),
    _ep(5, has_file=True),
    _ep(6, has_file=False, monitored=True),
    _ep(7, has_file=False, monitored=False),
]


@respx.mock
async def test_forward_trim_previews_ahead_deletions() -> None:
    await _configure(always_have=[])  # GET=1, KEEP=1, dry-run ON
    _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=2)  # anchor E2, GET=1 → keeps E3

    # Ahead of the GET window and on disk: E4, E5. Behind (KEEP=1): E1.
    # E6 (no file) is unmonitored, not deleted; E7 does nothing.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 4), (1, 5)}


@respx.mock
async def test_forward_trim_deletes_files_and_unmonitors_in_real_mode() -> None:
    await _configure(always_have=[])
    await set_dry_run(False)
    routes = _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=2)

    # Deletions: E1 (behind, keep) + E4, E5 (ahead).
    assert routes["delete"].call_count == 3
    done = await store.list_deletions(dry_run=False)
    assert {(d.episode, d.reason) for d in done} == {(1, "keep"), (4, "ahead"), (5, "ahead")}
    # Unmonitored (decoupled from delete): the kept anchor E2 (102) and the fileless ahead E6 (106).
    monitor_payloads = [json.loads(c.request.content) for c in routes["monitor"].calls]
    unmonitored = {
        eid for p in monitor_payloads if p["monitored"] is False for eid in p["episodeIds"]
    }
    assert {102, 106} <= unmonitored


@respx.mock
async def test_forward_trim_respects_always_have() -> None:
    await _configure(always_have=["S*E05"])  # protects E5 of each season
    routes = _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=2)

    # E5 protected: ahead only E4 is previewed (plus E1 behind).
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 4)}
    assert not routes["delete"].called


@respx.mock
async def test_unmonitors_watched_kept_episodes_without_deleting() -> None:
    # Whole season protected from deletion (Always-Have S*) + preload by episodes (GET=1).
    await _configure(always_have=["S*"])
    await set_dry_run(False)
    routes = _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=3)  # anchor E3, GET=1 → keeps E4 monitored

    # Nothing is deleted: S* protects every episode's file.
    assert not routes["delete"].called
    # But the watched/kept episodes on disk (E1-E3, E5) and the fileless E6 are unmonitored,
    # so the season never becomes fully monitored (no season-pack upgrade). E4 stays monitored.
    monitor_payloads = [json.loads(c.request.content) for c in routes["monitor"].calls]
    unmonitored = {
        eid for p in monitor_payloads if p["monitored"] is False for eid in p["episodeIds"]
    }
    monitored = {eid for p in monitor_payloads if p["monitored"] is True for eid in p["episodeIds"]}
    assert {101, 102, 103, 105, 106} <= unmonitored
    assert 104 in monitored
    assert 104 not in unmonitored


@respx.mock
async def test_cancels_season_pack_surplus_from_queue() -> None:
    """A search for the GET window can make Sonarr grab a full season pack; episodes downloading
    outside the window (GET ∪ KEEP ∪ Always-Have) are pulled from the queue so the whole season is
    not imported. Anchor E2, GET=1 → in-window = E2 (anchor) + E3 (GET)."""
    await _configure(always_have=[])  # GET=1, KEEP=1
    await set_dry_run(False)
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=AHEAD_EPISODES)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    # A season pack is downloading: E1, E3, E4, E5 in the queue.
    respx.get(f"{SONARR}/queue").mock(
        return_value=httpx.Response(
            200,
            json={
                "records": [
                    {"id": 11, "episodeId": 101},  # E1 - out of window → cancel
                    {"id": 13, "episodeId": 103},  # E3 - GET window → keep
                    {"id": 14, "episodeId": 104},  # E4 - out of window → cancel
                    {"id": 15, "episodeId": 105},  # E5 - out of window → cancel
                ]
            },
        )
    )
    cancel = respx.delete(url__regex=r"http://sonarr:8989/api/v3/queue/\d+").mock(
        return_value=httpx.Response(200)
    )

    await apply_window(TVDB, season=1, episode=2)

    cancelled = {int(c.request.url.path.rsplit("/", 1)[1]) for c in cancel.calls}
    assert cancelled == {11, 14, 15}  # E3 (in the GET window) is not pulled from the queue


def _mock_sonarr_seasons(series_obj: dict[str, object]) -> respx.Route:
    """Mocks so apply_window can set seasons: GET /series?tvdb, GET+PUT /series/1,
    episodes and writes. Returns the PUT /series/1 route to inspect the body."""
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[series_obj])
    )
    respx.get(f"{SONARR}/series/1").mock(return_value=httpx.Response(200, json=series_obj))
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=EPISODES)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))
    return respx.put(f"{SONARR}/series/1").mock(return_value=httpx.Response(200, json=series_obj))


@respx.mock
async def test_window_unmonitors_all_seasons_in_episode_mode() -> None:
    await _configure(always_have=[])  # GET=1 by episodes
    await set_dry_run(False)
    series_obj = {
        "id": 1,
        "title": "X",
        "tvdbId": TVDB,
        "seasons": [
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": True},
        ],
    }
    put = _mock_sonarr_seasons(series_obj)

    await apply_window(TVDB, season=1, episode=3)

    assert put.called
    body = json.loads(put.calls[0].request.content)
    assert {s["seasonNumber"]: s["monitored"] for s in body["seasons"]} == {1: False, 2: False}


@respx.mock
async def test_window_seasons_mode_monitors_only_window_seasons() -> None:
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    await set_global_policy(Policy(get_count=1, get_unit="seasons", keep_count=1, always_have=[]))
    await set_dry_run(False)
    series_obj = {
        "id": 1,
        "title": "X",
        "tvdbId": TVDB,
        "seasons": [
            {"seasonNumber": 1, "monitored": False},
            {"seasonNumber": 2, "monitored": False},
            {"seasonNumber": 3, "monitored": True},
        ],
    }
    put = _mock_sonarr_seasons(series_obj)

    # Anchor season 1, GET=1 season → on seasons 1 and 2; off season 3.
    await apply_window(TVDB, season=1, episode=3)

    body = json.loads(put.calls[0].request.content)
    monitored = {s["seasonNumber"]: s["monitored"] for s in body["seasons"]}
    assert monitored == {1: True, 2: True, 3: False}


@respx.mock
async def test_window_skips_series_put_when_seasons_already_correct() -> None:
    await _configure(always_have=[])  # GET=1 by episodes → all off
    await set_dry_run(False)
    series_obj = {
        "id": 1,
        "title": "X",
        "tvdbId": TVDB,
        "seasons": [{"seasonNumber": 1, "monitored": False}],  # already as it should be
    }
    put = _mock_sonarr_seasons(series_obj)

    await apply_window(TVDB, season=1, episode=3)

    assert not put.called  # idempotent: nothing to change, doesn't rewrite the series


@respx.mock
async def test_anchor_floored_at_persisted_furthest_watch() -> None:
    """An anchor below the furthest episode ever recorded as watched is re-anchored upward, so the
    window never slides back and re-downloads already-seen episodes (the sync creep)."""
    await _configure(always_have=[])  # GET=1, KEEP=1, dry-run ON
    _mock_sonarr(AHEAD_EPISODES)  # season 1, E1-E7
    await store.record_watch(TVDB, 1, 5)  # furthest watch on record is E5

    await apply_window(TVDB, season=1, episode=2)  # stale/low anchor E2

    # Floored to E5: KEEP=1 keeps E5 and deletes E1-E4 behind. (Un-floored E2 would have kept E1+E5
    # and trimmed E4/E5 ahead — the regression.)
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 2), (1, 3), (1, 4)}


@respx.mock
async def test_anchor_floor_clamps_to_episode_sonarr_lists() -> None:
    """If the furthest recorded watch isn't an episode Sonarr lists (deleted/absolute/special), the
    floor clamps to the furthest *real* watched episode instead of no-op'ing the window."""
    await _configure(always_have=[])  # GET=1, KEEP=1, dry-run ON
    _mock_sonarr(AHEAD_EPISODES)  # season 1, E1-E7 only
    await store.record_watch(TVDB, 1, 4)  # real
    await store.record_watch(TVDB, 2, 1)  # not in Sonarr's episode list → ignored by the clamp

    await apply_window(TVDB, season=1, episode=2)

    # Clamped to E4 (S02E01 ignored): KEEP=1 keeps E4, deletes E1-E3 behind.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 2), (1, 3)}


@respx.mock
async def test_anchor_floor_no_op_when_anchor_at_or_above_recorded() -> None:
    """When the anchor is at or above the recorded furthest watch, the floor changes nothing."""
    await _configure(always_have=[])  # GET=1, KEEP=1, dry-run ON
    _mock_sonarr()  # EPISODES E1-E5
    await store.record_watch(TVDB, 1, 2)  # below the anchor used below

    await apply_window(TVDB, season=1, episode=3)  # anchor E3 ≥ recorded E2 → unchanged

    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 2)}
