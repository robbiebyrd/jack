import pytest

from jack.support.attempt_all import attempt_all


def test_runs_every_action_in_order():
    calls = []
    attempt_all([lambda: calls.append(1), lambda: calls.append(2), lambda: calls.append(3)])
    assert calls == [1, 2, 3]


def test_runs_later_actions_when_an_earlier_one_raises():
    calls = []

    def fails():
        calls.append("fail")
        raise OSError("boom")

    with pytest.raises(OSError, match="boom"):
        attempt_all([fails, lambda: calls.append("after")])
    assert calls == ["fail", "after"]


def test_reraises_the_first_failure():
    def first_failure():
        raise OSError("first")

    def second_failure():
        raise ValueError("second")

    with pytest.raises(OSError, match="first"):
        attempt_all([first_failure, second_failure])


def test_does_nothing_for_no_actions():
    attempt_all([])
