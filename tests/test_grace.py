from datetime import UTC, datetime, timedelta

import httpx
import respx

from monitorr import constants, store
from monitorr.engine.grace import sweep
from monitorr.engine.policy import Policy, set_dry_run, set_global_policy

SONARR = "http://sonarr:8989/api/v3"
TVDB = 555


def _iso(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _ep(season: int, num: int, *, has_file: bool, aired: bool = True) -> dict[str, object]:
    return {
        "id": season * 100 + num,
        "seasonNumber": season,
        "episodeNumber": num,
        "title": f"S{season}E{num}",
        "hasFile": has_file,
        "episodeFileId": (season * 100 + num) if has_file else 0,
        "monitored": True,
        "airDateUtc": _iso(-100) if aired else _iso(30),
    }


# Finished series: S01E01..E05, all aired and on disk.
FINISHED = [_ep(1, n, has_file=True) for n in range(1, 6)]


async def _configure(*, completed_days: int | None, always_have: list[str] | None = None) -> None:
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    # Only the completed grace is in play; the others are disabled to isolate it.
    await set_global_policy(
        Policy(
            grace_watched_days=None,
            grace_unwatched_days=None,
            dormant_days=None,
            grace_completed_days=completed_days,
            always_have=["S01E01"] if always_have is None else always_have,
        )
    )


def _mock(episodes: list[dict[str, object]]) -> dict[str, respx.Route]:
    series_obj = {"id": 1, "title": "X", "tvdbId": TVDB}
    return {
        "series": respx.get("http://sonarr:8989/api/v3/series").mock(
            return_value=httpx.Response(200, json=[series_obj])
        ),
        "episodes": respx.get("http://sonarr:8989/api/v3/episode").mock(
            return_value=httpx.Response(200, json=episodes)
        ),
        "monitor": respx.put(f"{SONARR}/episode/monitor").mock(
            return_value=httpx.Response(200, json=[])
        ),
        "delete": respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
            return_value=httpx.Response(200)
        ),
    }


async def _watch(season: int, num: int, *, days_ago: int) -> None:
    await store.record_watch(TVDB, season, num, watched_at=_iso(-days_ago))


@respx.mock
async def test_completed_purges_when_caught_up_and_inactive() -> None:
    await _configure(completed_days=30)  # dry-run ON by default
    routes = _mock(FINISHED)
    for n in range(1, 6):
        await _watch(1, n, days_ago=40)

    await sweep()

    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2), (1, 3), (1, 4), (1, 5)}
    assert {d.reason for d in pending} == {"completed"}  # pilot protected by Always-Have
    assert not routes["monitor"].called  # dry-run: nothing written to Sonarr
    assert not routes["delete"].called


@respx.mock
async def test_completed_skips_within_grace() -> None:
    await _configure(completed_days=30)
    _mock(FINISHED)
    for n in range(1, 6):
        await _watch(1, n, days_ago=10)  # caught up but only 10 days inactive

    await sweep()

    assert await store.list_deletions(dry_run=True) == []


@respx.mock
async def test_completed_skips_when_not_caught_up() -> None:
    await _configure(completed_days=30)
    _mock(FINISHED)
    for n in range(1, 4):  # watched E1..E3; E4/E5 aired and unseen → not caught up
        await _watch(1, n, days_ago=40)

    await sweep()

    assert await store.list_deletions(dry_run=True) == []


@respx.mock
async def test_completed_purges_on_hiatus_with_future_episode() -> None:
    await _configure(completed_days=30)
    _mock([*FINISHED, _ep(2, 1, has_file=False, aired=False)])  # next season announced, unaired
    for n in range(1, 6):
        await _watch(1, n, days_ago=40)

    await sweep()

    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2), (1, 3), (1, 4), (1, 5)}


@respx.mock
async def test_completed_real_mode_deletes_and_keeps_watch_anchor() -> None:
    await _configure(completed_days=30, always_have=["S01E01"])
    await set_dry_run(False)
    routes = _mock(FINISHED)
    for n in range(1, 6):
        await _watch(1, n, days_ago=40)

    await sweep()

    assert routes["delete"].call_count == 4  # E2..E5 deleted, pilot protected
    done = await store.list_deletions(dry_run=False)
    assert {(d.episode, d.reason) for d in done} == {
        (2, "completed"),
        (3, "completed"),
        (4, "completed"),
        (5, "completed"),
    }
    # Re-arm precondition: the purge keeps episode_watch so sync can re-anchor GET on return.
    watched = {(w.season, w.episode) for w in await store.get_watches(TVDB)}
    assert watched == {(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)}


@respx.mock
async def test_completed_disabled_when_unset() -> None:
    await _configure(completed_days=None)
    _mock(FINISHED)
    for n in range(1, 6):
        await _watch(1, n, days_ago=40)

    await sweep()

    assert await store.list_deletions(dry_run=True) == []
