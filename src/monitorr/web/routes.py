import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from monitorr import __version__, constants, store, sync
from monitorr.config import get_settings
from monitorr.engine.policy import (
    Policy,
    effective_policy,
    get_dry_run,
    get_global_policy,
    get_user_filter,
    get_watched_threshold,
    set_dry_run,
    set_global_policy,
)
from monitorr.plex import auth as plex_auth
from monitorr.plex.client import (
    PlexServer,
    choose_connection,
    discover_servers,
    resolve_tvdb_id,
)
from monitorr.plex.poller import process_watch
from monitorr.plex.webhook import (
    get_or_create_webhook_secret,
    is_webhook_registered,
    parse_scrobble,
    regenerate_webhook_secret,
    register_webhook,
    resync_webhook,
    unregister_webhooks,
)
from monitorr.sonarr import client as sonarr

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals["app_version"] = __version__

router = APIRouter()

_PIN_ID = "plex_pin_id"
_PIN_CODE = "plex_pin_code"

# Keep a reference to background tasks launched from routes so the GC doesn't collect them.
_bg_tasks: set[asyncio.Task[dict[str, int]]] = set()


def _forward_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/settings"


def _webhook_url(request: Request, secret: str) -> str:
    return str(request.base_url).rstrip("/") + f"/webhook/plex/{secret}"


async def _plex_status() -> dict[str, Any]:
    name = await store.get_setting(constants.PLEX_SERVER_NAME)
    return {"linked": name is not None, "name": name}


async def _store_server(server: PlexServer) -> bool:
    uri = choose_connection(server.connections)
    if uri is None:
        return False
    await store.set_setting(constants.PLEX_SERVER_URI, uri)
    await store.set_setting(constants.PLEX_SERVER_TOKEN, server.access_token)
    await store.set_setting(constants.PLEX_SERVER_NAME, server.name)
    return True


async def _plex_account() -> tuple[str, str] | None:
    """The account token + client id, present once linked."""
    token = await store.get_setting(constants.PLEX_ACCOUNT_TOKEN)
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    return (token, client_id) if token and client_id else None


async def _try_register_webhook(request: Request) -> None:
    """Best-effort: push monitorr's webhook URL into the linked Plex account (Plex Pass)."""
    account = await _plex_account()
    if account is None:
        return
    secret = await get_or_create_webhook_secret()
    try:
        await register_webhook(*account, _webhook_url(request, secret))
    except Exception:
        logger.exception("could not register Plex webhook")


async def _webhook_registered(webhook_url: str) -> bool | None:
    """Whether monitorr's URL is in the Plex account list; None when it can't be checked."""
    account = await _plex_account()
    if account is None:
        return None
    try:
        return await is_webhook_registered(*account, webhook_url)
    except Exception:
        logger.warning("could not read Plex webhook status", exc_info=True)
        return None


# --- pages ---


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    sonarr_cfg = await sonarr.get_config()
    context = {
        "plex": await _plex_status(),
        "sonarr_configured": sonarr_cfg is not None,
        "dry_run": await get_dry_run(),
        "deletions": await store.list_deletions(limit=10),
        "pending": await store.list_deletions(limit=1000, dry_run=True),
        "last_sync": await sync.get_last_sync(),
        "sync_running": sync.is_running(),
    }
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    sonarr_url = await store.get_setting(constants.SONARR_URL)
    secret = await get_or_create_webhook_secret()
    webhook_url = _webhook_url(request, secret)
    context = {
        "plex": await _plex_status(),
        "sonarr_url": sonarr_url or "",
        "sonarr_configured": await sonarr.get_config() is not None,
        "policy": await get_global_policy(),
        "dry_run": await get_dry_run(),
        "watched_threshold": await get_watched_threshold(),
        "user_filter": ", ".join(await get_user_filter()),
        "webhook_url": webhook_url,
        "webhook_pinned_by_env": bool(get_settings().webhook_secret),
        "webhook_registered": await _webhook_registered(webhook_url),
    }
    return templates.TemplateResponse(request, "settings.html", context)


