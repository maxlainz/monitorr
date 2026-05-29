from monitorr.engine.policy import matches_always_have


def test_exact_episode() -> None:
    assert matches_always_have(["S01E01"], 1, 1)
    assert not matches_always_have(["S01E01"], 1, 2)


def test_first_of_every_season() -> None:
    assert matches_always_have(["S*E01"], 3, 1)
    assert not matches_always_have(["S*E01"], 3, 2)


def test_whole_season() -> None:
    assert matches_always_have(["S02"], 2, 5)
    assert not matches_always_have(["S02"], 3, 5)


def test_whole_series() -> None:
    assert matches_always_have(["S*"], 9, 9)


def test_invalid_pattern_ignored() -> None:
    assert not matches_always_have(["nope"], 1, 1)
