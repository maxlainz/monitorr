import json

import httpx
import respx

from monitorr import store
from monitorr.engine import actions
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

SONARR = "http://sonarr:8989/api/v3"
TVDB = 999
SERIES = SonarrSeries.model_validate({"id": 1, "title": "X", "tvdbId": TVDB})
EPISODES = [
    SonarrEpisode.model_validate(
        {"id": 101, "seasonNumber": 1, "episodeNumber": 1, "hasFile": False, "episodeFileId": 0}
    ),
    SonarrEpisode.model_validate(
        {"id": 102, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True, "episodeFileId": 202}
    ),
]


@respx.mock
async def test_normalize_dry_run_previews_delete_without_calls() -> None:
    monitor = respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    delete = respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )

    await actions.normalize_to_pilot("http://sonarr:8989", "key", TVDB, SERIES, EPISODES, [], True)

    assert not monitor.called
    assert not delete.called
    # El descargado no-piloto (E2) queda como pendiente de borrado.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2)}


@respx.mock
async def test_normalize_real_deletes_downloaded_and_monitors_pilot() -> None:
    monitor = respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    delete = respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )

    await actions.normalize_to_pilot("http://sonarr:8989", "key", TVDB, SERIES, EPISODES, [], False)

    assert delete.call_count == 1  # E2 (descargado, no piloto) se borra
    assert command.called  # piloto sin fichero → búsqueda
    assert monitor.call_count == 2  # desmonitorizar E2 (en el borrado) + monitorizar piloto


@respx.mock
async def test_normalize_keeps_always_have_file() -> None:
    delete = respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))

    # E2 protegido por Always-Have: se desmonitoriza pero NO se borra.
    await actions.normalize_to_pilot(
        "http://sonarr:8989", "key", TVDB, SERIES, EPISODES, ["S01E02"], False
    )

    assert not delete.called


@respx.mock
async def test_normalize_unmonitors_all_seasons() -> None:
    series_obj = {
        "id": 1,
        "title": "X",
        "tvdbId": TVDB,
        "seasons": [
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": True},
        ],
    }
    series = SonarrSeries.model_validate(series_obj)
    respx.get(f"{SONARR}/series/1").mock(return_value=httpx.Response(200, json=series_obj))
    put = respx.put(f"{SONARR}/series/1").mock(return_value=httpx.Response(200, json=series_obj))
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )

    await actions.normalize_to_pilot("http://sonarr:8989", "key", TVDB, series, EPISODES, [], False)

    body = json.loads(put.calls[0].request.content)
    assert {s["seasonNumber"]: s["monitored"] for s in body["seasons"]} == {1: False, 2: False}


@respx.mock
async def test_cancel_downloads_removes_from_queue_keeping_seed() -> None:
    respx.get(f"{SONARR}/queue").mock(
        return_value=httpx.Response(
            200, json={"records": [{"id": 5, "episodeId": 102}, {"id": 6, "episodeId": 777}]}
        )
    )
    delete = respx.delete(url__regex=r"http://sonarr:8989/api/v3/queue/\d+").mock(
        return_value=httpx.Response(200)
    )

    await actions.cancel_downloads("http://sonarr:8989", "key", [102], dry_run=False)

    assert delete.call_count == 1  # solo el ítem del episodio 102 (id 5)
    url = str(delete.calls[0].request.url)
    assert "/queue/5" in url
    assert "removeFromClient=false" in url  # se deja sembrando


@respx.mock
async def test_cancel_downloads_dry_run_makes_no_calls() -> None:
    queue = respx.get(f"{SONARR}/queue").mock(
        return_value=httpx.Response(200, json={"records": []})
    )

    await actions.cancel_downloads("http://sonarr:8989", "key", [102], dry_run=True)

    assert not queue.called
