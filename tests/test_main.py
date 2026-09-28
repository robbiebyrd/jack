import signal

import pytest

import main


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(notify_socket):
    main.ping_watchdog()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


def test_cycle_is_well_inside_the_watchdog_timeout():
    watchdog_s = 10  # WatchdogSec in deploy/jack.service
    assert main.CYCLE_S * 2 < watchdog_s


def test_configured_ramp_peaks_at_six_volts_over_a_two_second_cycle():
    counts = main.ramp_profile(main.PEAK_VOLTS, main.SUPPLY_VOLTS, main.STEPS_PER_CYCLE)
    assert max(counts) == 2048
    assert main.CYCLE_S / main.STEPS_PER_CYCLE == pytest.approx(0.04)


def test_both_hat_channels_are_driven():
    assert main.DRIVEN_CHANNELS == (main.MOTOR_A, main.MOTOR_B)
