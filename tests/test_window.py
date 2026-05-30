import json

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


# E1-E5 en disco, E6 monitorizado sin fichero, E7 ni monitorizado ni en disco.
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

    await apply_window(TVDB, season=1, episode=2)  # ancla E2, GET=1 → conserva E3

    # Por delante de la ventana GET y en disco: E4, E5. Por detrás (KEEP=1): E1.
    # E6 (sin fichero) se desmonitoriza, no se borra; E7 no hace nada.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 4), (1, 5)}


@respx.mock
async def test_forward_trim_deletes_files_and_unmonitors_in_real_mode() -> None:
    await _configure(always_have=[])
    await set_dry_run(False)
    routes = _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=2)

    # Borrados: E1 (detrás, keep) + E4, E5 (delante, ahead).
    assert routes["delete"].call_count == 3
    done = await store.list_deletions(dry_run=False)
    assert {(d.episode, d.reason) for d in done} == {(1, "keep"), (4, "ahead"), (5, "ahead")}
    # E6 (sin fichero, monitorizado) se desmonitoriza, no se borra.
    monitor_payloads = [json.loads(c.request.content) for c in routes["monitor"].calls]
    assert {"episodeIds": [106], "monitored": False} in monitor_payloads


@respx.mock
async def test_forward_trim_respects_always_have() -> None:
    await _configure(always_have=["S*E05"])  # protege el E5 de cada temporada
    routes = _mock_sonarr(AHEAD_EPISODES)

    await apply_window(TVDB, season=1, episode=2)

    # E5 protegido: por delante solo se previsualiza E4 (más E1 por detrás).
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 1), (1, 4)}
    assert not routes["delete"].called


def _mock_sonarr_seasons(series_obj: dict[str, object]) -> respx.Route:
    """Mocks para que apply_window pueda fijar temporadas: GET /series?tvdb, GET+PUT /series/1,
    episodios y escrituras. Devuelve la ruta PUT /series/1 para inspeccionar el body."""
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
    return respx.put(f"{SONARR}/series/1").mock(return_value=httpx.Response(200, json=series_obj))


@respx.mock
async def test_window_unmonitors_all_seasons_in_episode_mode() -> None:
    await _configure(always_have=[])  # GET=1 por episodios
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

    # Ancla temporada 1, GET=1 temporada → on las temporadas 1 y 2; off la 3.
    await apply_window(TVDB, season=1, episode=3)

    body = json.loads(put.calls[0].request.content)
    monitored = {s["seasonNumber"]: s["monitored"] for s in body["seasons"]}
    assert monitored == {1: True, 2: True, 3: False}


@respx.mock
async def test_window_skips_series_put_when_seasons_already_correct() -> None:
    await _configure(always_have=[])  # GET=1 por episodios → todas off
    await set_dry_run(False)
    series_obj = {
        "id": 1,
        "title": "X",
        "tvdbId": TVDB,
        "seasons": [{"seasonNumber": 1, "monitored": False}],  # ya está como debe
    }
    put = _mock_sonarr_seasons(series_obj)

    await apply_window(TVDB, season=1, episode=3)

    assert not put.called  # idempotente: nada que cambiar, no reescribe la serie
