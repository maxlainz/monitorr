import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from monitorr import constants, store, sync
from monitorr.engine.policy import Policy, set_dry_run, set_global_policy

PLEX = "http://plex:32400"
SONARR = "http://sonarr:8989/api/v3"
TVDB = 999


def _epoch_days_ago(days: float) -> int:
    """Recent Plex epoch timestamps: plays must look fresh or the window's re-arm gate
    (inactivity past dormant/unwatched grace) would legitimately suppress the GET arm."""
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp())

EPISODES = [
    {
        "id": 101,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "Pilot",
        "hasFile": True,
        "episodeFileId": 201,
    },
    {
        "id": 102,
        "seasonNumber": 1,
        "episodeNumber": 2,
        "title": "E2",
        "hasFile": True,
        "episodeFileId": 202,
    },
    {
        "id": 103,
        "seasonNumber": 1,
        "episodeNumber": 3,
        "title": "E3",
        "hasFile": True,
        "episodeFileId": 203,
    },
]

ALL_LEAVES = [
    {"parentIndex": 1, "index": 1, "viewCount": 1, "lastViewedAt": 1700000000},
    {"parentIndex": 1, "index": 2, "viewCount": 1, "lastViewedAt": 1700001000},
    {"parentIndex": 1, "index": 3, "viewCount": 0},
]


async def _configure_links() -> None:
    await store.set_setting(constants.PLEX_SERVER_URI, PLEX)
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[]))


def _mock_plex(tvdb_id: int) -> respx.Route:
    """Registers sections + all + allLeaves; returns the allLeaves route for asserts."""
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "X", "Guid": [{"id": f"tvdb://{tvdb_id}"}]}
                    ]
                }
            },
        )
    )
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": []}})
    )
    return respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )


def _mock_sonarr() -> dict[str, respx.Route]:
    return {
        "series": respx.get("http://sonarr:8989/api/v3/series").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
        ),
        "episodes": respx.get("http://sonarr:8989/api/v3/episode").mock(
            return_value=httpx.Response(200, json=EPISODES)
        ),
        "monitor": respx.put(f"{SONARR}/episode/monitor").mock(
            return_value=httpx.Response(200, json=[])
        ),
        "command": respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={})),
        "delete": respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
            return_value=httpx.Response(200)
        ),
        "queue": respx.get(f"{SONARR}/queue").mock(
            return_value=httpx.Response(200, json={"records": []})
        ),
    }


@respx.mock
async def test_sync_seeds_watches_and_applies_window() -> None:
    await _configure_links()
    await set_dry_run(False)
    _mock_plex(TVDB)
    routes = _mock_sonarr()

    summary = await sync.run_sync()

    # 999 watched → not normalized; all episodes on disk → nothing to re-search
    assert summary == {"shows": 1, "matched": 1, "normalized": 0, "searched": 0}
    watches = await store.get_watches(TVDB)
    assert {(w.season, w.episode) for w in watches} == {(1, 1), (1, 2)}
    # Anchor = last watched (1,2): monitors ahead (E3) and deletes behind (E1).
    assert routes["monitor"].called
    assert routes["delete"].call_count == 1
    last = await sync.get_last_sync()
    assert last is not None and last["matched"] == 1


@respx.mock
async def test_sync_skips_shows_not_in_sonarr() -> None:
    await _configure_links()
    leaves = _mock_plex(888)  # tvdb not present in Sonarr
    _mock_sonarr()

    summary = await sync.run_sync()

    # 999 managed and unwatched
    assert summary == {"shows": 1, "matched": 0, "normalized": 1, "searched": 0}
    assert not leaves.called  # episodes of unmanaged shows are not requested


