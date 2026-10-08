"""Shortens text that came from the network before it is logged, so one packet can't fill the journal."""

MAX_LOGGED_CHARS = 200


def clip(value: object) -> str:
    text = str(value)
    return text if len(text) <= MAX_LOGGED_CHARS else text[:MAX_LOGGED_CHARS] + "…"
