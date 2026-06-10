import httpx
import respx

from monitorr.plex import client as plex

PLEX = "http://plex:32400"


@respx.mock
async def test_get_watch_history_groups_by_title() -> None:
    """Sweeps the global play history and groups episode plays by normalized grandparentTitle;
    non-episode rows are ignored. The query carries NO metadataItemID (re-add-proof)."""
    route = respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentTitle": "Euphoria",
                            "parentIndex": 3,
                            "index": 2,
                            "viewedAt": 1710000200,
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "Euphoria",
                            "parentIndex": 1,
                            "index": 5,
                            "viewedAt": 1700000500,
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "Game of Thrones",
                            "parentIndex": 1,
                            "index": 4,
                            "viewedAt": 1710000300,
                        },
                        {"type": "movie", "viewedAt": 1710000400},  # not an episode → ignored
                    ]
                }
            },
        )
    )

    by_show, newest_seen = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

    assert "metadataItemID" not in route.calls.last.request.url.params  # not scoped per show
    assert {(w.season, w.episode) for w in by_show["euphoria"]} == {(3, 2), (1, 5)}
    assert {(w.season, w.episode) for w in by_show["game of thrones"]} == {(1, 4)}
    assert newest_seen is not None and newest_seen.startswith("2024-03")  # newest episode row


@respx.mock
async def test_get_watch_history_recovers_readded_show_by_title() -> None:
    """Repro of the re-add bug: after a remove/re-add Plex gives the show a new ratingKey and the
    `metadataItemID=<new key>` query returns nothing, but the global history still carries the plays
    under the same grandparentTitle. Correlating by title recovers them — independent of any
    ratingKey — so the back-catalog max (S03) is found, not just what's still on disk."""
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentTitle": "Euphoria",
                            "parentIndex": 3,
                            "index": 3,
                            "viewedAt": 1714000000,
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "Euphoria",
                            "parentIndex": 3,
                            "index": 1,
                            "viewedAt": 1713000000,
                        },
                    ]
                }
            },
        )
    )

    by_show, _ = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

    watched = by_show[plex.normalize_title("Euphoria")]
    assert max((w.season, w.episode) for w in watched) == (3, 3)


@respx.mock
async def test_get_watch_history_keeps_most_recent_view() -> None:
    """The same episode played twice keeps the latest viewed_at."""
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentTitle": "Show",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1577836800,
                        },  # 2020-01-01
                        {
                            "type": "episode",
                            "grandparentTitle": "Show",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1893456000,
                        },  # 2030-01-01
                    ]
                }
            },
        )
    )

    by_show, _ = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

    watched = by_show["show"]
    assert len(watched) == 1
    assert (watched[0].season, watched[0].episode) == (2, 4)
    assert watched[0].viewed_at.startswith("2030")  # keeps the most recent play


@respx.mock
async def test_history_filters_by_account_before_dedup() -> None:
    """With account_ids, only those users' plays count — filtered BEFORE the per-episode dedup, so
    a newer play by a filtered-out user can't shadow the allowed user's older one. newest_seen is
    still the newest scanned row (pre-filter), so the incremental watermark always advances."""
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentTitle": "Show",
                            "parentIndex": 5,
                            "index": 1,
                            "viewedAt": 1893456000,  # 2030: filtered-out user, furthest play
                            "accountID": 1,
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "Show",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1893455000,  # same episode as below, newer, filtered out
                            "accountID": 1,
                        },
                        {
                            "type": "episode",
                            "grandparentTitle": "Show",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1577836800,  # 2020: the allowed user's play
                            "accountID": 7,
                        },
                    ]
                }
            },
        )
    )

    by_show, newest_seen = await plex.get_watch_history_by_show(
        PLEX, "tok", "cid", account_ids={7}
    )

    watched = by_show["show"]
    assert {(w.season, w.episode) for w in watched} == {(2, 4)}  # S05E01 (account 1) excluded
    assert watched[0].viewed_at.startswith("2020")  # account 1's newer play didn't shadow it
    assert newest_seen is not None and newest_seen.startswith("2030")  # pre-filter watermark


@respx.mock
async def test_get_accounts_maps_normalized_names_to_ids() -> None:
    respx.get(f"{PLEX}/accounts").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Account": [
                        {"id": 1, "name": "Alice"},
                        {"id": 7, "name": "Bob"},
                        {"id": 9, "name": ""},  # unnamed (managed/hidden) → skipped
                    ]
                }
            },
        )
    )

    accounts = await plex.get_accounts(PLEX, "tok", "cid")

    assert accounts == {"alice": 1, "bob": 7}
