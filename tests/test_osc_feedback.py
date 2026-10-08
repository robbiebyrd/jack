from jack.show.control.osc_feedback import MAX_SUBSCRIBERS, Subscribers, state_messages
from tests.fakes import FakeClock

A = ("10.10.0.22", 21601)
B = ("10.10.0.30", 9001)


def status(hand_volts=0.0, tripped=False, mode="live", mumble=True, hand_command=None):
    motors = {
        name: {"volts": 0.0, "max_hold_tripped": False, "command": None} for name in ("mouth", "hand", "pivot", "elbow")
    }
    motors["hand"] = {"volts": hand_volts, "max_hold_tripped": tripped, "command": hand_command}
    return {"mouth_mode": mode, "mumble_connected": mumble, "motors": motors}


def test_state_messages_in_order_with_osc_types():
    assert state_messages(status(hand_volts=1.5, tripped=True, mode="show", mumble=False,
                                 hand_command={"value": 0.75, "age_s": 0.1})) == [
        ("/jack/mouth", [0.0]), ("/jack/mouth/volts", [0.0]), ("/jack/mouth/max_hold", [0]),
        ("/jack/hand", [0.75]), ("/jack/hand/volts", [1.5]), ("/jack/hand/max_hold", [1]),
        ("/jack/pivot", [0.0]), ("/jack/pivot/volts", [0.0]), ("/jack/pivot/max_hold", [0]),
        ("/jack/elbow", [0.0]), ("/jack/elbow/volts", [0.0]), ("/jack/elbow/max_hold", [0]),
        ("/jack/mouth/mode", ["show"]), ("/jack/mouth/mode/show", [1.0]), ("/jack/mumble", [0]),
    ]


def test_a_pose_or_rest_reports_zero_on_the_fader_address():
    posing = state_messages(status(hand_command={"pose": "curl", "seconds_left": 0.3}))
    resting = state_messages(status(hand_command=None))
    assert ("/jack/hand", [0.0]) in posing
    assert ("/jack/hand", [0.0]) in resting
    assert ("/jack/mouth/mode/show", [0.0]) in resting


def test_a_status_without_command_reports_zero_on_the_fader_address():
    snapshot = status()
    for motor in snapshot["motors"].values():
        del motor["command"]
    messages = state_messages(snapshot)
    assert all((f"/jack/{name}", [0.0]) in messages for name in ("mouth", "hand", "pivot", "elbow"))


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


def test_a_subscription_is_still_alive_just_before_60_seconds():
    clock = FakeClock()
    subs = Subscribers(clock)
    subs.subscribe(A)
    clock.advance(59.99)
    assert len(subs) == 1
    assert [destination for destination, _ in subs.due(state_messages(status()))] == [A]


def test_an_expired_subscriber_frees_a_slot_at_the_cap():
    clock = FakeClock()
    subs = Subscribers(clock)
    for port in range(MAX_SUBSCRIBERS):
        subs.subscribe(("10.10.0.22", 20000 + port))
    clock.advance(60.0)
    assert subs.subscribe(B)
    assert len(subs) == 1


def test_renew_from_extends_only_that_ips_subscriptions():
    clock = FakeClock()
    subs = Subscribers(clock)
    subs.subscribe(A)
    subs.subscribe(B)
    clock.advance(50.0)
    subs.renew_from(A[0])
    clock.advance(50.0)
    assert [destination for destination, _ in subs.due(state_messages(status()))] == [A]


def test_renew_from_covers_every_port_of_that_ip():
    clock = FakeClock()
    subs = Subscribers(clock)
    other_port = (A[0], 7000)
    subs.subscribe(A)
    subs.subscribe(other_port)
    clock.advance(50.0)
    subs.renew_from(A[0])
    clock.advance(50.0)
    assert len(subs) == 2


def test_renew_from_does_not_revive_an_expired_subscription():
    clock = FakeClock()
    subs = Subscribers(clock)
    subs.subscribe(A)
    clock.advance(60.0)
    subs.renew_from(A[0])
    assert len(subs) == 0


def test_renew_from_keeps_change_tracking_and_forces_no_full_refresh():
    subs = Subscribers(FakeClock())
    subs.subscribe(A)
    full = state_messages(status())
    subs.due(full)
    subs.renew_from(A[0])
    assert subs.due(full) == []
