import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from monitorr import constants, store
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