@respx.mock
async def test_sync_continues_when_one_series_errors() -> None:
    """A show error (allLeaves 500) doesn't abort the sync: the other is processed anyway."""
    await _configure_links()
    await set_dry_run(False)

    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "OK", "Guid": [{"id": f"tvdb://{TVDB}"}]},
                        {"ratingKey": "200", "title": "Boom", "Guid": [{"id": "tvdb://888"}]},
                    ]
                }
            },
        )
    )
    respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )
    respx.get(f"{PLEX}/library/metadata/200/allLeaves").mock(return_value=httpx.Response(500))
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": []}})
    )
    # Both shows managed; 999 goes first so find_series_by_tvdb returns the correct one.
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "OK", "tvdbId": TVDB},
                {"id": 2, "title": "Boom", "tvdbId": 888},
            ],
        )
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=EPISODES)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))

    summary = await sync.run_sync()

    # the failing one doesn't count as matched; having no viewing, it's normalized to pilot.
    assert summary == {"shows": 2, "matched": 1, "normalized": 1, "searched": 0}
    assert {(w.season, w.episode) for w in await store.get_watches(TVDB)} == {(1, 1), (1, 2)}
    last = await sync.get_last_sync()  # updated despite one show's error
    assert last is not None and last["matched"] == 1


@respx.mock
async def test_sync_anchors_on_history_when_files_deleted() -> None:
    """Repro: watched S3, then its files were deleted (and/or the show was removed and re-added,
    changing its ratingKey). Plex's allLeaves (current library state) only returns the episodes
    still on disk (S1E1,E2,E5), but the global play history persists S3 and is correlated by
    grandparentTitle (not the volatile ratingKey). The sync must anchor on the real last watched
    (S3) and search ahead of S3 — never search S1, which would re-download watched episodes."""
    await _configure_links()  # always_have=[], dry-run ON by default
    await set_dry_run(False)
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "X", "Guid": [{"id": f"tvdb://{TVDB}"}]}
                    ]
                }
            },
        )
    )
    # allLeaves: only the on-disk episodes report watched (S1E1, E2, E5).
    respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "parentIndex": 1,
                            "index": 1,
                            "viewCount": 1,
                            "lastViewedAt": _epoch_days_ago(30),
                        },
                        {
                            "parentIndex": 1,
                            "index": 2,
                            "viewCount": 1,
                            "lastViewedAt": _epoch_days_ago(29),
                        },
                        {
                            "parentIndex": 1,
                            "index": 5,
                            "viewCount": 1,
                            "lastViewedAt": _epoch_days_ago(28),
                        },
                    ]
                }
            },
        )
    )
    # Global play history: S3 watched (files since deleted, no longer in allLeaves). Correlated by
    # grandparentTitle ("X"), so it is found regardless of the show's current ratingKey.
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentTitle": "X",
                            "parentIndex": 3,
                            "index": 2,
                            "viewedAt": _epoch_days_ago(1),
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "X",
                            "parentIndex": 3,
                            "index": 1,
                            "viewedAt": _epoch_days_ago(1.1),
                        },
                    ]
                }
            },
        )
    )
    # Sonarr has S1E1-E8 + S3E1-E3; S1E1,E2,E5 re-downloaded (files), the rest without file.
    episodes = [
        {
            "id": 100 + n,
            "seasonNumber": 1,
            "episodeNumber": n,
            "hasFile": n in (1, 2, 5),
            "episodeFileId": (200 + n) if n in (1, 2, 5) else 0,
            "monitored": False,
        }
        for n in range(1, 9)
    ] + [
        {"id": 300 + n, "seasonNumber": 3, "episodeNumber": n, "hasFile": False, "monitored": False}
        for n in range(1, 4)
    ]
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=episodes)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))

    summary = await sync.run_sync()

    assert summary["matched"] == 1
    # Records the union: S1 (still on disk) + S3 (from history).
    assert {(w.season, w.episode) for w in await store.get_watches(TVDB)} == {
        (1, 1),
        (1, 2),
        (1, 5),
        (3, 1),
        (3, 2),
    }
    # Anchor = S3E2 → GET-ahead is S3E3 (id 303); the search targets it, never any S1 episode.
    searched = {
        eid for c in command.calls for eid in json.loads(c.request.content).get("episodeIds", [])
    }
    assert searched == {303}


def _mock_empty_plex() -> None:
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": []}})
    )


@respx.mock
async def test_sync_auto_normalizes_unwatched_managed_series() -> None:
    """A managed show without viewing is reduced to pilot in the sync (preview in dry-run)."""
    await _configure_links()  # always_have=[], dry-run ON by default
    _mock_empty_plex()
    routes = _mock_sonarr()  # show 999 managed with E1, E2, E3 on disk

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 1, "searched": 0}
    # Keeps the pilot (S01E01); the rest downloaded is left as pending deletion.
    pending = await store.list_deletions(dry_run=True)
    assert {(d.season, d.episode) for d in pending} == {(1, 2), (1, 3)}
    assert not routes["delete"].called  # dry-run: doesn't write to Sonarr


