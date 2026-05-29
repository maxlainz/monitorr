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
    """Registra sections + all + allLeaves; devuelve la ruta allLeaves para asserts."""
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
    }


@respx.mock
async def test_sync_seeds_watches_and_applies_window() -> None:
    await _configure_links()
    await set_dry_run(False)
    _mock_plex(TVDB)
    routes = _mock_sonarr()

    summary = await sync.run_sync()

    assert summary == {"shows": 1, "matched": 1}
    watches = await store.get_watches(TVDB)
    assert {(w.season, w.episode) for w in watches} == {(1, 1), (1, 2)}
    # Ancla = último visto (1,2): monitoriza por delante (E3) y borra por detrás (E1).
    assert routes["monitor"].called
    assert routes["delete"].call_count == 1
    last = await sync.get_last_sync()
    assert last is not None and last["matched"] == 1


@respx.mock
async def test_sync_skips_shows_not_in_sonarr() -> None:
    await _configure_links()
    leaves = _mock_plex(888)  # tvdb no presente en Sonarr
    _mock_sonarr()

    summary = await sync.run_sync()

    assert summary == {"shows": 1, "matched": 0}
    assert not leaves.called  # no se piden episodios de shows no gestionados
