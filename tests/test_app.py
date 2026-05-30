import httpx
import respx
from fastapi.testclient import TestClient

from monitorr.main import app


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


def test_webhook_rejects_bad_secret() -> None:
    with TestClient(app) as client:
        response = client.post("/webhook/plex/wrong", data={"payload": "{}"})
        assert response.json() == {"status": "forbidden"}


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
