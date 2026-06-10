import asyncio
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from monitorr import constants, store, sync
from monitorr.engine.policy import effective_policy, get_global_policy
from monitorr.main import app
from monitorr.plex.webhook import get_or_create_webhook_secret, parse_scrobble
from monitorr.web import routes


def test_health_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_pages_render() -> None:
    with TestClient(app) as client:
        for path in ("/", "/settings", "/series", "/deletions"):
            response = client.get(path)
            assert response.status_code == 200
            assert "monitorr" in response.text


@respx.mock
def test_plex_link_returns_auth_url() -> None:
    respx.post("https://plex.tv/api/v2/pins").mock(
        return_value=httpx.Response(200, json={"id": 123, "code": "ABCD"})
    )
    with TestClient(app) as client:
        response = client.post("/plex/link")
        assert response.status_code == 200
        assert "app.plex.tv/auth" in response.text


def _scrobble_payload(user: str = "alice") -> str:
    return json.dumps(
        {
            "event": "media.scrobble",
            "Account": {"title": user},
            "Metadata": {
                "type": "episode",
                "grandparentRatingKey": "1234",
                "parentIndex": 2,
                "index": 5,
            },
        }
    )


def test_parse_scrobble_extracts_user() -> None:
    event = parse_scrobble(json.loads(_scrobble_payload("bob")))
    assert event is not None
    assert event.user == "bob"
    assert event.grandparent_rating_key == "1234"
    assert (event.season, event.episode) == (2, 5)


def test_webhook_rejects_bad_secret() -> None:
    # The real secret is auto-generated, so an arbitrary one never matches.
    with TestClient(app) as client:
        response = client.post("/webhook/plex/wrong", data={"payload": "{}"})
        assert response.json() == {"status": "forbidden"}


def test_settings_shows_webhook_url() -> None:
    with TestClient(app) as client:
        page = client.get("/settings")
        assert "/webhook/plex/" in page.text


async def test_webhook_accepts_valid_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    await store.set_setting(constants.PLEX_SERVER_URI, "http://plex:32400")
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    secret = await get_or_create_webhook_secret()

    calls: list[tuple[int, int, int]] = []

    async def fake_resolve(uri: str, token: str, client_id: str, rating_key: str) -> int | None:
        return 999

    async def fake_process(tvdb_id: int, season: int, episode: int) -> None:
        calls.append((tvdb_id, season, episode))

    monkeypatch.setattr(routes, "resolve_tvdb_id", fake_resolve)
    monkeypatch.setattr(routes, "process_watch", fake_process)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/webhook/plex/{secret}", data={"payload": _scrobble_payload("alice")}
        )
    assert resp.json() == {"status": "ok"}
    assert calls == [(999, 2, 5)]


async def test_webhook_respects_user_filter() -> None:
    await store.set_setting(constants.PLEX_SERVER_URI, "http://plex:32400")
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    await store.set_setting(constants.USER_FILTER, json.dumps(["alice"]))
    secret = await get_or_create_webhook_secret()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/webhook/plex/{secret}", data={"payload": _scrobble_payload("bob")}
        )
    assert resp.json() == {"status": "filtered"}


