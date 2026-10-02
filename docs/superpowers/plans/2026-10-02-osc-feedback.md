# OSC Replies and Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jack answers `/jack/ping` and `/jack/status` over OSC and pushes state changes to subscribed controllers.

**Architecture:**
- A pure `osc_feedback.py` turns the ControlBoard's status into OSC state messages, and keeps the subscriber list with lease, cap, per-subscriber change tracking and full refresh. Its clock is injected.
- `show_commands.py` parses the four new routes and answers them through an injected `send`.
- `osc_server.py` becomes an `OscEndpoint`. It binds the UDP socket, serves with the sender's address, and sends replies from the same socket. A daemon feedback thread reads the board every 20 ms and sends whatever is due.
- `main.py` wires it all, plus `JACK_OSC_REPLY_PORT`.

**Tech Stack:** Python 3.13, python-osc 1.10.2 (`Dispatcher.set_default_handler(handler, needs_reply_address=True)` → `handler(client_address, address, *args)`; `pythonosc.osc_message_builder.OscMessageBuilder(address).add_arg(v)`, then `.build().dgram`), pytest.

**Spec:** `SPEC.md` "### OSC replies and feedback" (and "### OSC").

## Global Constraints

- Work on `main`, with one commit per task: `git commit --only -m "<msg>" -m "Co-Authored-By: Claude <noreply@anthropic.com>" -- <paths>`. Never push.
- TDD: write the tests first and capture RED before writing the code. Output must be pristine under `.venv/bin/pytest -q -W error`.
- State messages, in this order:
  - `/jack/<motor>/volts` (float) then `/jack/<motor>/max_hold` (int 0/1), for mouth, hand, pivot, elbow
  - `/jack/mouth/mode` (str)
  - `/jack/mumble` (int 0/1)
- Lease 60 s, at most 8 subscribers, full refresh every 1 s and immediately on subscribe or renew, change check every 20 ms.
- Reply destination: the sender's IP, at `JACK_OSC_REPLY_PORT` if set, otherwise the sender's source port. A subscribe or unsubscribe port argument overrides the port; the IP is always the sender's.
- The talk loop never does network I/O. python-osc is imported only in `motor_test/osc_server.py` (the tests may import it).
- Style: module docstrings, type hints, short functions, comments only for what and why.

## Review Focus

1. **A subscriber that disappears**, such as a laptop leaving: its expiry after 60 s stops the sends, and a send error to it (ICMP unreachable shows up as `OSError` on Linux UDP) is logged, rate-limited, without stopping the others. Tested in Tasks 1 and 3.
2. **A flood of `/jack/subscribe` from many ports** never exceeds 8 subscribers; refusals are logged and rate-limited. Tested in Tasks 1 and 2.
3. **Bad subscribe ports** (0, 70000, `"x"`, `True`) get a 400 and are ignored with a log line. Tested in Task 2.
4. **`/jack/status` and `/jack/ping` over HTTP** are not routes, so they get a 404. HTTP keeps its `/status`. Tested in Task 2.
5. **A packet that arrives before the handler is set up** is ignored, not crashed on. The endpoint binds first and serves later. Tested in Task 3.

---

### Task 1: `osc_feedback.py`, the state messages and subscribers

**Files:** Create `motor_test/osc_feedback.py`, `tests/test_osc_feedback.py`

**Interfaces produced:** `Message = tuple[str, list]`, `Destination = tuple[str, int]`, `SUBSCRIPTION_S = 60.0`, `MAX_SUBSCRIBERS = 8`, `FULL_REFRESH_S = 1.0`, `state_messages(status: Mapping) -> list[Message]`, `Subscribers(clock)` with `.subscribe(destination) -> bool`, `.unsubscribe(destination) -> None`, `.due(messages) -> list[tuple[Destination, list[Message]]]`, `len()`.

- [ ] **Step 1: Tests** — `tests/test_osc_feedback.py`:

