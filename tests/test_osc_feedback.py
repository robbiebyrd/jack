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
