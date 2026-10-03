import pytest

from jack.show.control.control_board import ControlBoard
from jack.show.audio.envelope import EnvelopeFollower, rms_dbfs
from jack.show.audio.lip_sync import MouthController
from jack.show.audio.pcm import TICK_S, TICKS_PER_SECOND, silence
from jack.show.motion.ramp import volts_to_count
from jack.application.talk_loop import run_talk_loop
from jack.show.audio.talk_settings import TalkSettings
from tests.audio import constant_frame
from tests.fakes import FakeClock, MotorThatFailsToStop, RecordingMotor, RecordingSink, ScriptedSource, drives, no_op
from tests.profiles import PROFILES, profile

SUPPLY_VOLTS = 12.0
LOUD = constant_frame(20000)  # about -4.3 dBFS: above full_db once the envelope has risen
FAST = {**PROFILES, "mouth": profile("mouth", slew_v_per_s=1000.0), "hand": profile("hand", slew_v_per_s=1000.0),
        "elbow": profile("elbow", slew_v_per_s=1000.0)}


def stop_after(ticks):
    """An `until` check that lets the loop run exactly `ticks` ticks."""
    remaining = ticks

    def until():
        nonlocal remaining
        if remaining == 0:
            return True
        remaining -= 1
        return False

    return until


class SwitchingSource(ScriptedSource):
    """ScriptedSource that switches the board's mouth mode at the start of tick `on_tick` (1-based)."""

    def __init__(self, frames, board, mode, on_tick):
        super().__init__(frames)
        self._board, self._mode, self._on_tick = board, mode, on_tick
        self._ticks = 0

    def take_frames(self):
        self._ticks += 1
        if self._ticks == self._on_tick:
            self._board.set_mouth_mode(self._mode)
        return super().take_frames()


def without_repeats(values):
    """`values` with consecutive repeats dropped, as the loop writes a motor only when its drive changes."""
    return [value for index, value in enumerate(values) if index == 0 or value != values[index - 1]]


def four_motors(**replacements):
    motors = {name: RecordingMotor() for name in ("mouth", "hand", "pivot", "elbow")}
    motors.update(replacements)
    return motors


def new_board(mode="live", profiles=FAST):
    return ControlBoard(profiles, 0.5, mode, FakeClock())


def talk(sources, ticks, *, board=None, motors=None, sink=None, profiles=FAST, settings=TalkSettings(), on_second=no_op):
    sink = RecordingSink() if sink is None else sink
    motors = four_motors() if motors is None else motors
    board = new_board(profiles=profiles) if board is None else board
    run_talk_loop(sources, sink, motors, profiles, settings, board, SUPPLY_VOLTS, on_second, stop_after(ticks))
    return sink, motors, board


def test_quiet_sources_play_silence_and_keep_the_mouth_closed():
    sink, motors, _ = talk([ScriptedSource([])], ticks=3)
    assert sink.frames == [silence()] * 3
    assert drives(motors["mouth"]) == [0]


def test_uncommanded_motors_brake_once_then_again_on_exit():
    _, motors, _ = talk([ScriptedSource([])], ticks=3)
    assert motors["hand"].calls == [("stop",), ("stop",)]


def test_a_loud_voice_is_played_and_opens_the_mouth_fully_in_live_mode():
    sink, motors, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2)
    assert sink.frames == [LOUD, LOUD]
    assert drives(motors["mouth"])[0] < 0
    assert drives(motors["mouth"])[1] == volts_to_count(-6.0, SUPPLY_VOLTS)


def test_sources_sounding_together_are_mixed():
    sink, _, _ = talk([ScriptedSource([LOUD]), ScriptedSource([LOUD])], ticks=1)
    assert sink.frames == [constant_frame(32767)]


def test_mouth_lead_delays_the_audio_but_not_the_mouth():
    sink, motors, _ = talk([ScriptedSource([LOUD] * 3)], ticks=3, settings=TalkSettings(mouth_lead_ms=40.0))
    assert sink.frames == [silence(), silence(), LOUD]
    assert drives(motors["mouth"])[0] < 0


@pytest.mark.parametrize("ticks, pings", [(TICKS_PER_SECOND - 1, 0), (TICKS_PER_SECOND, 1), (2 * TICKS_PER_SECOND, 2)])
def test_on_second_runs_once_per_second_of_audio(ticks, pings):
    calls = []
    talk([ScriptedSource([])], ticks=ticks, on_second=lambda: calls.append(1))
    assert len(calls) == pings


