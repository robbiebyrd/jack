from jack.show.motion.segments import describe, hold, ramp


def test_hold_keeps_one_level():
    assert hold(-2.0, 0.5) == (-2.0, -2.0, 0.5)


def test_ramp_moves_between_levels():
    assert ramp(0.0, -6.0, 0.25) == (0.0, -6.0, 0.25)


def test_describe_summarises_holds_and_ramps():
    assert describe((hold(1.0, 0.25), ramp(0.0, -6.0, 0.25))) == "+1.0 V for 0.25 s, +0.0 -> -6.0 V over 0.25 s"
