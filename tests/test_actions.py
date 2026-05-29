import httpx
import respx

from monitorr.engine import actions
from monitorr.sonarr.client import SonarrEpisode, SonarrSeries

SONARR = "http://sonarr:8989/api/v3"
SERIES = SonarrSeries.model_validate({"id": 1, "title": "X", "tvdbId": 999})
EPISODES = [
    SonarrEpisode.model_validate(
        {"id": 101, "seasonNumber": 1, "episodeNumber": 1, "hasFile": False, "episodeFileId": 0}
    ),
    SonarrEpisode.model_validate(
        {"id": 102, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True, "episodeFileId": 202}
    ),
]


@respx.mock
async def test_normalize_dry_run_makes_no_calls() -> None:
    monitor = respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    await actions.normalize_to_pilot("http://sonarr:8989", "key", SERIES, EPISODES, dry_run=True)
    assert not monitor.called


@respx.mock
async def test_normalize_real_unmonitors_all_then_monitors_pilot() -> None:
    monitor = respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    await actions.normalize_to_pilot("http://sonarr:8989", "key", SERIES, EPISODES, dry_run=False)
    assert monitor.call_count == 2  # desmonitoriza todo + monitoriza piloto
    assert command.called  # piloto sin fichero → búsqueda