@respx.mock
async def test_sync_auto_normalize_disabled_by_policy() -> None:
    """With auto_normalize=False, a show without viewing is not touched."""
    await _configure_links()
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False))
    _mock_empty_plex()
    _mock_sonarr()

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 0}
    assert await store.list_deletions(dry_run=True) == []


# monitored + aired + no file = Sonarr's "Missing"; one of them is mid-download (in the queue).
MISSING_EPISODES = [
    {
        "id": 301,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2020-01-01T00:00:00Z",
    },  # missing → re-search
    {
        "id": 302,
        "seasonNumber": 1,
        "episodeNumber": 2,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2020-01-08T00:00:00Z",
    },  # in the queue → skip
    {
        "id": 303,
        "seasonNumber": 1,
        "episodeNumber": 3,
        "monitored": True,
        "hasFile": True,
        "episodeFileId": 403,
        "airDateUtc": "2020-01-15T00:00:00Z",
    },  # on disk → skip
    {
        "id": 304,
        "seasonNumber": 1,
        "episodeNumber": 4,
        "monitored": False,
        "hasFile": False,
        "airDateUtc": "2020-01-22T00:00:00Z",
    },  # unmonitored → skip
    {
        "id": 305,
        "seasonNumber": 1,
        "episodeNumber": 5,
        "monitored": True,
        "hasFile": False,
        "airDateUtc": "2999-01-01T00:00:00Z",
    },  # not aired yet → skip
]


@respx.mock
async def test_sync_researches_missing_excluding_queue() -> None:
    """Each sync re-searches the Missing episodes (monitored, aired, no file) except the ones
    already downloading. auto_normalize off + no viewing isolates the re-search pass."""
    await _configure_links()
    await set_global_policy(Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False))
    await set_dry_run(False)
    _mock_empty_plex()
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=MISSING_EPISODES)
    )
    respx.get(f"{SONARR}/queue").mock(
        return_value=httpx.Response(200, json={"records": [{"id": 1, "episodeId": 302}]})
    )
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 1}
    assert command.call_count == 1
    body = json.loads(command.calls.last.request.content)
    assert body == {"name": "EpisodeSearch", "episodeIds": [301]}


@respx.mock
async def test_sync_skips_missing_search_when_search_on_get_disabled() -> None:
    """search_on_get gates the re-search too: with it off, no EpisodeSearch is issued."""
    await _configure_links()
    await set_global_policy(
        Policy(get_count=1, keep_count=1, always_have=[], auto_normalize=False, search_on_get=False)
    )
    await set_dry_run(False)
    _mock_empty_plex()
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=MISSING_EPISODES)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))

    summary = await sync.run_sync()

    assert summary == {"shows": 0, "matched": 0, "normalized": 0, "searched": 0}
    assert not command.called


# --- incremental sync (watermark) ---


def _mock_two_shows(history: list[httpx.Response]) -> tuple[respx.Route, respx.Route]:
    """Two managed shows (Alpha=999, Beta=777). `history` feeds successive sweeps. Returns the two
    allLeaves routes so a test can assert which shows were (re-)scanned."""
    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "Alpha", "Guid": [{"id": "tvdb://999"}]},
                        {"ratingKey": "200", "title": "Beta", "Guid": [{"id": "tvdb://777"}]},
                    ]
                }
            },
        )
    )
    leaves_a = respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )
    leaves_b = respx.get(f"{PLEX}/library/metadata/200/allLeaves").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {"Metadata": ALL_LEAVES}})
    )
    respx.get(f"{PLEX}/status/sessions/history/all").mock(side_effect=history)
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "Alpha", "tvdbId": 999},
                {"id": 2, "title": "Beta", "tvdbId": 777},
            ],
        )
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=EPISODES)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))
    return leaves_a, leaves_b


def _empty_history() -> httpx.Response:
    return httpx.Response(200, json={"MediaContainer": {"Metadata": []}})


