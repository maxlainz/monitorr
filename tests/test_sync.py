import json

import httpx
import respx

from monitorr import constants, store, sync
from monitorr.engine.policy import Policy, set_dry_run, set_global_policy

PLEX = "http://plex:32400"
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
]

ALL_LEAVES = [
    {"parentIndex": 1, "index": 1, "viewCount": 1, "lastViewedAt": 1700000000},
    {"parentIndex": 1, "index": 2, "viewCount": 1, "lastViewedAt": 1700001000},
    {"parentIndex": 1, "index": 3, "viewCount": 0},
]


async def _configure_links() -> None:
    await store.set_setting(constants.PLEX_SERVER_URI, PLEX)
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[]))


def _mock_plex(tvdb_id: int) -> respx.Route:
    """Registers sections + all + allLeaves; returns the allLeaves route for asserts."""
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "X", "Guid": [{"id": f"tvdb://{tvdb_id}"}]}
                    ]
                }
            },
        )
    )
    return respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )


def _mock_sonarr() -> dict[str, respx.Route]:
    return {
        "series": respx.get("http://sonarr:8989/api/v3/series").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
        ),
        "episodes": respx.get("http://sonarr:8989/api/v3/episode").mock(
            return_value=httpx.Response(200, json=EPISODES)
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
async def test_sync_seeds_watches_and_applies_window() -> None:
    await _configure_links()
    await set_dry_run(False)
    _mock_plex(TVDB)
    routes = _mock_sonarr()

    summary = await sync.run_sync()

    # 999 watched → not normalized; all episodes on disk → nothing to re-search
    assert summary == {"shows": 1, "matched": 1, "normalized": 0, "searched": 0}
    watches = await store.get_watches(TVDB)
    assert {(w.season, w.episode) for w in watches} == {(1, 1), (1, 2)}
    # Anchor = last watched (1,2): monitors ahead (E3) and deletes behind (E1).
    assert routes["monitor"].called
    assert routes["delete"].call_count == 1
    last = await sync.get_last_sync()
    assert last is not None and last["matched"] == 1


@respx.mock
async def test_sync_skips_shows_not_in_sonarr() -> None:
    await _configure_links()
    leaves = _mock_plex(888)  # tvdb not present in Sonarr
    _mock_sonarr()

    summary = await sync.run_sync()

    # 999 managed and unwatched
    assert summary == {"shows": 1, "matched": 0, "normalized": 1, "searched": 0}
    assert not leaves.called  # episodes of unmanaged shows are not requested


@respx.mock
async def test_sync_continues_when_one_series_errors() -> None:
    """A show error (allLeaves 500) doesn't abort the sync: the other is processed anyway."""
    await _configure_links()
    await set_dry_run(False)

    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "OK", "Guid": [{"id": f"tvdb://{TVDB}"}]},
                        {"ratingKey": "200", "title": "Boom", "Guid": [{"id": "tvdb://888"}]},
                    ]
                }
            },
        )
    )
    respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )
    respx.get(f"{PLEX}/library/metadata/200/allLeaves").mock(return_value=httpx.Response(500))
    # Both shows managed; 999 goes first so find_series_by_tvdb returns the correct one.
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "OK", "tvdbId": TVDB},
                {"id": 2, "title": "Boom", "tvdbId": 888},
            ],
        )
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=EPISODES)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))

    summary = await sync.run_sync()

    # the failing one doesn't count as matched; having no viewing, it's normalized to pilot.
    assert summary == {"shows": 2, "matched": 1, "normalized": 1, "searched": 0}
    assert {(w.season, w.episode) for w in await store.get_watches(TVDB)} == {(1, 1), (1, 2)}
    last = await sync.get_last_sync()  # updated despite one show's error
    assert last is not None and last["matched"] == 1


def _mock_empty_plex() -> None:
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": []}})
    )


@respx.mock
async def test_sync_auto_normalizes_unwatched_managed_series() -> None:
    """A managed show without viewing is reduced to pilot in the sync (preview in dry-run)."""
    await _configure_links()  # always_have=[], dry-run ON by default
    _mock_empty_plex()
    routes = _mock_sonarr()  # show 999 managed with E1, E2, E3 on disk

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 1, "searched": 0}
    # Keeps the pilot (S01E01); the rest downloaded is left as pending deletion.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2), (1, 3)}
    assert not routes["delete"].called  # dry-run: doesn't write to Sonarr


@respx.mock
async def test_sync_auto_normalize_disabled_by_policy() -> None:
    """With auto_normalize=False, a show without viewing is not touched."""
    await _configure_links()
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False))
    _mock_empty_plex()
    _mock_sonarr()

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 0}
    assert await store.list_deletions(dry_run=True) == []


# monitored + aired + no file = Sonarr's "Missing"; one of them is mid-download (in the queue).
MISSING_EPISODES = [
    {
        "id": 301,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2020-01-01T00:00:00Z",
    },  # missing → re-search
    {
        "id": 302,
        "seasonNumber": 1,
        "episodeNumber": 2,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2020-01-08T00:00:00Z",
    },  # in the queue → skip
    {
        "id": 303,
        "seasonNumber": 1,
        "episodeNumber": 3,
        "monitored": True,
        "hasFile": True,
        "episodeFileId": 403,
        "airDateUtc": "2020-01-15T00:00:00Z",
    },  # on disk → skip
    {
        "id": 304,
        "seasonNumber": 1,
        "episodeNumber": 4,
        "monitored": False,
        "hasFile": False,
        "airDateUtc": "2020-01-22T00:00:00Z",
    },  # unmonitored → skip
    {
        "id": 305,
        "seasonNumber": 1,
        "episodeNumber": 5,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2999-01-01T00:00:00Z",
    },  # not aired yet → skip
]


@respx.mock
async def test_sync_researches_missing_excluding_queue() -> None:
    """Each sync re-searches the Missing episodes (monitored, aired, no file) except the ones
    already downloading. auto_normalize off + no viewing isolates the re-search pass."""
    await _configure_links()
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False))
    await set_dry_run(False)
    _mock_empty_plex()
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=MISSING_EPISODES)
    )
    respx.get(f"{SONARR}/queue").mock(
        return_value=httpx.Response(200, json={"records": [{"id": 1, "episodeId": 302}]})
    )
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 1}
    assert command.call_count == 1
    body = json.loads(command.calls.last.request.content)
    assert body == {"name": "EpisodeSearch", "episodeIds": [301]}


@respx.mock
async def test_sync_skips_missing_search_when_search_on_get_disabled() -> None:
    """search_on_get gates the re-search too: with it off, no EpisodeSearch is issued."""
    await _configure_links()
    await set_global_policy(
        Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False, search_on_get=False)
    )
    await set_dry_run(False)
    _mock_empty_plex()
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=MISSING_EPISODES)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 0}
    assert not command.called
