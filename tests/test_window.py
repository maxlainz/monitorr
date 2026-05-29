import httpx
import respx

from monitorr import constants, store
from monitorr.engine.policy import Policy, set_dry_run, set_global_policy
from monitorr.engine.window import apply_window

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
async def test_dry_run_previews_without_touching_sonarr() -> None:
    await _configure(always_have=[])  # dry-run ON por defecto
    routes = _mock_sonarr()

    await apply_window(TVDB, season=1, episode=3)

    # Interruptor maestro: en dry-run no se escribe nada en Sonarr.
    assert not routes["monitor"].called
    assert not routes["command"].called
    assert not routes["delete"].called
    # Pero el borrado que haría queda registrado como pendiente (KEEP=1: E1 y E2).
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

    assert routes["monitor"].called  # E4 monitorizado por delante
    assert routes["command"].called  # E4 buscado (sin fichero)
    assert routes["delete"].call_count == 2  # E1 y E2 borrados
    done = await store.list_deletions(dry_run=False)
    assert {(d.season, d.episode) for d in done} == {(1, 1), (1, 2)}
