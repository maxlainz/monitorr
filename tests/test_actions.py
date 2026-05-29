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

    # E2 protegido por Always-Have: se desmonitoriza pero NO se borra.
    await actions.normalize_to_pilot(
        "http://sonarr:8989", "key", TVDB, SERIES, EPISODES, ["S01E02"], False
    )

    assert not delete.called
