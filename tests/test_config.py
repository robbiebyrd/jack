import pytest

from jack.adapters.system.deployment import POSES_PATHS
from jack.application import config
from jack.application.config import AppConfig, ConfigError, RocConfig, ShowControlConfig
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.motors import SUPPLY_VOLTS
from tests.profiles import MOUTH

VALID_ENVIRON = {"JACK_MUMBLE_PASSWORD": "s3cret"}


def test_the_whole_config_loads_from_a_valid_environment_and_the_repo_poses():
    loaded = config.load_app_config(VALID_ENVIRON, POSES_PATHS, SUPPLY_VOLTS)
    assert loaded == AppConfig(
        settings=TalkSettings(),
        profiles=config.motor_profiles(POSES_PATHS, SUPPLY_VOLTS),
        show_control=ShowControlConfig(osc_port=9000, http_port=8080, timeout_s=5.0, mouth_mode="live"),
        roc=RocConfig(source_port=10001, repair_port=10002, control_port=10003, target_latency_ms=100.0),
        mumble_password="s3cret",
    )


def test_the_first_problem_stops_the_whole_load():
    with pytest.raises(ConfigError, match="JACK_ATTACK_S"):
        config.load_app_config({**VALID_ENVIRON, "JACK_ATTACK_S": "abc"}, POSES_PATHS, SUPPLY_VOLTS)


def test_config_error_is_a_value_error_so_the_tools_report_it_like_any_other():
    assert issubclass(ConfigError, ValueError)


def test_mumble_password_comes_from_the_environment():
    assert config.mumble_password({"JACK_MUMBLE_PASSWORD": "s3cret"}) == "s3cret"


@pytest.mark.parametrize("environ", [{}, {"JACK_MUMBLE_PASSWORD": ""}])
def test_missing_mumble_password_says_where_to_set_it(environ):
    with pytest.raises(ConfigError) as error:
        config.mumble_password(environ)
    assert "JACK_MUMBLE_PASSWORD" in str(error.value)
    assert "/etc/jack/jack.env" in str(error.value)


def test_no_overrides_gives_the_default_settings():
    assert config.talk_settings({}) == TalkSettings()


def test_env_overrides_set_only_the_named_fields():
    settings = config.talk_settings({"JACK_MOUTH_GAIN_DB": "20", "JACK_ATTACK_S": "0.02"})
    assert settings == TalkSettings(mouth_gain_db=20.0, attack_s=0.02)


def test_env_variables_that_are_not_settings_are_ignored():
    assert config.talk_settings({"JACK_MUMBLE_PASSWORD": "s3cret"}) == TalkSettings()


def test_empty_override_keeps_the_default():
    assert config.talk_settings({"JACK_ATTACK_S": ""}) == TalkSettings()


def test_override_that_is_not_a_number_names_the_variable():
    with pytest.raises(ConfigError) as error:
        config.talk_settings({"JACK_ATTACK_S": "abc"})
    assert str(error.value) == "JACK_ATTACK_S='abc' in /etc/jack/jack.env is not a number"


def test_impossible_override_says_the_settings_are_invalid():
    with pytest.raises(ConfigError, match=r"Mouth settings from /etc/jack/jack\.env are invalid"):
        config.talk_settings({"JACK_ATTACK_S": "0"})


def test_applied_overrides_are_listed_for_the_log():
    assert config.describe_overrides(TalkSettings(mouth_gain_db=20.0, attack_s=0.02)) == (
        "mouth_gain_db=20.0, attack_s=0.02"
    )
    assert config.describe_overrides(TalkSettings()) == ""


@pytest.mark.parametrize("var, value", [("JACK_ATTACK_S", "nan"), ("JACK_RELEASE_S", "inf")])
def test_non_finite_override_names_the_variable(var, value):
    with pytest.raises(ConfigError) as error:
        config.talk_settings({var: value})
    assert str(error.value) == f"{var}={value!r} in /etc/jack/jack.env is not a number"


@pytest.mark.parametrize(
    "var, key",
    [
        ("JACK_OPEN_MIN_V", "min_v"),
        ("JACK_OPEN_MAX_V", "max_v"),
        ("JACK_OPEN_SLEW_V_PER_S", "slew_v_per_s"),
        ("JACK_CLOSE_V", "rest_pulse_v"),
        ("JACK_CLOSE_S", "rest_pulse_s"),
    ],
)
def test_overrides_that_moved_to_poses_toml_are_refused_saying_where(var, key):
    with pytest.raises(ConfigError) as error:
        config.talk_settings({var: "2"})
    message = str(error.value)
    assert var in message and key in message and "/etc/jack/poses.toml" in message


