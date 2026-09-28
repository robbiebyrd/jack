"""Jack's talk loop: plays the voices, measures their loudness and moves the mouth to match.

The sound card's blocking write paces the loop at one 20 ms frame per tick, so audio,
mouth and watchdog all run on the same clock. See "Talk loop" in SPEC.md.
"""

from collections import deque
from collections.abc import Callable, Sequence

from motor_test.attempt_all import attempt_all
from motor_test.envelope import EnvelopeFollower, rms_dbfs
from motor_test.lip_sync import MouthController
from motor_test.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from motor_test.ports import AudioSink, MotorOutput, VoiceSource
from motor_test.ramp import volts_to_count
from motor_test.talk_settings import TalkSettings


def run_talk_loop(
    sources: Sequence[VoiceSource],
    sink: AudioSink,
    mouth: MotorOutput,
    idle_motors: Sequence[MotorOutput],
    settings: TalkSettings,
    supply_volts: float,
    on_second: Callable[[], None],
    until: Callable[[], bool] = lambda: False,
) -> None:
    """Play and lip-sync every source until `until()` is true (never, for the app).

    Each tick mixes the next frame from every source (silence if none are sounding),
    drives the mouth from its smoothed loudness, then plays the frame `mouth_lead_ticks`
    ticks later so the mouth can run ahead of the sound. `on_second` runs once per
    second of audio. Every motor is stopped and the sink closed on the way out, whatever
    the reason, and a failure in one of those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    controller = MouthController(settings)
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    ticks = 0
    try:
        check_supply(settings, supply_volts)
        for motor in idle_motors:
            motor.drive(0)
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            volts = controller.update(envelope.update(rms_dbfs(frame)))
            mouth.drive(volts_to_count(volts, supply_volts))
            delayed_audio.append(frame)
            sink.write(delayed_audio.popleft())
            ticks += 1
            if ticks % TICKS_PER_SECOND == 0:
                on_second()
    finally:
        attempt_all([mouth.stop, *(motor.stop for motor in idle_motors), sink.close])


def check_supply(settings: TalkSettings, supply_volts: float) -> None:
    """Reject settings that ask for more volts than the supply can produce, before any motion."""
    for name in ("open_max_v", "close_v"):
        if getattr(settings, name) > supply_volts:
            raise ValueError(f"{name} ({getattr(settings, name)} V) exceeds the {supply_volts} V supply")
