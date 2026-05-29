"""Política de la ventana y resolución global + override. Ver .claude/behavior.md."""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from monitorr import constants, store

Unit = Literal["episodes", "seasons"]


class Policy(BaseModel):
    """Parte de la configuración que se puede sobrescribir por serie."""

    get_count: int = 1
    get_unit: Unit = "episodes"
    keep_count: int = 1
    keep_unit: Unit = "episodes"
    always_have: list[str] = Field(default_factory=lambda: ["S01E01"])
    grace_watched_days: int | None = 7
    grace_unwatched_days: int | None = 365
    dormant_days: int | None = None
    search_on_get: bool = True


async def get_global_policy() -> Policy:
    raw = await store.get_setting(constants.POLICY)
    if raw is None:
        return Policy()
    return Policy.model_validate_json(raw)


async def set_global_policy(policy: Policy) -> None:
    await store.set_setting(constants.POLICY, policy.model_dump_json())


async def effective_policy(tvdb_id: int) -> tuple[Policy, bool]:
    """Devuelve (política efectiva, habilitada). El override sustituye campos del global."""
    base = await get_global_policy()
    override = await store.get_override(tvdb_id)
    if override is None:
        return base, True
    if override.policy_json:
        partial = json.loads(override.policy_json)
        merged = {**base.model_dump(), **partial}
        return Policy.model_validate(merged), override.enabled
    return base, override.enabled


async def get_dry_run() -> bool:
    raw = await store.get_setting(constants.DRY_RUN)
    return True if raw is None else raw == "1"


async def set_dry_run(value: bool) -> None:
    await store.set_setting(constants.DRY_RUN, "1" if value else "0")


async def get_watched_threshold() -> float:
    raw = await store.get_setting(constants.WATCHED_THRESHOLD)
    return 0.9 if raw is None else float(raw)


async def get_user_filter() -> list[str]:
    raw = await store.get_setting(constants.USER_FILTER)
    if not raw:
        return []
    parsed = json.loads(raw)
    return [str(u) for u in parsed]


_PATTERN = re.compile(r"^S(?P<season>\d+|\*)(?:E(?P<episode>\d+|\*))?$", re.IGNORECASE)


def matches_always_have(patterns: list[str], season: int, episode: int) -> bool:
    """Soporta `S01E01`, `S*E01`, `S01` (temporada entera) y `S*` (toda la serie)."""
    for pattern in patterns:
        match = _PATTERN.match(pattern.strip())
        if match is None:
            continue
        season_token = match.group("season")
        if season_token != "*" and int(season_token) != season:
            continue
        episode_token = match.group("episode")
        if episode_token is None or episode_token == "*" or int(episode_token) == episode:
            return True
    return False
