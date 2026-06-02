import pytest

from monitorr import constants, store
from monitorr.plex import poller
from monitorr.plex.client import PlexSession

TVDB = 999


def _session(
    session_key: str, season: int, episode: int, progress: float, *, rating_key: str = "rk"
) -> PlexSession:
    return PlexSession(
        grandparent_title="Show",
        grandparent_rating_key=rating_key,
        season=season,
        episode=episode,
        view_offset=int(progress * 100),
        duration=100,
        session_key=session_key,
        user="alice",
    )


async def _configure() -> None:
    await store.set_setting(constants.PLEX_SERVER_URI, "http://plex:32400")
    await store.set_setting(constants.PLEX_SERVER_TOKEN, "tok")
    await store.set_setting(constants.PLEX_CLIENT_ID, "cid")


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int]]:
    """Captures the calls to process_watch and always resolves the same tvdb."""
    calls: list[tuple[int, int, int]] = []

    async def fake_process_watch(tvdb_id: int, season: int, episode: int) -> None:
        calls.append((tvdb_id, season, episode))

    async def fake_resolve(uri: str, token: str, client_id: str, rating_key: str) -> int:
        return TVDB

    monkeypatch.setattr(poller, "process_watch", fake_process_watch)
    monkeypatch.setattr(poller, "resolve_tvdb_id", fake_resolve)
    return calls


def _serve(monkeypatch: pytest.MonkeyPatch, sessions: list[PlexSession]) -> None:
    async def fake_get_sessions(uri: str, token: str, client_id: str) -> list[PlexSession]:
        return sessions

    monkeypatch.setattr(poller, "get_sessions", fake_get_sessions)


async def test_binge_fires_each_episode_same_session_key(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[int, int, int]]
) -> None:
    await _configure()
    fired: set[poller.WatchKey] = set()
    unresolved: set[str] = set()
    prev: dict[poller.WatchKey, poller._PrevSession] = {}

    # Same sessionKey on auto-play: E1, then E2, then re-poll of E2.
    _serve(monkeypatch, [_session("s1", 1, 1, 0.95)])
    await poller._poll_once(fired, unresolved, prev)
    _serve(monkeypatch, [_session("s1", 1, 2, 0.95)])
    await poller._poll_once(fired, unresolved, prev)
    await poller._poll_once(fired, unresolved, prev)  # E2 again → debounce

    assert captured == [(TVDB, 1, 1), (TVDB, 1, 2)]


async def test_near_complete_session_disappearing_counts_as_watched(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[int, int, int]]
) -> None:
    await _configure()
    fired: set[poller.WatchKey] = set()
    unresolved: set[str] = set()
    prev: dict[poller.WatchKey, poller._PrevSession] = {}

    # 0.86 < threshold 0.9 → doesn't fire live, but ≥ NEAR_COMPLETE.
    _serve(monkeypatch, [_session("s2", 1, 5, 0.86)])
    await poller._poll_once(fired, unresolved, prev)
    assert captured == []

    _serve(monkeypatch, [])  # the session disappears
    await poller._poll_once(fired, unresolved, prev)
    assert captured == [(TVDB, 1, 5)]


async def test_process_watch_anchors_on_furthest_watched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Filling an earlier gap (or re-watching) must not pull the window back: it anchors on the
    maximum recorded watch, not on the episode just played."""
    anchors: list[tuple[int, int, int]] = []

    async def fake_apply_window(tvdb_id: int, season: int, episode: int) -> None:
        anchors.append((tvdb_id, season, episode))

    monkeypatch.setattr(poller, "apply_window", fake_apply_window)
    await store.record_watch(TVDB, 3, 8)  # furthest already watched

    await poller.process_watch(TVDB, 2, 5)  # play an earlier gap

    assert anchors == [(TVDB, 3, 8)]  # anchored on the max, not on (2, 5)


async def test_process_watch_advances_to_new_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advancing to a new highest episode slides the window forward as before."""
    anchors: list[tuple[int, int, int]] = []

    async def fake_apply_window(tvdb_id: int, season: int, episode: int) -> None:
        anchors.append((tvdb_id, season, episode))

    monkeypatch.setattr(poller, "apply_window", fake_apply_window)
    await store.record_watch(TVDB, 1, 1)

    await poller.process_watch(TVDB, 1, 2)

    assert anchors == [(TVDB, 1, 2)]


async def test_low_progress_session_disappearing_is_ignored(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[int, int, int]]
) -> None:
    await _configure()
    fired: set[poller.WatchKey] = set()
    unresolved: set[str] = set()
    prev: dict[poller.WatchKey, poller._PrevSession] = {}

    _serve(monkeypatch, [_session("s3", 1, 7, 0.30)])
    await poller._poll_once(fired, unresolved, prev)
    _serve(monkeypatch, [])
    await poller._poll_once(fired, unresolved, prev)

    assert captured == []