def test_save_policy_clamps_negative_counts() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/settings/policy",
            data={"get_count": "-3", "keep_count": "-1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get("/settings")
        # The saved policy doesn't allow negatives (clamp to 0); there's no validation 500.
        assert page.status_code == 200
        assert 'name="get_count" min="0" value="0"' in page.text
        assert 'name="keep_count" min="0" value="0"' in page.text


def test_series_detail_renders_without_sonarr() -> None:
    # No Sonarr configured: the page still renders, pre-filled with the global policy.
    with TestClient(app) as client:
        page = client.get("/series/12345")
        assert page.status_code == 200
        assert "TVDB 12345" in page.text
        assert 'name="keep_count"' in page.text


async def test_series_policy_save_persists_and_merges() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/series/12345/policy",
            data={"get_count": "2", "keep_count": "3", "always_have": "S01E01"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    override = await store.get_override(12345)
    assert override is not None
    assert override.policy_json is not None
    policy, enabled = await effective_policy(12345)
    assert enabled is True
    assert (policy.get_count, policy.keep_count) == (2, 3)


async def test_series_policy_reset_follows_global() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/series/12345/policy", data={"keep_count": "7"})
        resp = await client.post("/series/12345/policy/reset", follow_redirects=False)
    assert resp.status_code == 303
    override = await store.get_override(12345)
    assert override is not None
    assert override.policy_json is None
    policy, _ = await effective_policy(12345)
    assert policy.keep_count == (await get_global_policy()).keep_count


def _capture_run_sync(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Replace sync.run_sync with a recorder of the force_full flag (the routes only enqueue it)."""
    calls: list[bool] = []

    async def fake_run_sync(force_full: bool = False) -> dict[str, int]:
        calls.append(force_full)
        return {}

    monkeypatch.setattr(sync, "run_sync", fake_run_sync)
    return calls


async def test_sync_button_forces_full(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_sync(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/sync", follow_redirects=False)
    await asyncio.sleep(0.05)  # let the background task run
    assert resp.status_code == 303
    assert calls == [True]  # manual "Sync now" = full reconciliation


async def test_connecting_both_deps_triggers_full_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_sync(monkeypatch)
    # Plex already linked; saving Sonarr completes the pair → a full sync is enqueued.
    await store.set_setting(constants.PLEX_SERVER_URI, "http://plex:32400")
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/settings/sonarr",
            data={"sonarr_url": "http://sonarr:8989", "sonarr_api_key": "key"},
            follow_redirects=False,
        )
    await asyncio.sleep(0.05)
    assert resp.status_code == 303
    assert calls == [True]


@respx.mock
async def test_single_server_link_triggers_full_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auto-selected single-server link path must fire the same "connection of both deps"
    full sync as the multi-server choose path (it previously skipped it)."""
    calls = _capture_run_sync(monkeypatch)
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    await store.set_setting("plex_pin_id", "123")
    await store.set_setting("plex_pin_code", "ABCD")
    respx.get("https://plex.tv/api/v2/pins/123").mock(
        return_value=httpx.Response(200, json={"authToken": "tok"})
    )
    respx.get("https://plex.tv/api/v2/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "provides": "server",
                    "name": "Home",
                    "clientIdentifier": "srv-1",
                    "accessToken": "srv-tok",
                    "connections": [{"uri": "http://plex:32400", "local": True, "relay": False}],
                }
            ],
        )
    )
    respx.get("https://plex.tv/api/v2/user/webhooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://plex.tv/api/v2/user/webhooks").mock(return_value=httpx.Response(201))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/plex/link/poll")
    await asyncio.sleep(0.05)
    assert resp.status_code == 200
    assert "linked" in resp.text or "Home" in resp.text
    assert calls == [True]


async def test_saving_sonarr_without_plex_does_not_trigger_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_run_sync(monkeypatch)  # no Plex configured
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/settings/sonarr",
            data={"sonarr_url": "http://sonarr:8989", "sonarr_api_key": "key"},
            follow_redirects=False,
        )
    await asyncio.sleep(0.05)
    assert resp.status_code == 303
    assert calls == []  # only one dependency → no full sync


async def test_unlink_clears_per_server_sync_state() -> None:
    """Unlinking drops the watermark/full stamp with the server: they describe THAT server's
    history and would make incrementals against the next server skip its plays."""
    await store.set_setting(constants.PLEX_SERVER_URI, "http://plex:32400")
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_SERVER_ID, "srv-1")
    await store.set_setting(constants.HISTORY_WATERMARK, "2026-01-01T00:00:00+00:00")
    await store.set_setting(constants.LAST_FULL_SYNC, "2026-01-01T00:00:00+00:00")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/plex/unlink", follow_redirects=False)
    assert resp.status_code == 303
    for key in (
        constants.PLEX_SERVER_ID,
        constants.HISTORY_WATERMARK,
        constants.LAST_FULL_SYNC,
    ):
        assert await store.get_setting(key) is None


@respx.mock
async def test_switching_servers_resets_per_server_sync_state() -> None:
    """Linking a different PMS (new clientIdentifier) resets the watermark/full stamp: a
    carried-over watermark newer than the new server's plays would bury them forever."""
    await store.set_setting(constants.PLEX_ACCOUNT_TOKEN, "acc-tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    await store.set_setting(constants.PLEX_SERVER_ID, "srv-old")
    await store.set_setting(constants.HISTORY_WATERMARK, "2099-01-01T00:00:00+00:00")
    await store.set_setting(constants.LAST_FULL_SYNC, "2026-01-01T00:00:00+00:00")
    respx.get("https://plex.tv/api/v2/resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "provides": "server",
                    "name": "New box",
                    "clientIdentifier": "srv-new",
                    "accessToken": "srv-tok",
                    "connections": [{"uri": "http://plex2:32400", "local": True, "relay": False}],
                }
            ],
        )
    )
    respx.get("https://plex.tv/api/v2/user/webhooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://plex.tv/api/v2/user/webhooks").mock(return_value=httpx.Response(201))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/plex/server", data={"client_identifier": "srv-new"}, follow_redirects=False
        )
    assert resp.status_code == 303
    assert await store.get_setting(constants.PLEX_SERVER_ID) == "srv-new"
    assert await store.get_setting(constants.HISTORY_WATERMARK) is None
    assert await store.get_setting(constants.LAST_FULL_SYNC) is None


async def test_toggle_enabled_preserves_policy_override() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/series/12345/policy", data={"keep_count": "5"})
        # Empty form = checkbox unchecked = disabled; must NOT wipe the stored policy.
        await client.post("/series/12345/override", data={})
    override = await store.get_override(12345)
    assert override is not None
    assert override.enabled is False
    assert override.policy_json is not None
    policy, enabled = await effective_policy(12345)
    assert enabled is False
    assert policy.keep_count == 5