@router.get("/series", response_class=HTMLResponse)
async def series_page(request: Request) -> HTMLResponse:
    cfg = await sonarr.get_config()
    series_rows: list[dict[str, Any]] = []
    if cfg is not None:
        overrides = {o.tvdb_id: o for o in await store.list_overrides()}
        for s in await sonarr.list_series(*cfg):
            override = overrides.get(s.tvdb_id)
            series_rows.append(
                {
                    "tvdb_id": s.tvdb_id,
                    "title": s.title,
                    "enabled": override.enabled if override else True,
                    "custom": override is not None and override.policy_json is not None,
                }
            )
    return templates.TemplateResponse(
        request, "series.html", {"series": series_rows, "configured": cfg is not None}
    )


@router.get("/deletions", response_class=HTMLResponse)
async def deletions_page(request: Request) -> HTMLResponse:
    context = {
        "pending": await store.list_deletions(limit=200, dry_run=True),
        "done": await store.list_deletions(limit=200, dry_run=False),
    }
    return templates.TemplateResponse(request, "deletions.html", context)


# --- settings: Sonarr ---


@router.post("/settings/sonarr")
async def save_sonarr(
    sonarr_url: str = Form(...), sonarr_api_key: str = Form(...)
) -> RedirectResponse:
    await store.set_setting(constants.SONARR_URL, sonarr_url.strip())
    await store.set_setting(constants.SONARR_API_KEY, sonarr_api_key.strip())
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/sonarr/test", response_class=HTMLResponse)
async def test_sonarr(
    request: Request, sonarr_url: str = Form(...), sonarr_api_key: str = Form(...)
) -> HTMLResponse:
    version = await sonarr.system_status(sonarr_url.strip(), sonarr_api_key.strip())
    return templates.TemplateResponse(request, "_sonarr_test.html", {"version": version})


# --- settings: policy ---


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


def _policy_from_form(
    get_count: int,
    get_unit: str,
    keep_count: int,
    keep_unit: str,
    always_have: str,
    grace_watched_days: str,
    grace_unwatched_days: str,
    dormant_days: str,
    grace_completed_days: str,
    search_on_get: str | None,
    auto_normalize: str | None,
) -> Policy:
    """Build a Policy from the shared form fields (global and per-series forms)."""

    def _opt_int(value: str) -> int | None:
        value = value.strip()
        return int(value) if value else None

    return Policy(
        get_count=max(0, get_count),
        get_unit="seasons" if get_unit == "seasons" else "episodes",
        keep_count=max(0, keep_count),
        keep_unit="seasons" if keep_unit == "seasons" else "episodes",
        always_have=_split(always_have) or ["S01E01"],
        grace_watched_days=_opt_int(grace_watched_days),
        grace_unwatched_days=_opt_int(grace_unwatched_days),
        dormant_days=_opt_int(dormant_days),
        grace_completed_days=_opt_int(grace_completed_days),
        search_on_get=search_on_get is not None,
        auto_normalize=auto_normalize is not None,
    )


@router.post("/settings/policy")
async def save_policy(
    get_count: int = Form(1),
    get_unit: str = Form("episodes"),
    keep_count: int = Form(1),
    keep_unit: str = Form("episodes"),
    always_have: str = Form(""),
    grace_watched_days: str = Form(""),
    grace_unwatched_days: str = Form(""),
    dormant_days: str = Form(""),
    grace_completed_days: str = Form(""),
    search_on_get: str | None = Form(None),
    auto_normalize: str | None = Form(None),
    watched_threshold: float = Form(0.9),
    user_filter: str = Form(""),
    dry_run: str | None = Form(None),
) -> RedirectResponse:
    policy = _policy_from_form(
        get_count,
        get_unit,
        keep_count,
        keep_unit,
        always_have,
        grace_watched_days,
        grace_unwatched_days,
        dormant_days,
        grace_completed_days,
        search_on_get,
        auto_normalize,
    )
    await set_global_policy(policy)
    await set_dry_run(dry_run is not None)
    await store.set_setting(constants.WATCHED_THRESHOLD, str(watched_threshold))
    await store.set_setting(constants.USER_FILTER, json.dumps(_split(user_filter)))
    return RedirectResponse(url="/settings", status_code=303)


# --- Plex linking ---