```python
from motor_test.osc_feedback import MAX_SUBSCRIBERS, Subscribers, state_messages
from tests.fakes import FakeClock

A = ("10.10.0.22", 21601)
B = ("10.10.0.30", 9001)


def status(hand_volts=0.0, tripped=False, mode="live", mumble=True):
    motors = {name: {"volts": 0.0, "max_hold_tripped": False} for name in ("mouth", "hand", "pivot", "elbow")}
    motors["hand"] = {"volts": hand_volts, "max_hold_tripped": tripped}
    return {"mouth_mode": mode, "mumble_connected": mumble, "motors": motors}


def test_state_messages_in_order_with_osc_types():
    assert state_messages(status(hand_volts=1.5, tripped=True, mode="show", mumble=False)) == [
        ("/jack/mouth/volts", [0.0]), ("/jack/mouth/max_hold", [0]),
        ("/jack/hand/volts", [1.5]), ("/jack/hand/max_hold", [1]),
        ("/jack/pivot/volts", [0.0]), ("/jack/pivot/max_hold", [0]),
        ("/jack/elbow/volts", [0.0]), ("/jack/elbow/max_hold", [0]),
        ("/jack/mouth/mode", ["show"]), ("/jack/mumble", [0]),
    ]


def test_a_new_subscriber_gets_the_full_set_then_only_changes():
    subs = Subscribers(FakeClock())
    assert subs.subscribe(A)
    full = state_messages(status())
    assert subs.due(full) == [(A, full)]
    assert subs.due(full) == []
    assert subs.due(state_messages(status(hand_volts=2.0))) == [(A, [("/jack/hand/volts", [2.0])])]


def test_full_refresh_every_second():
    clock = FakeClock()
    subs = Subscribers(clock)
    subs.subscribe(A)
    full = state_messages(status())
    subs.due(full)
    clock.advance(0.99)
    assert subs.due(full) == []
    clock.advance(0.01)
    assert subs.due(full) == [(A, full)]


def test_renewing_sends_the_full_set_at_once():
    subs = Subscribers(FakeClock())
    subs.subscribe(A)
    full = state_messages(status())
    subs.due(full)
    subs.subscribe(A)
    assert subs.due(full) == [(A, full)]


def test_a_subscription_lapses_after_60_seconds_unless_renewed():
    clock = FakeClock()
    subs = Subscribers(clock)
    subs.subscribe(A)
    subs.subscribe(B)
    clock.advance(30.0)
    subs.subscribe(B)
    clock.advance(30.0)  # A: 60 s since subscribing → gone; B: 30 s since renewing
    due = subs.due(state_messages(status()))
    assert [destination for destination, _ in due] == [B]
    assert len(subs) == 1


def test_at_most_eight_subscribers_but_existing_ones_can_renew():
    subs = Subscribers(FakeClock())
    for port in range(MAX_SUBSCRIBERS):
        assert subs.subscribe(("10.10.0.22", 20000 + port))
    assert not subs.subscribe(("10.10.0.99", 1))
    assert subs.subscribe(("10.10.0.22", 20000))
    assert len(subs) == MAX_SUBSCRIBERS


def test_unsubscribe_stops_feedback_and_ignores_unknown_destinations():
    subs = Subscribers(FakeClock())
    subs.subscribe(A)
    subs.unsubscribe(B)
    subs.unsubscribe(A)
    assert subs.due(state_messages(status())) == []
    assert len(subs) == 0


def test_change_tracking_is_per_subscriber():
    subs = Subscribers(FakeClock())
    subs.subscribe(A)
    full = state_messages(status())
    subs.due(full)
    subs.subscribe(B)
    changed = state_messages(status(hand_volts=2.0))
    assert subs.due(changed) == [(A, [("/jack/hand/volts", [2.0])]), (B, changed)]
```

Run `.venv/bin/pytest tests/test_osc_feedback.py -q` → `ModuleNotFoundError`.

- [ ] **Step 2: Implement** `motor_test/osc_feedback.py`:

```python
"""OSC feedback: Jack's state as OSC messages, and which subscriber needs which of them now.

A subscriber gets the full set when it subscribes or renews and once a second after that,
and in between only the messages whose value changed. See "OSC replies and feedback" in SPEC.md.
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from motor_test.motors import MOTOR_NAMES

Message = tuple[str, list]
Destination = tuple[str, int]

SUBSCRIPTION_S = 60.0
MAX_SUBSCRIBERS = 8
FULL_REFRESH_S = 1.0


def state_messages(status: Mapping) -> list[Message]:
    """The state messages for a ControlBoard.status() snapshot, in a fixed order."""
    messages: list[Message] = []
    for name in MOTOR_NAMES:
        motor = status["motors"][name]
        messages.append((f"/jack/{name}/volts", [float(motor["volts"])]))
        messages.append((f"/jack/{name}/max_hold", [int(bool(motor["max_hold_tripped"]))]))
    messages.append(("/jack/mouth/mode", [status["mouth_mode"]]))
    messages.append(("/jack/mumble", [int(bool(status["mumble_connected"]))]))
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
```

