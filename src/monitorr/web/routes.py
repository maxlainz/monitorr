import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from monitorr import constants, store, sync
from monitorr.config import get_settings
from monitorr.engine import actions
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
from monitorr.plex.webhook import parse_scrobble
from monitorr.sonarr import client as sonarr

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter()

_PIN_ID = "plex_pin_id"
_PIN_CODE = "plex_pin_code"

# Mantener referencia a las tareas de fondo lanzadas desde rutas para que no las recoja el GC.
_bg_tasks: set[asyncio.Task[dict[str, int]]] = set()


def _forward_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/settings"


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


# --- páginas ---


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
    context = {
        "plex": await _plex_status(),
        "sonarr_url": sonarr_url or "",
        "sonarr_configured": await sonarr.get_config() is not None,
        "policy": await get_global_policy(),
        "dry_run": await get_dry_run(),
        "watched_threshold": await get_watched_threshold(),
        "user_filter": ", ".join(await get_user_filter()),
    }
    return templates.TemplateResponse(request, "settings.html", context)


@router.get("/series", response_class=HTMLResponse)
async def series_page(request: Request) -> HTMLResponse:
    cfg = await sonarr.get_config()
    series_rows: list[dict[str, Any]] = []
    if cfg is not None:
        overrides = {o.tvdb_id: o.enabled for o in await store.list_overrides()}
        for s in await sonarr.list_series(*cfg):
            series_rows.append(
                {"tvdb_id": s.tvdb_id, "title": s.title, "enabled": overrides.get(s.tvdb_id, True)}
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


# --- ajustes: Sonarr ---


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


# --- ajustes: política ---


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
    search_on_get: str | None = Form(None),
    watched_threshold: float = Form(0.9),
    user_filter: str = Form(""),
    dry_run: str | None = Form(None),
) -> RedirectResponse:
    def _opt_int(value: str) -> int | None:
        value = value.strip()
        return int(value) if value else None

    def _split(value: str) -> list[str]:
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]

    policy = Policy(
        get_count=max(0, get_count),
        get_unit="seasons" if get_unit == "seasons" else "episodes",
        keep_count=max(0, keep_count),
        keep_unit="seasons" if keep_unit == "seasons" else "episodes",
        always_have=_split(always_have) or ["S01E01"],
        grace_watched_days=_opt_int(grace_watched_days),
        grace_unwatched_days=_opt_int(grace_unwatched_days),
        dormant_days=_opt_int(dormant_days),
        search_on_get=search_on_get is not None,
    )
    await set_global_policy(policy)
    await set_dry_run(dry_run is not None)
    await store.set_setting(constants.WATCHED_THRESHOLD, str(watched_threshold))
    await store.set_setting(constants.USER_FILTER, json.dumps(_split(user_filter)))
    return RedirectResponse(url="/settings", status_code=303)


# --- vinculación Plex ---


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
            request, "_plex_link.html", {"state": "error", "message": "Reinicia la vinculación."}
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
            {"state": "error", "message": "No se encontraron servidores Plex."},
        )
    if len(servers) == 1 and await _store_server(servers[0]):
        return templates.TemplateResponse(
            request, "_plex_link.html", {"state": "linked", "server_name": servers[0].name}
        )
    return templates.TemplateResponse(
        request, "_plex_link.html", {"state": "choose", "servers": servers}
    )


@router.post("/plex/server")
async def plex_choose_server(client_identifier: str = Form(...)) -> RedirectResponse:
    token = await store.get_setting(constants.PLEX_ACCOUNT_TOKEN)
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if token and client_id:
        for server in await discover_servers(token, client_id):
            if server.client_identifier == client_identifier:
                await _store_server(server)
                break
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/plex/unlink")
async def plex_unlink() -> RedirectResponse:
    for key in (
        constants.PLEX_ACCOUNT_TOKEN,
        constants.PLEX_SERVER_URI,
        constants.PLEX_SERVER_TOKEN,
        constants.PLEX_SERVER_NAME,
    ):
        await store.delete_setting(key)
    return RedirectResponse(url="/settings", status_code=303)


# --- override por serie ---


@router.post("/series/{tvdb_id}/override")
async def series_override(tvdb_id: int, enabled: str | None = Form(None)) -> RedirectResponse:
    await store.set_override(tvdb_id, enabled is not None, None)
    return RedirectResponse(url="/series", status_code=303)


@router.post("/series/{tvdb_id}/normalize")
async def series_normalize(tvdb_id: int) -> RedirectResponse:
    """Fuerza la monitorización a solo-Pilot (opt-in). Respeta dry-run."""
    cfg = await sonarr.get_config()
    if cfg is not None:
        base_url, api_key = cfg
        series = await sonarr.find_series_by_tvdb(base_url, api_key, tvdb_id)
        if series is not None:
            episodes = await sonarr.get_episodes(base_url, api_key, series.id)
            policy, _ = await effective_policy(tvdb_id)
            await actions.normalize_to_pilot(
                base_url,
                api_key,
                tvdb_id,
                series,
                episodes,
                policy.always_have,
                await get_dry_run(),
            )
    return RedirectResponse(url="/series", status_code=303)


# --- sincronización ---


@router.post("/sync")
async def trigger_sync() -> RedirectResponse:
    if not sync.is_running():
        task = asyncio.create_task(sync.run_sync())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    return RedirectResponse(url="/", status_code=303)


# --- webhook opcional ---


@router.post("/webhook/plex/{secret}")
async def plex_webhook(secret: str, request: Request) -> dict[str, str]:
    expected = get_settings().webhook_secret
    if not expected or secret != expected:
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

    server = await store.get_setting(constants.PLEX_SERVER_URI)
    token = await store.get_setting(constants.PLEX_SERVER_TOKEN)
    client_id = await store.get_setting(constants.PLEX_CLIENT_ID)
    if not (server and token and client_id):
        return {"status": "unlinked"}
    # Devolvemos siempre 200 para que Plex no reintente ante un error puntual de Sonarr/Plex.
    try:
        tvdb_id = await resolve_tvdb_id(server, token, client_id, event.grandparent_rating_key)
        if tvdb_id is None:
            return {"status": "no-tvdb"}
        await process_watch(tvdb_id, event.season, event.episode)
    except Exception:
        logger.exception("error procesando webhook de %s", event.grandparent_rating_key)
        return {"status": "error"}
    return {"status": "ok"}
