from pathlib import Path

import pytest

from motor_test.poses import Pose, load_profiles
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
max_hold_s = 1.0
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
        MOTOR_TABLE.format(name=name).replace("[" + name + ".poses]", extra_by_motor.get(name, "") + "\n[" + name + ".poses]")
        for name in ("mouth", "hand", "pivot", "elbow")
    )


def test_repo_poses_file_matches_the_spec():
    profiles = load_profiles([REPO_POSES], SUPPLY)
    mouth = profiles["mouth"]
    assert (mouth.calibrated, mouth.min_v, mouth.max_v, mouth.sign) == (True, 1.0, 6.0, -1)
    assert (mouth.slew_v_per_s, mouth.rest, mouth.rest_pulse_v, mouth.rest_pulse_s) == (48.0, "brake", 0.5, 0.08)
    assert mouth.poses == {"close": Pose(1.0, 0.25), "relax": Pose(-2.0, 0.5), "open": Pose(-6.0, 0.5)}
    for name, poses in (("hand", {"curl"}), ("pivot", {"left", "right"}), ("elbow", {"up"})):
        placeholder = profiles[name]
        assert placeholder.calibrated is False
        assert (placeholder.max_v, placeholder.max_hold_s, placeholder.slew_v_per_s, placeholder.sign) == (2.0, 1.0, 24.0, 1)
        assert set(placeholder.poses) == poses
        assert all(abs(pose.volts) == 2.0 and pose.seconds == 0.5 for pose in placeholder.poses.values())


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
        ("[hand]\nmax_hold_s = 0.33\n", "max_hold_s"),
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