(Check: in `test_full_refresh_every_second`, 0.99 + 0.01 must reach `next_full_at` = 1.0. If float addition lands just below it, use `clock.advance(0.5)` twice in the test instead; 0.5 + 0.5 is exact.)

- [ ] **Step 3:** Run the test file, then the full suite with `-W error`. Commit `motor_test/osc_feedback.py tests/test_osc_feedback.py` with "Add OSC feedback state messages and subscribers with lease, cap and change tracking".

---

### Task 2: Parse and answer ping, status, subscribe and unsubscribe

**Files:** Modify `motor_test/show_commands.py` and `tests/test_show_commands.py`.

**Consumes:** `state_messages`, `Subscribers`, `MAX_SUBSCRIBERS`, `Destination`, `Message` (Task 1).

**Produces:**
- Commands: `Ping()`, `StatusRequest()`, `Subscribe(port: int | None)`, `Unsubscribe(port: int | None)`.
- `OscContext(profiles, board, subscribers, send: Callable[[Destination, list[Message]], None], mumble_connected: Callable[[], bool], reply_port: int | None, log: RateLimitedLog)`, a frozen dataclass.
- New signature: `handle_osc(address, args, sender: Destination, context: OscContext) -> None`.

- [ ] **Step 1: Tests.** Change the existing `handle_osc` tests to the new signature. Add a helper:

```python
def osc_context(board, lines, clock, reply_port=None):
    sent = []
    context = OscContext(
        profiles=PROFILES, board=board, subscribers=Subscribers(clock), send=lambda dest, msgs: sent.append((dest, msgs)),
        mumble_connected=lambda: True, reply_port=reply_port, log=RateLimitedLog(lines.append, 60.0, clock),
    )
    return context, sent

SENDER = ("10.10.0.22", 9000)
```

Every existing `handle_osc(addr, args, PROFILES, b, log)` becomes `handle_osc(addr, args, SENDER, context)`, with the same expected log lines. Add these tests:

```python
def test_ping_replies_pong_to_the_senders_source_port():
    lines, clock = [], FakeClock()
    context, sent = osc_context(board(), lines, clock)
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(SENDER, [("/jack/pong", [])])]


def test_replies_use_the_configured_reply_port_on_the_senders_ip():
    lines, clock = [], FakeClock()
    context, sent = osc_context(board(), lines, clock, reply_port=21601)
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(("10.10.0.22", 21601), [("/jack/pong", [])])]


def test_status_replies_with_the_full_state():
    lines, clock = [], FakeClock()
    b = board()
    context, sent = osc_context(b, lines, clock)
    handle_osc("/jack/status", [], SENDER, context)
    assert sent == [(SENDER, state_messages(b.status(True)))]


def test_subscribe_uses_the_given_port_or_the_reply_port():
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock, reply_port=21601)
    handle_osc("/jack/subscribe", [], SENDER, context)
    handle_osc("/jack/subscribe", [7000], SENDER, context)
    assert {dest for dest, _ in context.subscribers.due(state_messages(board().status(True)))} == {
        ("10.10.0.22", 21601), ("10.10.0.22", 7000)}
    handle_osc("/jack/unsubscribe", [7000], SENDER, context)
    assert len(context.subscribers) == 1


def test_a_refused_subscription_is_logged_once_per_minute():
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock)
    for port in range(MAX_SUBSCRIBERS + 3):
        handle_osc("/jack/subscribe", [20000 + port], SENDER, context)
    assert len(context.subscribers) == MAX_SUBSCRIBERS
    assert len([line for line in lines if "subscription" in line]) == 1


@pytest.mark.parametrize("args", [[0], [70000], ["x"], [True], [1, 2]])
def test_bad_subscribe_ports_are_ignored_and_logged(args):
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock)
    handle_osc("/jack/subscribe", args, SENDER, context)
    assert len(context.subscribers) == 0
    assert lines and "Ignored OSC /jack/subscribe" in lines[0]


@pytest.mark.parametrize("address", ["/jack/ping", "/jack/status"])
def test_extra_arguments_to_ping_and_status_are_rejected(address):
    with pytest.raises(CommandError) as error:
        osc_command(address, [1], PROFILES)
    assert error.value.status == 400


@pytest.mark.parametrize("path", ["/ping", "/status", "/subscribe", "/unsubscribe"])
def test_osc_only_routes_are_not_http_commands(path):
    with pytest.raises(CommandError) as error:
        http_command(path, {}, PROFILES)
    assert error.value.status == 404


def test_a_failed_reply_is_logged_not_raised():
    lines, clock = [], FakeClock()
    def failing_send(dest, msgs):
        raise OSError("network unreachable")
    context, _ = osc_context(board(), lines, clock)
    context = dataclasses.replace(context, send=failing_send)
    handle_osc("/jack/ping", [], SENDER, context)
    assert lines and "reply" in lines[0].lower()
```

