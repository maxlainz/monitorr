"""Connection pooling scoped to the sync cycle (see sync._run). Inside a `pooled_session` every
call reuses one client; outside it (poller/routes path) each call gets a fresh one — unchanged."""

from monitorr.plex import client as plex
from monitorr.sonarr import client as sonarr

SONARR = ("http://sonarr:8989", "key")


async def test_plex_pooled_session_reuses_one_client() -> None:
    assert plex._pooled.get() is None
    async with plex.pooled_session():
        assert plex._pooled.get() is not None  # set for the duration of the block
        async with plex._http() as a, plex._http() as b:
            assert a is b  # same pooled client across calls within the cycle
    assert plex._pooled.get() is None  # reset on exit


async def test_plex_http_is_fresh_without_session() -> None:
    async with plex._http() as a:
        pass
    async with plex._http() as b:
        pass
    assert a is not b  # poller/routes path: a client per call, as before


async def test_sonarr_pooled_session_reuses_one_client() -> None:
    assert sonarr._pooled.get() is None
    # pooled_session is entered first (sets the ContextVar); the two _client calls then reuse it.
    async with (
        sonarr.pooled_session(*SONARR),
        sonarr._client(*SONARR) as a,
        sonarr._client(*SONARR) as b,
    ):
        assert a is b
    assert sonarr._pooled.get() is None


async def test_sonarr_client_is_fresh_without_session() -> None:
    async with sonarr._client(*SONARR) as a:
        pass
    async with sonarr._client(*SONARR) as b:
        pass
    assert a is not b
