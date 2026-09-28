import configparser
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def load_unit(name="jack.service"):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # systemd keys are case-sensitive
    unit_path = DEPLOY / name
    parser.read(unit_path)
    return parser


def test_app_restarts_after_any_exit_and_never_gives_up():
    unit = load_unit()
    assert unit["Service"]["Restart"] == "always"
    assert unit["Service"]["RestartSec"] == "5"
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"


def test_app_is_supervised_by_the_systemd_watchdog():
    unit = load_unit()
    assert unit["Service"]["Type"] == "notify"
    assert unit["Service"]["NotifyAccess"] == "main"
    assert unit["Service"]["WatchdogSec"] == "10"


def test_app_runs_as_jack_at_boot_without_login():
    unit = load_unit()
    assert unit["Service"]["User"] == "jack"
    assert unit["Service"]["SupplementaryGroups"] == "i2c"
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_updater_gives_up_on_a_hung_fetch():
    unit = load_unit("jack-update.service")
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["TimeoutStartSec"] == "120"


def test_updater_service_runs_the_update_script():
    unit = load_unit("jack-update.service")
    assert unit["Service"]["ExecStart"] == "/bin/bash /opt/jack/deploy/jack-update.sh"


def test_updater_timer_polls_every_60_seconds_precisely():
    unit = load_unit("jack-update.timer")
    assert unit["Timer"]["OnBootSec"] == "60"
    assert unit["Timer"]["OnUnitActiveSec"] == "60"
    assert unit["Timer"]["AccuracySec"] == "1s"
    assert unit["Install"]["WantedBy"] == "timers.target"