def test_mouth_gain_can_be_set_in_jack_env():
    assert config.talk_settings({"JACK_MOUTH_GAIN_DB": "10"}).mouth_gain_db == 10.0


@pytest.mark.parametrize("var", ["JACK_GATE_OPEN_DB", "JACK_GATE_CLOSE_DB", "JACK_FULL_DB", "JACK_OPEN_CURVE"])
def test_a_replaced_lip_sync_setting_is_refused_saying_what_replaced_it(var):
    with pytest.raises(ConfigError) as error:
        config.talk_settings({var: "-20"})
    message = str(error.value)
    assert var in message and "JACK_MOUTH_GAIN_DB" in message and "amplitude" in message


def test_motor_profiles_load_the_repo_poses_file():
    assert config.motor_profiles(POSES_PATHS, SUPPLY_VOLTS)["mouth"].max_v == 6.0


def test_invalid_poses_file_is_one_config_error(tmp_path):
    bad = tmp_path / "poses.toml"
    bad.write_text("[hand]\nmax_v = 99\n")
    with pytest.raises(ConfigError, match="99"):
        config.motor_profiles((POSES_PATHS[0], bad), SUPPLY_VOLTS)


def test_a_mouth_gain_that_would_open_the_mouth_on_silence_is_refused():
    with pytest.raises(ConfigError) as error:
        config.check_mouth_settings(TalkSettings(mouth_gain_db=80.0), {"mouth": MOUTH})
    message = str(error.value)
    # Either file can cause it: the gain in jack.env, or the mouth's min_v/max_v in an override poses.toml.
    assert "mouth_gain_db" in message
    assert "/etc/jack/jack.env" in message and "poses.toml" in message
    config.check_mouth_settings(TalkSettings(), {"mouth": MOUTH})


def test_show_control_defaults():
    assert config.show_control_config({}) == ShowControlConfig(
        osc_port=9000, http_port=8080, timeout_s=5.0, mouth_mode="live"
    )


def test_reply_port_defaults_to_the_senders_port():
    assert config.show_control_config({}).reply_port is None


def test_reply_port_comes_from_the_environment():
    assert config.show_control_config({"JACK_OSC_REPLY_PORT": "21601"}).reply_port == 21601


@pytest.mark.parametrize("value", ["x", "0", "70000", "²"])
def test_bad_reply_port_names_the_variable(value):
    with pytest.raises(ConfigError, match="JACK_OSC_REPLY_PORT"):
        config.show_control_config({"JACK_OSC_REPLY_PORT": value})


def test_show_control_from_the_environment():
    loaded = config.show_control_config(
        {"JACK_OSC_PORT": "9100", "JACK_HTTP_PORT": "8181", "JACK_CONTROL_TIMEOUT_S": "1.5", "JACK_MOUTH_MODE": "show"}
    )
    assert loaded == ShowControlConfig(osc_port=9100, http_port=8181, timeout_s=1.5, mouth_mode="show")


@pytest.mark.parametrize(
    "var, value",
    [("JACK_OSC_PORT", "x"), ("JACK_OSC_PORT", "70000"), ("JACK_OSC_PORT", "²"), ("JACK_HTTP_PORT", "0"),
     ("JACK_CONTROL_TIMEOUT_S", "nan"), ("JACK_CONTROL_TIMEOUT_S", "-1"), ("JACK_MOUTH_MODE", "auto")],
)
def test_bad_show_control_settings_name_the_variable(var, value):
    with pytest.raises(ConfigError, match=var):
        config.show_control_config({var: value})


def test_roc_settings_default_to_the_spec_values():
    assert config.roc_config({}) == RocConfig(
        source_port=10001, repair_port=10002, control_port=10003, target_latency_ms=100.0
    )


def test_roc_settings_come_from_the_environment():
    environ = {
        "JACK_ROC_SOURCE_PORT": "11001", "JACK_ROC_REPAIR_PORT": "11002",
        "JACK_ROC_CONTROL_PORT": "11003", "JACK_ROC_TARGET_LATENCY_MS": "120",
    }
    assert config.roc_config(environ) == RocConfig(11001, 11002, 11003, 120.0)


@pytest.mark.parametrize(
    "var, value",
    [("JACK_ROC_SOURCE_PORT", "0"), ("JACK_ROC_REPAIR_PORT", "70000"), ("JACK_ROC_CONTROL_PORT", "x"),
     ("JACK_ROC_TARGET_LATENCY_MS", "0"), ("JACK_ROC_TARGET_LATENCY_MS", "-5"), ("JACK_ROC_TARGET_LATENCY_MS", "nan")],
)
def test_bad_roc_settings_name_the_variable(var, value):
    with pytest.raises(ConfigError, match=var):
        config.roc_config({var: value})
