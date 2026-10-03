"""Runs a batch of independent actions without letting one failure skip the rest."""

from collections.abc import Callable, Iterable


def attempt_all(actions: Iterable[Callable[[], None]]) -> None:
    """Run every action, even if an earlier one raises, then re-raise the first failure."""
    failures = []
    for action in actions:
        try:
            action()
        except Exception as failure:
            failures.append(failure)
    if failures:
        raise failures[0]
