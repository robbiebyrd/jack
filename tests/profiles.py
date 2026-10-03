"""MotorProfiles written out for tests, so tests don't depend on poses.toml's tunable values."""

import dataclasses

from motor_test.poses import Hold, MotorProfile, Pose

MOUTH = MotorProfile(
    calibrated=True, min_v=1.0, max_v=6.0, sign=-1, slew_v_per_s=48.0,
    holds=(Hold(-6.0, 1.0), Hold(1.0, 1.0)),
    rest="brake", rest_pulse_v=0.5, rest_pulse_s=0.08,
    poses={"close": Pose(1.0, 0.25), "relax": Pose(-2.0, 0.5), "open": Pose(-6.0, 0.5)},
)
HAND = MotorProfile(
    calibrated=False, min_v=1.0, max_v=2.0, sign=1, slew_v_per_s=24.0,
    holds=(Hold(-2.0, 1.0), Hold(2.0, 1.0)),
    rest="brake", rest_pulse_v=0.0, rest_pulse_s=0.0, poses={"curl": Pose(2.0, 0.5)},
)
PIVOT = dataclasses.replace(HAND, poses={"left": Pose(-2.0, 0.5), "right": Pose(2.0, 0.5)})
ELBOW = dataclasses.replace(HAND, poses={"up": Pose(2.0, 0.5)})
PROFILES = {"mouth": MOUTH, "hand": HAND, "pivot": PIVOT, "elbow": ELBOW}


def profile(name, **overrides):
    """PROFILES[name] with some fields replaced."""
    return dataclasses.replace(PROFILES[name], **overrides)