@router.post("/plex/link", response_class=HTMLResponse)
async def plex_link(request: Request) -> HTMLResponse:
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if not client_id:
        client_id = plex_auth.generate_client_id()
        await store.set_setting(constants.PLEX_CLIENT_ID, client_id)
    pin_id, code = await plex_auth.create_pin(client_id)
    await store.set_setting(_PIN_ID, str(pin_id))
    await store.set_setting(_PIN_CODE, code)
    auth_url = plex_auth.build_auth_url(client_id, code, _forward_url(request))
    return templates.TemplateResponse(
        request, "_plex_link.html", {"state": "waiting", "auth_url": auth_url}
    )


@router.get("/plex/link/poll", response_class=HTMLResponse)
async def plex_link_poll(request: Request) -> HTMLResponse:
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    pin_id = await store.get_setting(_PIN_ID)
    code = await store.get_setting(_PIN_CODE)
    if not (client_id and pin_id and code):
        return templates.TemplateResponse(
            request, "_plex_link.html", {"state": "error", "message": "Restart the linking."}
        )

    token = await plex_auth.poll_pin(int(pin_id), code, client_id)
    if token is None:
        auth_url = plex_auth.build_auth_url(client_id, code, _forward_url(request))
        return templates.TemplateResponse(
            request, "_plex_link.html", {"state": "waiting", "auth_url": auth_url}
        )

    await store.set_setting(constants.PLEX_ACCOUNT_TOKEN, token)
    servers = await discover_servers(token, client_id)
    if not servers:
        return templates.TemplateResponse(
            request,
            "_plex_link.html",
            {"state": "error", "message": "No Plex servers found."},
        )
    if len(servers) == 1 and await _store_server(servers[0]):
        await _try_register_webhook(request)
        return templates.TemplateResponse(
            request, "_plex_link.html", {"state": "linked", "server_name": servers[0].name}
        )
    return templates.TemplateResponse(
        request, "_plex_link.html", {"state": "choose", "servers": servers}
    )


@router.post("/plex/server")
async def plex_choose_server(
    request: Request, client_identifier: str = Form(...)
) -> RedirectResponse:
    token = await store.get_setting(constants.PLEX_ACCOUNT_TOKEN)
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if token and client_id:
        for server in await discover_servers(token, client_id):
            if server.client_identifier == client_identifier:
                if await _store_server(server):
                    await _try_register_webhook(request)
                break
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/plex/unlink")
async def plex_unlink() -> RedirectResponse:
    # Remove monitorr's webhook from Plex first, while we still hold the account token.
    account = await _plex_account()
    if account is not None:
        try:
            await unregister_webhooks(*account)
        except Exception:
            logger.exception("could not remove Plex webhook on unlink")
    for key in (
        constants.PLEX_ACCOUNT_TOKEN,
        constants.PLEX_SERVER_URI,
        constants.PLEX_SERVER_TOKEN,
        constants.PLEX_SERVER_NAME,
    ):
        await store.delete_setting(key)
    return RedirectResponse(url="/settings", status_code=303)


# --- per-series override ---


@router.get("/series/{tvdb_id}", response_class=HTMLResponse)
async def series_detail(request: Request, tvdb_id: int) -> HTMLResponse:
    cfg = await sonarr.get_config()
    title: str | None = None
    if cfg is not None:
        series = await sonarr.find_series_by_tvdb(*cfg, tvdb_id)
        title = series.title if series else None
    override = await store.get_override(tvdb_id)
    policy, enabled = await effective_policy(tvdb_id)
    return templates.TemplateResponse(
        request,
        "series_detail.html",
        {
            "tvdb_id": tvdb_id,
            "title": title,
            "policy": policy,
            "enabled": enabled,
            "override_active": override is not None and override.policy_json is not None,
        },
    )


@router.post("/series/{tvdb_id}/override")
async def series_override(tvdb_id: int, enabled: str | None = Form(None)) -> RedirectResponse:
    # Preserve any per-series policy override; only flip the enabled flag.
    existing = await store.get_override(tvdb_id)
    policy_json = existing.policy_json if existing else None
    await store.set_override(tvdb_id, enabled is not None, policy_json)
    return RedirectResponse(url="/series", status_code=303)


