"""OSC feedback: Jack's state as OSC messages, and which subscriber needs which of them now.

A subscriber gets the full set when it subscribes or renews and once a second after that,
and in between only the messages whose value changed. See "OSC replies and feedback" in SPEC.md.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from jack.show.control.status import Status
from jack.show.motion.motors import MOTOR_NAMES

Message = tuple[str, list]
Destination = tuple[str, int]

SUBSCRIPTION_S = 60.0
MAX_SUBSCRIBERS = 8
FULL_REFRESH_S = 1.0


def state_messages(status: Status) -> list[Message]:
    """The state messages for a ControlBoard.status() snapshot, in a fixed order.

    `/jack/<motor>` mirrors the fader value: the current value command, 0 at rest or during a pose.
    `/jack/mouth/mode/show` is 1.0 in show mode and 0.0 in live, for a TouchOSC toggle.
    """
    messages: list[Message] = []
    for name in MOTOR_NAMES:
        motor = status.motors[name]
        messages.append((f"/jack/{name}", [float(motor.fader_value)]))
        messages.append((f"/jack/{name}/volts", [float(motor.volts)]))
        messages.append((f"/jack/{name}/max_hold", [int(motor.max_hold_tripped)]))
    messages.append(("/jack/mouth/mode", [status.mouth_mode]))
    messages.append(("/jack/mouth/mode/show", [1.0 if status.mouth_mode == "show" else 0.0]))
    messages.append(("/jack/mumble", [int(status.mumble_connected)]))
    return messages


@dataclass
class _Subscriber:
    expires_at: float
    next_full_at: float
    sent: dict[str, list] = field(default_factory=dict)


class Subscribers:
    """Who gets feedback, until when, and what each was last sent. Safe across threads."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._lock = threading.Lock()
        self._subscribers: dict[Destination, _Subscriber] = {}

    def subscribe(self, destination: Destination) -> bool:
        """Start or renew a lease; False when full and `destination` isn't already subscribed."""
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            if destination not in self._subscribers and len(self._subscribers) >= MAX_SUBSCRIBERS:
                return False
            self._subscribers[destination] = _Subscriber(expires_at=now + SUBSCRIPTION_S, next_full_at=now)
            return True

    def renew_from(self, ip: str) -> None:
        """Extend the lease of every live subscription to `ip`, leaving what it was last sent alone."""
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            for destination, subscriber in self._subscribers.items():
                if destination[0] == ip:
                    subscriber.expires_at = now + SUBSCRIPTION_S

    def unsubscribe(self, destination: Destination) -> None:
        with self._lock:
            self._subscribers.pop(destination, None)

    def due(self, messages: list[Message]) -> list[tuple[Destination, list[Message]]]:
        """For each live subscriber, the messages to send now (full set when due, else changes)."""
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            batches = []
            for destination, subscriber in self._subscribers.items():
                if now >= subscriber.next_full_at:
                    batch = list(messages)
                    subscriber.next_full_at = now + FULL_REFRESH_S
                else:
                    batch = [(address, args) for address, args in messages if subscriber.sent.get(address) != args]
                subscriber.sent.update(dict(batch))
                if batch:
                    batches.append((destination, batch))
            return batches

    def __len__(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def _drop_expired(self, now: float) -> None:
        for destination in [d for d, s in self._subscribers.items() if now >= s.expires_at]:
            del self._subscribers[destination]