def test_a_commanded_value_drives_its_motor_and_a_steady_value_is_written_once():
    board = new_board()
    board.set_value("hand", 1.0)
    _, motors, _ = talk([ScriptedSource([])], ticks=5, board=board)
    assert drives(motors["hand"]) == [volts_to_count(2.0, SUPPLY_VOLTS)]


def test_a_pose_drives_its_volts():
    board = new_board()
    board.start_pose("elbow", "up")
    _, motors, _ = talk([ScriptedSource([])], ticks=2, board=board)
    assert drives(motors["elbow"]) == [volts_to_count(2.0, SUPPLY_VOLTS)]


def test_show_mode_mouth_follows_the_board_not_the_voice():
    board = new_board("show")
    board.set_value("mouth", 1.0)
    _, motors, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2, board=board)
    assert drives(motors["mouth"]) == [volts_to_count(-6.0, SUPPLY_VOLTS)]


def test_live_mode_mouth_ignores_the_board():
    board = new_board("live")
    board.set_value("mouth", 1.0)
    _, motors, _ = talk([ScriptedSource([])], ticks=2, board=board)
    assert drives(motors["mouth"]) == [0]


def test_a_coast_profile_coasts_at_rest():
    profiles = {**FAST, "hand": profile("hand", rest="coast")}
    _, motors, _ = talk([ScriptedSource([])], ticks=2, profiles=profiles)
    assert motors["hand"].calls[0] == ("coast",)


def test_what_was_driven_is_reported_to_the_board():
    board = new_board()
    board.set_value("hand", 1.0)
    talk([ScriptedSource([])], ticks=2, board=board)
    assert board.status(False)["motors"]["hand"]["volts"] == 2.0


def test_a_sound_card_failure_brakes_every_motor_and_closes_the_sink():
    sink, motors = RecordingSink(fail_on_write=2), four_motors()
    with pytest.raises(OSError):
        talk([ScriptedSource([LOUD] * 5)], ticks=5, sink=sink, motors=motors)
    assert all(motor.calls[-1] == ("stop",) for motor in motors.values())
    assert sink.closed


def test_a_motor_failing_to_stop_still_stops_the_others_and_closes_the_sink():
    sink, motors = RecordingSink(), four_motors(mouth=MotorThatFailsToStop())
    with pytest.raises(OSError):
        talk([ScriptedSource([])], ticks=1, sink=sink, motors=motors)
    assert all(motors[name].calls[-1] == ("stop",) for name in ("hand", "pivot", "elbow"))
    assert sink.closed


def test_switching_to_show_while_lip_sync_holds_the_mouth_open_plays_the_rest_pulse_before_braking():
    board = new_board("live")
    source = SwitchingSource([LOUD] * 10, board, "show", on_tick=3)
    _, motors, _ = talk([source], ticks=10, board=board)
    calls = motors["mouth"].calls
    assert calls[1] == ("drive", volts_to_count(-6.0, SUPPLY_VOLTS))  # open wide on lip sync's second tick
    # Rest pulse +0.5 V for 4 ticks closes the mouth, then the brake; the last stop is the loop's exit.
    assert calls[2:] == [("drive", volts_to_count(0.5, SUPPLY_VOLTS)), ("stop",), ("stop",)]


def test_switching_to_live_opens_from_closed_with_a_fresh_lip_sync_not_a_stale_one():
    settings = TalkSettings()
    board = new_board("show", profiles=PROFILES)
    board.set_value("mouth", 1.0)
    # The mouth's 48 V/s slew (0.96 V a tick) reaches -6 V on the 7th show tick; tick 8 is the first live one.
    source = SwitchingSource([LOUD] * 12, board, "live", on_tick=8)
    _, motors, _ = talk([source], ticks=12, board=board, profiles=PROFILES, settings=settings)
    show_drives, live_drives = drives(motors["mouth"])[:7], drives(motors["mouth"])[7:]
    assert show_drives[-1] == volts_to_count(-6.0, SUPPLY_VOLTS)
    # What a lip sync that starts closed at tick 8 drives, fed the same smoothed levels.
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    levels = [envelope.update(rms_dbfs(LOUD)) for _ in range(12)]
    fresh = MouthController(settings, PROFILES["mouth"])
    expected = [volts_to_count(fresh.update(level), SUPPLY_VOLTS) for level in levels[7:]]
    assert expected[0] == volts_to_count(-0.96, SUPPLY_VOLTS)
    assert live_drives == without_repeats(expected)