@router.post("/series/{tvdb_id}/policy")
async def save_series_policy(
    tvdb_id: int,
    get_count: int = Form(1),
    get_unit: str = Form("episodes"),
    keep_count: int = Form(1),
    keep_unit: str = Form("episodes"),
    always_have: str = Form(""),
    grace_watched_days: str = Form(""),
    grace_unwatched_days: str = Form(""),
    dormant_days: str = Form(""),
    grace_completed_days: str = Form(""),
    search_on_get: str | None = Form(None),
    auto_normalize: str | None = Form(None),
) -> RedirectResponse:
    policy = _policy_from_form(
        get_count,
        get_unit,
        keep_count,
        keep_unit,
        always_have,
        grace_watched_days,
        grace_unwatched_days,
        dormant_days,
        grace_completed_days,
        search_on_get,
        auto_normalize,
    )
    existing = await store.get_override(tvdb_id)
    enabled = existing.enabled if existing else True
    await store.set_override(tvdb_id, enabled, policy.model_dump_json())
    return RedirectResponse(url=f"/series/{tvdb_id}", status_code=303)


@router.post("/series/{tvdb_id}/policy/reset")
async def reset_series_policy(tvdb_id: int) -> RedirectResponse:
    # Drop the per-series policy (follow the global one again); keep the enabled flag.
    existing = await store.get_override(tvdb_id)
    enabled = existing.enabled if existing else True
    await store.set_override(tvdb_id, enabled, None)
    return RedirectResponse(url=f"/series/{tvdb_id}", status_code=303)


# --- sync ---


@router.post("/sync")
async def trigger_sync() -> RedirectResponse:
    if not sync.is_running():
        task = asyncio.create_task(sync.run_sync())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    return RedirectResponse(url="/", status_code=303)


# --- optional webhook ---


@router.post("/webhook/regenerate")
async def webhook_regenerate(request: Request) -> RedirectResponse:
    # Pinned by env var → nothing to rotate from the UI.
    if get_settings().webhook_secret:
        return RedirectResponse(url="/settings", status_code=303)
    await regenerate_webhook_secret()
    # If the webhook was registered in Plex, swap the stale URL for the new one.
    account = await _plex_account()
    if account is not None:
        secret = await get_or_create_webhook_secret()
        try:
            await resync_webhook(*account, _webhook_url(request, secret))
        except Exception:
            logger.exception("could not re-register Plex webhook after regenerate")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/webhook/register")
async def webhook_register(webhook_url: str = Form(...)) -> RedirectResponse:
    account = await _plex_account()
    if account is not None:
        try:
            await register_webhook(*account, webhook_url.strip())
        except Exception:
            logger.exception("could not register Plex webhook")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/webhook/unregister")
async def webhook_unregister() -> RedirectResponse:
    account = await _plex_account()
    if account is not None:
        try:
            await unregister_webhooks(*account)
        except Exception:
            logger.exception("could not remove Plex webhook")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/webhook/plex/{secret}")
async def plex_webhook(secret: str, request: Request) -> dict[str, str]:
    expected = await get_or_create_webhook_secret()
    if not secrets.compare_digest(secret, expected):
        return {"status": "forbidden"}

    form = await request.form()
    raw = form.get("payload")
    if not isinstance(raw, str):
        return {"status": "ignored"}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"status": "ignored"}
    event = parse_scrobble(payload)
    if event is None:
        return {"status": "ignored"}

    user_filter = await get_user_filter()
    if user_filter and event.user not in user_filter:
        return {"status": "filtered"}

    server = await store.get_setting(constants.PLEX_SERVER_URI)
    token = await store.get_setting(constants.PLEX_SERVER_TOKEN)
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if not (server and token and client_id):
        return {"status": "unlinked"}
    # Always return 200 so Plex doesn't retry on a one-off Sonarr/Plex error.
    try:
        tvdb_id = await resolve_tvdb_id(server, token, client_id, event.grandparent_rating_key)
        if tvdb_id is None:
            return {"status": "no-tvdb"}
        await process_watch(tvdb_id, event.season, event.episode)
    except Exception:
        logger.exception("error processing webhook for %s", event.grandparent_rating_key)
        return {"status": "error"}
    return {"status": "ok"}