def _history_play(title: str, season: int, episode: int, viewed_at: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "MediaContainer": {
                "Metadata": [
                    {
                        "type": "episode",
                        "grandparentTitle": title,
                        "parentIndex": season,
                        "index": episode,
                        "viewedAt": viewed_at,
                    }
                ]
            }
        },
    )


@respx.mock
async def test_sync_first_run_is_full_then_incremental_skips_unchanged() -> None:
    """First sync (no watermark) is FULL and scans both shows. The next, with a single new play in
    the history delta, is INCREMENTAL: only the changed show's allLeaves is fetched — the cost we
    avoid is the per-show scan of every other managed show."""
    await _configure_links()
    await set_dry_run(False)
    # 2100-01-01: comfortably newer than the watermark stamped by the first full.
    leaves_a, leaves_b = _mock_two_shows(
        [_empty_history(), _history_play("Alpha", 1, 3, 4102444800)]
    )

    await sync.run_sync()
    first = await sync.get_last_sync()
    assert first is not None and first["mode"] == "full"
    assert leaves_a.call_count == 1 and leaves_b.call_count == 1  # full scanned both

    await sync.run_sync()
    second = await sync.get_last_sync()
    assert second is not None and second["mode"] == "incremental"
    assert leaves_a.call_count == 2  # Alpha had a new play → re-scanned
    assert leaves_b.call_count == 1  # Beta unchanged → NOT re-scanned


@respx.mock
async def test_force_full_rescans_every_show() -> None:
    """force_full ignores the watermark and scans all shows even with no new history (the manual
    'Sync now' button uses this)."""
    await _configure_links()
    await set_dry_run(False)
    leaves_a, leaves_b = _mock_two_shows([_empty_history(), _empty_history()])

    await sync.run_sync()  # full (no watermark)
    await sync.run_sync(force_full=True)  # full again despite no new plays

    last = await sync.get_last_sync()
    assert last is not None and last["mode"] == "full"
    assert leaves_a.call_count == 2 and leaves_b.call_count == 2


@respx.mock
async def test_incremental_promotes_to_full_when_history_unavailable() -> None:
    """If the history endpoint fails on an incremental cycle, the delta can't be trusted → the cycle
    is promoted to a full reconciliation (every show scanned)."""
    await _configure_links()
    await set_dry_run(False)
    leaves_a, leaves_b = _mock_two_shows([_empty_history(), httpx.Response(500)])

    await sync.run_sync()  # full establishes the watermark
    assert leaves_a.call_count == 1 and leaves_b.call_count == 1

    await sync.run_sync()  # history 500 → promote to full
    last = await sync.get_last_sync()
    assert last is not None and last["mode"] == "full"
    assert leaves_a.call_count == 2 and leaves_b.call_count == 2


async def test_full_sync_due_tracks_the_rolling_floor() -> None:
    """full_sync_due (the startup check) needs both deps and is due when no full ran or the rolling
    floor elapsed — a comparison against the last full, so it survives downtime past the floor."""
    assert await sync.full_sync_due() is False  # nothing configured

    await store.set_setting(constants.PLEX_SERVER_URI, PLEX)
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.SONARR_URL, "http://sonarr:8989")
    await store.set_setting(constants.SONARR_API_KEY, "key")
    assert await sync.full_sync_due() is True  # ready, never ran a full

    await store.set_setting(constants.LAST_FULL_SYNC, datetime.now(UTC).isoformat())
    assert await sync.full_sync_due() is False  # fresh full

    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    await store.set_setting(constants.LAST_FULL_SYNC, old)
    assert await sync.full_sync_due() is True  # past the 30-day floor