Add the imports the tests need (`dataclasses`, `Subscribers`, `state_messages`, `MAX_SUBSCRIBERS`, `OscContext`). Run the tests and see them fail.

- [ ] **Step 2: Implement** in `show_commands.py`.
  1. **Command dataclasses:** add `Ping`, `StatusRequest`, `Subscribe` and `Unsubscribe` as frozen dataclasses. Define `OscQuery = Ping | StatusRequest | Subscribe | Unsubscribe`.
  2. **Parse in `osc_command` only, before `_build`:**
     - Let `route = address[len(OSC_PREFIX):]`.
     - If `route` is one of `/ping`, `/status`, `/subscribe`, `/unsubscribe`, return the query command.
     - `ping` and `status` take no arguments. Any argument is a 400 ("too many arguments for …").
     - `subscribe` and `unsubscribe` take at most one argument, an `int` port that isn't a `bool`, from 1 to 65535. Anything else is a 400 ("port must be an integer 1-65535, got …").
     - `http_command` is unchanged, so these paths still 404 through `_build`.
  3. **`OscContext`** as specified.
  4. **`handle_osc(address, args, sender, context)`:**
     - Parse the command.
     - If it's a query, call `_answer(command, sender, context)`. Otherwise call `apply(command, context.board)`.
     - On `CommandError`, log under `f"ignored {error.status}"` as today.
     - On `OSError` from `send`, log `context.log("reply failed", f"OSC reply to {dest} failed: {error}")`.
     - Keep the clamp logging.
  5. **`_answer`:**
     - `reply_to = (sender[0], context.reply_port or sender[1])`.
     - `Ping`: `send(reply_to, [("/jack/pong", [])])`.
     - `StatusRequest`: `send(reply_to, state_messages(context.board.status(context.mumble_connected())))`.
     - `Subscribe(port)`: the destination is `(sender[0], port or reply_to[1])`. If `subscribers.subscribe(dest)` returns False, log `context.log("subscribers full", f"Refused OSC subscription from {dest}: already {MAX_SUBSCRIBERS} subscribers")`.
     - `Unsubscribe(port)`: `subscribers.unsubscribe((sender[0], port or reply_to[1]))`.
  6. **Module docstring:** update the route list.

- [ ] **Step 3:** Run `tests/test_show_commands.py`, then the full suite with `-W error`.

  `main.py` still calls the old `handle_osc` signature. Its tests don't call `main()`, so the suite still passes; Task 4 rewires the call. Check that `.venv/bin/python -c "import main"` still works.

  Commit `motor_test/show_commands.py tests/test_show_commands.py` with "Answer OSC ping and status, and take subscriptions".

---

### Task 3: `OscEndpoint` with replies and the feedback thread

**Files:** Modify `motor_test/osc_server.py` and `tests/test_osc_server.py`.

**Consumes:** `Subscribers`, `state_messages`, `Destination`, `Message` (Task 1).

**Produces:**
- `OscEndpoint(host, port)`: binds at construction and doesn't serve yet. It has:
  - `.server_address`
  - `.serve(handle: Callable[[str, list, Destination], None])`, which starts the daemon "osc" thread
  - `.send(destination, messages)`, which sends each message from the bound socket
  - `.close()`, which calls shutdown and server_close
- `start_feedback(endpoint, subscribers, status: Callable[[], Mapping], log: Callable[[str, str], None], interval_s=0.02) -> threading.Event`. It starts the daemon "osc-feedback" thread; setting the returned event stops it.
- `start_osc_server` is removed, replaced by `OscEndpoint`.

