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

    by_show = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

    assert "metadataItemID" not in route.calls.last.request.url.params  # not scoped per show
    assert {(w.season, w.episode) for w in by_show["euphoria"]} == {(3, 2), (1, 5)}
    assert {(w.season, w.episode) for w in by_show["game of thrones"]} == {(1, 4)}


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

    by_show = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

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

    by_show = await plex.get_watch_history_by_show(PLEX, "tok", "cid")

    watched = by_show["show"]
    assert len(watched) == 1
    assert (watched[0].season, watched[0].episode) == (2, 4)
    assert watched[0].viewed_at.startswith("2030")  # keeps the most recent play
