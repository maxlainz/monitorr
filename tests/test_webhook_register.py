"""Auto-registration of monitorr's webhook in the account-level Plex webhook list."""

from urllib.parse import parse_qsl

import httpx
import respx

from monitorr import constants, store
from monitorr.main import app
from monitorr.plex.webhook import (
    is_webhook_registered,
    register_webhook,
    resync_webhook,
    unregister_webhooks,
)

WEBHOOKS = "https://plex.tv/api/v2/user/webhooks"

OTHER = "https://ha.local/api/webhook/plex"
OLD = "http://old:8080/webhook/plex/OLD"
NEW = "http://new:8080/webhook/plex/NEW"


def _posted_urls(route: respx.Route) -> list[str]:
    """The `urls[]` values from the last POST body (empty list when the set was cleared)."""
    body = route.calls.last.request.content.decode()
    return [value for key, value in parse_qsl(body) if key == "urls[]"]


@respx.mock
async def test_register_appends_and_replaces_monitorr_entry() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}, {"url": OLD}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    await register_webhook("tok", "cid", NEW)

    # Third-party URL preserved, stale monitorr entry dropped, new one appended.
    assert _posted_urls(post) == [OTHER, NEW]


@respx.mock
async def test_unregister_removes_only_monitorr_entries() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}, {"url": OLD}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    await unregister_webhooks("tok", "cid")

    assert _posted_urls(post) == [OTHER]


@respx.mock
async def test_unregister_is_noop_when_nothing_ours() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    await unregister_webhooks("tok", "cid")

    assert not post.called


@respx.mock
async def test_resync_replaces_when_registered() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OLD}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    assert await resync_webhook("tok", "cid", NEW) is True
    assert _posted_urls(post) == [NEW]


@respx.mock
async def test_resync_is_noop_when_not_registered() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    assert await resync_webhook("tok", "cid", NEW) is False
    assert not post.called


@respx.mock
async def test_is_webhook_registered() -> None:
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OLD}]))

    assert await is_webhook_registered("tok", "cid", OLD) is True
    assert await is_webhook_registered("tok", "cid", NEW) is False


@respx.mock
async def test_route_register_pushes_to_plex() -> None:
    await store.set_setting(constants.PLEX_ACCOUNT_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook/register", data={"webhook_url": NEW}, follow_redirects=False
        )

    assert resp.status_code == 303
    assert _posted_urls(post) == [OTHER, NEW]


@respx.mock
async def test_route_unregister_pushes_to_plex() -> None:
    await store.set_setting(constants.PLEX_ACCOUNT_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    respx.get(WEBHOOKS).mock(return_value=httpx.Response(200, json=[{"url": OTHER}, {"url": OLD}]))
    post = respx.post(WEBHOOKS).mock(return_value=httpx.Response(200, json=[]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/unregister", follow_redirects=False)

    assert resp.status_code == 303
    assert _posted_urls(post) == [OTHER]