- [ ] **Step 1: Tests.** Rewrite `tests/test_osc_server.py` for `OscEndpoint`. Keep the existing four behaviours: delivery with address and args, a non-OSC datagram ignored, a handler exception not stopping the server, and handler return values not sent back. The handler now also gets `sender` (a `(ip, port)` tuple); assert it equals the client socket's address. Add:

```python
def recv_messages(sock, count, timeout=2.0):
    """Read `count` OSC datagrams from a UDP socket as (address, params)."""
    sock.settimeout(timeout)
    return [(m.address, m.params) for m in (OscMessage(sock.recv(65535)) for _ in range(count))]


def test_send_delivers_messages_from_the_osc_port():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    try:
        endpoint.send(client.getsockname(), [("/jack/pong", []), ("/jack/hand/volts", [1.5])])
        client.settimeout(2.0)
        data, source = client.recvfrom(65535)
        assert source[1] == endpoint.server_address[1]
        assert OscMessage(data).address == "/jack/pong"
        assert recv_messages(client, 1) == [("/jack/hand/volts", [1.5])]
    finally:
        client.close()
        endpoint.close()


def test_packets_before_serve_are_ignored():
    endpoint = OscEndpoint("127.0.0.1", 0)
    # No handler yet: python-osc drops messages with no matching handler.
    ...send one message with SimpleUDPClient, then serve(handle) and send another; assert only the second reaches handle (bounded Event wait); close sockets.


def test_feedback_thread_sends_the_full_set_to_a_subscriber_then_stops():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    subs = Subscribers(time.monotonic)
    snapshot = {"mouth_mode": "live", "mumble_connected": True,
                "motors": {n: {"volts": 0.0, "max_hold_tripped": False} for n in ("mouth", "hand", "pivot", "elbow")}}
    stop = start_feedback(endpoint, subs, lambda: snapshot, lambda key, message: None, interval_s=0.01)
    try:
        subs.subscribe(client.getsockname())
        got = recv_messages(client, len(state_messages(snapshot)))
        assert got == [(address, args) for address, args in state_messages(snapshot)]
    finally:
        stop.set()
        client.close()
        endpoint.close()


def test_a_send_error_to_one_subscriber_is_logged_and_feedback_continues():
    # subs with two destinations: one unroutable/closed-port that raises OSError from send — simulate by an endpoint
    # subclass whose send raises OSError for one destination; assert the other still receives and the log was called
    # with a key naming the failed destination.
```

Write the two sketched tests (`test_packets_before_serve_are_ignored` and `test_a_send_error…`) out in full, following the comments. For the send error, wrap the endpoint so `send` raises `OSError` for one destination and delegates for the other; the point is to test our loop's error handling. Use bounded waits only, close every socket, and stay clean under `-W error`. `OscMessage` comes from `pythonosc.osc_message`. Run the tests and see them fail.

- [ ] **Step 2: Implement** in `osc_server.py`:

```python
"""OSC over UDP for show control: receive commands, send replies and feedback. The only module that imports python-osc."""

import threading
from collections.abc import Callable, Mapping

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_server import ThreadingOSCUDPServer

from motor_test.osc_feedback import Destination, Message, Subscribers, state_messages

FEEDBACK_INTERVAL_S = 0.02


class OscEndpoint:
    """One UDP socket: commands arrive on it and replies and feedback leave from it."""

    def __init__(self, host: str, port: int):
        # Live show control: a future bundle timetag would otherwise sleep its handler thread until then.
        self._dispatcher = Dispatcher(strict_timing=False)
        self._server = ThreadingOSCUDPServer((host, port), self._dispatcher)
        # Per-packet threads must not keep the process alive after the talk loop exits.
        self._server.daemon_threads = True

    @property
    def server_address(self) -> Destination:
        return self._server.server_address

    def serve(self, handle: Callable[[str, list, Destination], None]) -> None:
        """Start serving on a daemon thread, passing each message's address, arguments and sender to `handle`."""

        def deliver(sender: Destination, address: str, *args) -> None:
            # python-osc replies to the sender with any non-None handler return value.
            handle(address, list(args), sender)

        self._dispatcher.set_default_handler(deliver, needs_reply_address=True)
        threading.Thread(target=self._server.serve_forever, name="osc", daemon=True).start()

    def send(self, destination: Destination, messages: list[Message]) -> None:
        for address, args in messages:
            builder = OscMessageBuilder(address=address)
            for arg in args:
                builder.add_arg(arg)
            self._server.socket.sendto(builder.build().dgram, destination)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def start_feedback(
    endpoint: OscEndpoint,
    subscribers: Subscribers,
    status: Callable[[], Mapping],
    log: Callable[[str, str], None],
    interval_s: float = FEEDBACK_INTERVAL_S,
) -> threading.Event:
    """Every `interval_s`, send each subscriber what's due; returns an event that stops the thread."""
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(interval_s):
            for destination, messages in subscribers.due(state_messages(status())):
                try:
                    endpoint.send(destination, messages)
                except OSError as error:
                    log(f"feedback {destination}", f"OSC feedback to {destination} failed: {error}")

    threading.Thread(target=run, name="osc-feedback", daemon=True).start()
    return stop
```

