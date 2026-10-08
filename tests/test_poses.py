from pathlib import Path

import pytest

from jack.show.motion.poses import Hold, Pose, load_profiles
from tests.profiles import HAND, MOUTH, PIVOT, profile

SUPPLY = 12.0
REPO_POSES = Path(__file__).resolve().parent.parent / "poses.toml"

MOTOR_TABLE = """
[{name}]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1
slew_v_per_s = 24.0
holds = [{{ volts = 3.0, seconds = 1.0 }}, {{ volts = -3.0, seconds = 1.0 }}]
rest = "brake"
[{name}.poses]
go = {{ volts = 2.0, seconds = 0.5 }}
"""


def write(tmp_path, text, name="poses.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def all_motors(**extra_by_motor):
    """A valid file for all four motors; extra_by_motor[name] is appended to that motor's table."""
    return "".join(
        MOTOR_TABLE.format(name=name).replace(f"[{name}.poses]", extra_by_motor.get(name, "") + f"\n[{name}.poses]")
        for name in ("mouth", "hand", "pivot", "elbow")
    )


def test_repo_poses_file_matches_the_spec():
    profiles = load_profiles([REPO_POSES], SUPPLY)
    mouth = profiles["mouth"]
    assert (mouth.calibrated, mouth.min_v, mouth.max_v, mouth.sign) == (True, 1.0, 6.0, 1)
    assert mouth.holds == (Hold(-5.0, 0.25), Hold(-0.25, 1.0), Hold(2.0, 2.0), Hold(6.0, 0.5))
    assert (mouth.slew_v_per_s, mouth.rest, mouth.rest_pulse_v, mouth.rest_pulse_s) == (48.0, "brake", -0.5, 0.08)
    assert mouth.poses == {"close": Pose(-1.0, 0.25), "relax": Pose(2.0, 0.5), "open": Pose(6.0, 0.5)}
    hand = profiles["hand"]
    assert (hand.calibrated, hand.min_v, hand.max_v, hand.sign) == (True, 3.0, 5.0, 1)
    assert hand.holds == (
        Hold(-5.0, 0.5), Hold(-3.0, 1.0), Hold(-1.0, 4.0), Hold(3.0, 5.0), Hold(5.0, 2.0), Hold(6.0, 2.0)
    )
    assert (hand.slew_v_per_s, hand.rest, hand.rest_pulse_s) == (24.0, "brake", 0.0)
    assert hand.poses == {"curl": Pose(6.0, 1.5), "open": Pose(-5.0, 0.5)}
    pivot = profiles["pivot"]
    assert (pivot.calibrated, pivot.min_v, pivot.max_v, pivot.sign) == (True, 6.0, 7.0, 1)
    assert pivot.holds == (Hold(-7.0, 4.0), Hold(-6.0, 4.0), Hold(6.0, 4.0), Hold(7.0, 4.0))
    assert (pivot.slew_v_per_s, pivot.rest, pivot.rest_pulse_s) == (24.0, "brake", 0.0)
    assert pivot.poses == {"left": Pose(-6.0, 4.0), "right": Pose(6.0, 4.0)}
    elbow = profiles["elbow"]
    assert elbow.calibrated is False
    assert elbow.holds == (Hold(-2.0, 1.0), Hold(2.0, 1.0))
    assert (elbow.max_v, elbow.slew_v_per_s, elbow.sign) == (2.0, 24.0, 1)
    assert elbow.poses == {"up": Pose(2.0, 0.5)}


def test_repo_poses_fit_inside_their_holds():
    for name, motor in load_profiles([REPO_POSES], SUPPLY).items():
        for pose_name, pose in motor.poses.items():
            assert pose.seconds <= motor.hold_s(pose.volts), f"{name} {pose_name}"


def test_rest_pulse_ticks_counts_whole_ticks_and_is_zero_without_a_pulse():
    assert MOUTH.rest_pulse_ticks == 4  # 0.08 s at 20 ms a tick
    assert HAND.rest_pulse_ticks == 0


def test_value_maps_from_min_to_max_in_the_motors_direction():
    assert MOUTH.volts_for(0.0) == 0.0
    assert MOUTH.volts_for(1.0) == -6.0
    assert MOUTH.volts_for(0.5) == -3.5
    assert HAND.volts_for(0.25) == 1.25


def test_two_sided_value_runs_the_other_way_below_zero():
    assert PIVOT.volts_for(-1.0) == -2.0
    assert PIVOT.volts_for(1.0) == 2.0


def test_override_file_replaces_fields_and_poses_one_by_one(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, '[hand]\nmax_v = 3.0\n[hand.poses]\ncurl = { volts = 3.0, seconds = 1.0 }\n', "pi.toml")
    hand = load_profiles([base, override], SUPPLY)["hand"]
    assert hand.max_v == 3.0
    assert hand.min_v == 1.0
    assert hand.poses == {"go": Pose(2.0, 0.5), "curl": Pose(3.0, 1.0)}


def test_missing_override_file_is_skipped(tmp_path):
    base = write(tmp_path, all_motors())
    assert load_profiles([base, tmp_path / "absent.toml"], SUPPLY)["elbow"].max_v == 2.0


def test_missing_first_file_is_an_error(tmp_path):
    with pytest.raises(OSError):
        load_profiles([tmp_path / "absent.toml"], SUPPLY)


@pytest.mark.parametrize(
    "text, message",
    [
        ("[tail]\nmin_v = 1.0\n", "unknown motor"),
        ("[hand]\nmax_volts = 3.0\n", "max_volts"),
        ("[hand]\nmax_v = 13.0\n", "supply"),
        ("[hand]\nmin_v = 3.0\n", "min_v"),
        ("[hand]\nsign = 2\n", "sign"),
        ("[hand]\nrest = \"float\"\n", "rest"),
        ("[hand]\nmax_hold_s = 1.0\n", "holds"),
        ("[hand]\nholds = 3\n", "holds"),
        ("[hand]\nholds = []\n", "holds"),
        ("[hand]\nholds = [{ volts = 2.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 0.0, seconds = 1.0 }, { volts = -3.0, seconds = 1.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 3.0, seconds = 0 }, { volts = -3.0, seconds = 1.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 20.0, seconds = 1.0 }, { volts = -3.0, seconds = 1.0 }]\n", "supply"),
        (
            "[hand]\nholds = [{ volts = 3.0, seconds = 1.0 }, { volts = 3.0, seconds = 2.0 },"
            " { volts = -3.0, seconds = 1.0 }]\n",
            "more than once",
        ),
        ("[elbow]\nmax_v = 4.0\n", "max_v"),
        ("[pivot]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "max_v"),
        ("[hand.poses]\ncurl = { volts = -4.0, seconds = 0.5 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0, seconds = 1.5 }\n", "curl"),
        ("[mouth]\nrest_pulse_v = 4.0\nrest_pulse_s = 0.08\n", "rest_pulse"),
        ("[mouth]\nrest_pulse_v = 0.5\nrest_pulse_s = 2.0\n", "rest_pulse"),
        ("[hand]\nrest_pulse_v = 0.5\nrest_pulse_s = 0.05\n", "rest_pulse_s"),
        ("[hand]\nslew_v_per_s = 0\n", "slew_v_per_s"),
        ("[hand]\nmax_v = nan\n", "max_v"),
        ("[hand.poses]\ncurl = { volts = 20.0, seconds = 0.5 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0, seconds = 0 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0 }\n", "curl"),
        ("hand = 3\n", "hand"),
        ("[hand\n", "pi.toml"),
    ],
)
def test_invalid_entries_are_rejected_naming_what_is_wrong(tmp_path, text, message):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, text, "pi.toml")
    with pytest.raises(ValueError, match=message):
        load_profiles([base, override], SUPPLY)


def test_a_motor_missing_from_every_file_is_an_error(tmp_path):
    only_mouth = write(tmp_path, MOTOR_TABLE.format(name="mouth"))
    with pytest.raises(ValueError, match="hand, pivot, elbow"):
        load_profiles([only_mouth], SUPPLY)


def test_profile_helper_replaces_fields():
    assert profile("mouth", slew_v_per_s=1000.0).slew_v_per_s == 1000.0


def test_a_bad_value_in_the_override_names_the_override_file(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand]\nmax_v = 13.0\n", "pi.toml")
    with pytest.raises(ValueError, match="max_v") as error:
        load_profiles([base, override], SUPPLY)
    assert str(override) in str(error.value)


def test_a_bad_value_in_the_base_file_names_the_base_file(tmp_path):
    base = write(tmp_path, all_motors().replace("max_v = 2.0", "max_v = 13.0", 1))
    with pytest.raises(ValueError, match="max_v") as error:
        load_profiles([base, tmp_path / "absent.toml"], SUPPLY)
    assert str(base) in str(error.value)


def test_a_bad_pose_names_the_file_that_set_it(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand.poses]\ncurl = { volts = 20.0, seconds = 0.5 }\n", "pi.toml")
    with pytest.raises(ValueError, match="curl") as error:
        load_profiles([base, override], SUPPLY)
    assert str(override) in str(error.value)


def test_a_non_table_poses_value_is_rejected_naming_file_and_motor(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand]\nposes = 3\n", "pi.toml")
    with pytest.raises(ValueError, match="poses") as error:
        load_profiles([base, override], SUPPLY)
    assert str(override) in str(error.value)
    assert "[hand]" in str(error.value)


def test_a_missing_required_key_names_the_files_that_set_the_motor(tmp_path):
    text = all_motors()
    head, _, tail = text.rpartition("[elbow]")
    base = write(tmp_path, head + "[elbow]" + tail.replace('rest = "brake"\n', "", 1))
    with pytest.raises(ValueError, match="rest") as error:
        load_profiles([base], SUPPLY)
    assert str(base) in str(error.value)


def test_float_sign_is_stored_as_an_int(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand]\nsign = -1.0\n", "pi.toml")
    sign = load_profiles([base, override], SUPPLY)["hand"].sign
    assert sign == -1
    assert isinstance(sign, int)


def test_a_leftover_max_hold_s_says_to_write_holds(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand]\nmax_hold_s = 1.0\n", "pi.toml")
    with pytest.raises(ValueError, match=r"max_hold_s.*holds") as error:
        load_profiles([base, override], SUPPLY)
    assert str(override) in str(error.value)


def test_holds_load_as_points(tmp_path):
    base = write(tmp_path, all_motors())
    assert load_profiles([base], SUPPLY)["hand"].holds == (Hold(-3.0, 1.0), Hold(3.0, 1.0))


def test_an_override_replaces_the_whole_holds_list(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[elbow]\nholds = [{ volts = 3.0, seconds = 0.5 }]\n", "pi.toml")
    assert load_profiles([base, override], SUPPLY)["elbow"].holds == (Hold(3.0, 0.5),)


def test_a_one_sided_motor_needs_no_points_on_a_side_nothing_drives(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[elbow]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "pi.toml")
    assert load_profiles([base, override], SUPPLY)["elbow"].hold_s(2.0) == 1.0


def test_an_unmeasured_hold_names_the_files_that_set_the_motor(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[pivot]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "pi.toml")
    with pytest.raises(ValueError, match="no hold measured") as error:
        load_profiles([base, override], SUPPLY)
    assert str(base) in str(error.value) and str(override) in str(error.value)


MEASURED = profile(
    "hand", holds=(Hold(2.0, 2.0), Hold(6.0, 0.5), Hold(-0.25, 1.0), Hold(-5.0, 0.25)),
)


def test_hold_at_a_measured_point_is_that_points_seconds():
    assert MEASURED.hold_s(6.0) == 0.5
    assert MEASURED.hold_s(-5.0) == 0.25


def test_hold_between_points_is_interpolated_on_magnitude():
    assert MEASURED.hold_s(4.0) == pytest.approx(1.25)  # halfway from 2 s at 2 V to 0.5 s at 6 V


def test_hold_below_the_lowest_point_is_the_lowest_points_hold():
    assert MEASURED.hold_s(1.0) == 2.0
    assert MEASURED.hold_s(-0.1) == 1.0


def test_hold_is_read_only_from_points_on_the_same_side():
    assert MEASURED.hold_s(-1.0) == pytest.approx(1.0 - 0.75 * 0.75 / 4.75)


@pytest.mark.parametrize("volts", [6.5, -5.5, 0.0])
def test_unmeasured_volts_have_no_hold(volts):
    with pytest.raises(ValueError, match="no hold measured"):
        MEASURED.hold_s(volts)


def test_a_side_with_no_points_has_no_hold():
    with pytest.raises(ValueError, match="no hold measured"):
        profile("hand", holds=(Hold(2.0, 1.0),)).hold_s(-1.0)
