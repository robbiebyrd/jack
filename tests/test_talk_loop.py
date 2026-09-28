import pytest

from motor_test.pcm import TICKS_PER_SECOND, silence
from motor_test.ramp import volts_to_count
from motor_test.talk_loop import run_talk_loop
from motor_test.talk_settings import TalkSettings
from tests.audio import constant_frame
from tests.fakes import MotorThatFailsToStop, RecordingMotor, RecordingSink, ScriptedSource, drives, no_op

SUPPLY_VOLTS = 12.0
LOUD = constant_frame(20000)  # about -4.3 dBFS: above full_db once the envelope has risen
FAST = TalkSettings(open_slew_v_per_s=1000.0)


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


def talk(sources, ticks, settings=FAST, sink=None, mouth=None, idle=None, on_second=no_op):
    sink = sink if sink is not None else RecordingSink()
    mouth = mouth if mouth is not None else RecordingMotor()
    idle = idle if idle is not None else RecordingMotor()
    run_talk_loop(sources, sink, mouth, [idle], settings, SUPPLY_VOLTS, on_second, stop_after(ticks))
    return sink, mouth, idle


def test_quiet_sources_play_silence_and_keep_the_mouth_closed():
    sink, mouth, idle = talk([ScriptedSource([])], ticks=3)
    assert sink.frames == [silence()] * 3
    assert drives(mouth) == [0, 0, 0]


def test_idle_motor_holds_zero_volts_then_brakes():
    _, _, idle = talk([ScriptedSource([])], ticks=3)
    assert idle.calls == [("drive", 0), ("stop",)]


def test_a_loud_voice_is_played_and_opens_the_mouth_fully():
    sink, mouth, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2)
    assert sink.frames == [LOUD, LOUD]
    assert drives(mouth)[0] < 0
    assert drives(mouth)[1] == volts_to_count(-6.0, SUPPLY_VOLTS)


def test_sources_sounding_together_are_mixed():
    sink, _, _ = talk([ScriptedSource([LOUD]), ScriptedSource([LOUD])], ticks=1)
    assert sink.frames == [constant_frame(32767)]


def test_mouth_lead_delays_the_audio_but_not_the_mouth():
    settings = TalkSettings(open_slew_v_per_s=1000.0, mouth_lead_ms=40.0)
    sink, mouth, _ = talk([ScriptedSource([LOUD] * 3)], ticks=3, settings=settings)
    assert sink.frames == [silence(), silence(), LOUD]
    assert drives(mouth)[0] < 0


@pytest.mark.parametrize("ticks, pings", [(TICKS_PER_SECOND - 1, 0), (TICKS_PER_SECOND, 1), (2 * TICKS_PER_SECOND, 2)])
def test_on_second_runs_once_per_second_of_audio(ticks, pings):
    calls = []
    talk([ScriptedSource([])], ticks=ticks, on_second=lambda: calls.append(1))
    assert len(calls) == pings


def test_a_sound_card_failure_brakes_both_motors_and_closes_the_sink():
    sink, mouth, idle = RecordingSink(fail_on_write=2), RecordingMotor(), RecordingMotor()
    with pytest.raises(OSError):
        talk([ScriptedSource([LOUD] * 5)], ticks=5, sink=sink, mouth=mouth, idle=idle)
    assert mouth.calls[-1] == ("stop",)
    assert idle.calls[-1] == ("stop",)
    assert sink.closed


def test_a_motor_failing_to_stop_still_stops_the_other_and_closes_the_sink():
    sink, idle = RecordingSink(), RecordingMotor()
    with pytest.raises(OSError):
        talk([ScriptedSource([])], ticks=1, sink=sink, mouth=MotorThatFailsToStop(), idle=idle)
    assert idle.calls[-1] == ("stop",)
    assert sink.closed


@pytest.mark.parametrize("overrides", [{"open_max_v": 13.0}, {"close_v": 12.5}])
def test_voltages_beyond_the_supply_are_rejected_before_the_mouth_moves(overrides):
    sink, mouth = RecordingSink(), RecordingMotor()
    with pytest.raises(ValueError):
        talk([ScriptedSource([LOUD])], ticks=1, settings=TalkSettings(**overrides), sink=sink, mouth=mouth)
    assert drives(mouth) == []
    assert sink.frames == []
    assert sink.closed