`close()` before `serve()` must not hang. `shutdown()` blocks until `serve_forever` is running, so track whether serving started and only call `shutdown()` then. Test it in Step 1 by closing an endpoint that was never served.

- [ ] **Step 3:** Run `tests/test_osc_server.py` with `-W error`, then the full suite. `main.py` still imports `start_osc_server` until Task 4, so `import main` would break. Keep a thin `start_osc_server(host, port, handle)` wrapper for now (it builds an `OscEndpoint`, serves a handler that drops `sender`, and returns the endpoint). Task 4 removes it. Commit `motor_test/osc_server.py tests/test_osc_server.py` with "OSC endpoint: replies from the OSC socket and a feedback thread".

---

### Task 4: Wire it in `main.py` and document it

**Files:** Modify `main.py`, `tests/test_main.py`, `motor_test/osc_server.py` (remove the temporary `start_osc_server` wrapper), `README.md`, and the `TODO.md` if it mentions OSC.

**Consumes:** everything above.

- [ ] **Step 1: Tests (`tests/test_main.py`):**
  - `show_control_config({})` now has `reply_port=None`.
  - `{"JACK_OSC_REPLY_PORT": "21601"}` gives `reply_port=21601`.
  - Bad values (`x`, `0`, `70000`, `²`) exit naming `JACK_OSC_REPLY_PORT`.

  Add `reply_port: int | None = None` as the last field of `ShowControlConfig` so the existing equality tests stay valid. Make `_env_port` accept a `None` default. Run the tests and see them fail.

- [ ] **Step 2: `main()` wiring**, which replaces the `start_osc_server(...)` line:

```python
        endpoint = OscEndpoint("0.0.0.0", config.osc_port)
        subscribers = Subscribers(time.monotonic)
        osc_log = RateLimitedLog(print, OSC_LOG_INTERVAL_S, time.monotonic)
        context = OscContext(
            profiles=profiles, board=board, subscribers=subscribers, send=endpoint.send,
            mumble_connected=lambda: voice.connected, reply_port=config.reply_port, log=osc_log,
        )
        endpoint.serve(lambda address, args, sender: handle_osc(address, args, sender, context))
        start_feedback(endpoint, subscribers, lambda: board.status(voice.connected), osc_log)
```

  Then:
  - Remove `start_osc_server` from `osc_server.py`.
  - Add `OSC replies to <reply port or "sender's port">` to the startup "Talking:" line.
  - Check `.venv/bin/python -c "import main, lipsync_wav, calibrate"`.

- [ ] **Step 3: README.md "Show control"**, a short "OSC replies and feedback" subsection:
  - the four addresses
  - the reply-port rule and `JACK_OSC_REPLY_PORT`
  - renewing the subscription every <60 s
  - the state messages
  - one example: subscribe from a script and print what arrives (python-osc)

  Mention UDP only. Run the full suite with `-W error`. Commit `main.py tests/test_main.py motor_test/osc_server.py README.md` with "Serve OSC replies and feedback from Jack; JACK_OSC_REPLY_PORT".

### Task 5: Roll out (with Boss)

Push only on Boss's go-ahead. After the deploy:
1. From the Mac, send `/jack/ping` and receive `/jack/pong` on the reply port.
2. Subscribe from the Mac, then receive the full set, then changes (drive the elbow pose from the control page and see `/jack/elbow/volts` 2.0, then 0.0).
3. Boss sets `JACK_OSC_REPLY_PORT` in `/etc/jack/jack.env` if his app listens on a fixed port, then restarts Jack.