@respx.mock
async def test_incremental_sync_does_not_regress_below_recorded_max() -> None:
    """Incremental repro of the creep: an earlier full sync recorded the furthest watch (S3) but its
    file is now gone and the play predates the watermark, so this cycle's live read (allLeaves on
    disk + the history delta) only sees S1. The anchor floor in apply_window keeps the window on S3
    instead of sliding back and re-searching the already-watched S1 back-catalog."""
    await _configure_links()  # GET=1, KEEP=1
    await set_dry_run(False)
    # Earlier full sync already recorded the furthest watch; make this cycle incremental.
    await store.record_watch(TVDB, 3, 1)
    await store.record_watch(TVDB, 3, 2)
    await store.set_setting(constants.HISTORY_WATERMARK, "2100-01-01T00:00:00+00:00")
    await store.set_setting(constants.LAST_FULL_SYNC, datetime.now(UTC).isoformat())

    respx.get(f"{PLEX}/library/sections").mock(
        return_value=httpx.Response(
            200, json={"MediaContainer": {"Directory": [{"key": "1", "type": "show"}]}}
        )
    )
    respx.get(f"{PLEX}/library/sections/1/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"ratingKey": "100", "title": "X", "Guid": [{"id": f"tvdb://{TVDB}"}]}
                    ]
                }
            },
        )
    )
    # On disk only S1 reports watched; S3's files are gone.
    respx.get(f"{PLEX}/library/metadata/100/allLeaves").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {"parentIndex": 1, "index": 1, "viewCount": 1, "lastViewedAt": 1700000000},
                        {"parentIndex": 1, "index": 2, "viewCount": 1, "lastViewedAt": 1700000100},
                    ]
                }
            },
        )
    )
    # Incremental delta: only a fresh LOW play (re-watch of S1E3) newer than the watermark.
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        side_effect=[_history_play("X", 1, 3, 4102444800)]
    )
    episodes = [
        {
            "id": 100 + n,
            "seasonNumber": 1,
            "episodeNumber": n,
            "hasFile": n in (1, 2, 3),
            "episodeFileId": (200 + n) if n in (1, 2, 3) else 0,
            "monitored": False,
        }
        for n in range(1, 9)
    ] + [
        {"id": 300 + n, "seasonNumber": 3, "episodeNumber": n, "hasFile": False, "monitored": False}
        for n in range(1, 4)
    ]
    respx.get("http://sonarr:8989/api/v3/series").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "X", "tvdbId": TVDB}])
    )
    respx.get("http://sonarr:8989/api/v3/episode").mock(
        return_value=httpx.Response(200, json=episodes)
    )
    respx.put(f"{SONARR}/episode/monitor").mock(return_value=httpx.Response(200, json=[]))
    command = respx.post(f"{SONARR}/command").mock(return_value=httpx.Response(201, json={}))
    respx.delete(url__regex=r"http://sonarr:8989/api/v3/episodefile/\d+").mock(
        return_value=httpx.Response(200)
    )
    respx.get(f"{SONARR}/queue").mock(return_value=httpx.Response(200, json={"records": []}))

    summary = await sync.run_sync()

    assert summary["matched"] == 1
    last = await sync.get_last_sync()
    assert last is not None and last["mode"] == "incremental"
    # Floored to S3E2 → GET-ahead is S3E3 (303); never re-searches the watched S1 back-catalog.
    searched = {
        eid for c in command.calls for eid in json.loads(c.request.content).get("episodeIds", [])
    }
    assert searched == {303}


async def test_migration_clears_last_full_sync_to_force_full() -> None:
    """The upgrade migration drops last_full_sync so the first post-upgrade cycle runs a FULL
    reconciliation, re-anchoring shows the incremental creep left over-monitored."""
    import pathlib
    import tempfile

    import aiosqlite

    from monitorr import db

    path = pathlib.Path(tempfile.mkdtemp(prefix="monitorr-mig-")) / "m.db"
    # Simulate a pre-fix DB (schema v3) with a recorded last full sync.
    async with aiosqlite.connect(path) as conn:
        for version in range(3):  # apply the first three migrations
            await conn.executescript(db.MIGRATIONS[version])
        await conn.execute("PRAGMA user_version = 3;")
        await conn.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)",
            (constants.LAST_FULL_SYNC, "2020-01-01T00:00:00+00:00"),
        )
        await conn.commit()

    await db.init_db(path)  # applies the pending migration #4

    async with aiosqlite.connect(path) as conn:
        cur = await conn.execute("PRAGMA user_version;")
        row = await cur.fetchone()
        assert row is not None and row[0] == len(db.MIGRATIONS)
        cur = await conn.execute(
            "SELECT value FROM setting WHERE key = ?", (constants.LAST_FULL_SYNC,)
        )
        assert await cur.fetchone() is None
