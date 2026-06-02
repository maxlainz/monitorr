import httpx
import respx

from monitorr.plex import client as plex

PLEX = "http://plex:32400"


@respx.mock
async def test_get_watch_history_parses_and_filters() -> None:
    """Reads episode plays from /status/sessions/history/all; ignores non-episode entries and
    other shows (in case the server doesn't honour the metadataItemID filter)."""
    respx.get(f"{PLEX}/status/sessions/history/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "type": "episode",
                            "grandparentRatingKey": "100",
                            "parentIndex": 3,
                            "index": 2,
                            "viewedAt": 1710000200,
                        },
                        {
                            "type": "episode",
                            "grandparentRatingKey": "100",
                            "parentIndex": 1,
                            "index": 5,
                            "viewedAt": 1700000500,
                        },
                        {
                            "type": "episode",
                            "grandparentRatingKey": "999",  # other show → ignored
                            "parentIndex": 9,
                            "index": 9,
                            "viewedAt": 1710000300,
                        },
                        {"type": "movie", "viewedAt": 1710000400},  # not an episode → ignored
                    ]
                }
            },
        )
    )

    watched = await plex.get_watch_history(PLEX, "tok", "cid", "100")

    assert {(w.season, w.episode) for w in watched} == {(3, 2), (1, 5)}


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
                            "grandparentRatingKey": "100",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1577836800,
                        },  # 2020-01-01
                        {
                            "type": "episode",
                            "grandparentRatingKey": "100",
                            "parentIndex": 2,
                            "index": 4,
                            "viewedAt": 1893456000,
                        },  # 2030-01-01
                    ]
                }
            },
        )
    )

    watched = await plex.get_watch_history(PLEX, "tok", "cid", "100")

    assert len(watched) == 1
    assert (watched[0].season, watched[0].episode) == (2, 4)
    assert watched[0].viewed_at.startswith("2030")  # keeps the most recent play
