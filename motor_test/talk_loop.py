"""Jack's talk loop: plays the voices, measures their loudness and drives every motor.

The sound card's blocking write paces the loop at one 20 ms frame per tick, so audio,
mouth and watchdog all run on the same clock. The mouth follows the voice in live mode;
every other motor follows the show-control board. See "Talk loop" in SPEC.md.
"""

from collections import deque
from collections.abc import Callable, Mapping, Sequence

from motor_test.attempt_all import attempt_all
from motor_test.control_board import ControlBoard
from motor_test.envelope import EnvelopeFollower, rms_dbfs
from motor_test.lip_sync import MouthController
from motor_test.motor_driver import Drive, MotorDriver, Rest
from motor_test.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from motor_test.ports import AudioSink, MotorOutput, VoiceSource
from motor_test.poses import MotorProfile
from motor_test.ramp import volts_to_count
from motor_test.talk_settings import TalkSettings


def run_talk_loop(
    sources: Sequence[VoiceSource],
    sink: AudioSink,
    motors: Mapping[str, MotorOutput],
    profiles: Mapping[str, MotorProfile],
    settings: TalkSettings,
    board: ControlBoard,
    supply_volts: float,
    on_second: Callable[[], None],
    until: Callable[[], bool] = lambda: False,
) -> None:
    """Play every source and drive every motor until `until()` is true (never, for the app).

    Each tick mixes the next frame from every source (silence if none are sounding). The mouth
    follows that frame's smoothed loudness in live mode, or the board in show mode; every other
    motor follows the board through its MotorDriver. A motor is written only when its drive
    changes. The frame plays `mouth_lead_ticks` ticks later so the mouth can run ahead of the
    sound. `on_second` runs once per second of audio. Every motor is stopped and the sink closed
    on the way out, whatever the reason, and a failure in one of those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    lip_sync = MouthController(settings, profiles["mouth"])
    drivers = {name: MotorDriver(profiles[name]) for name in motors}
    applied: dict[str, Drive | None] = {name: None for name in motors}
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    ticks = 0
    try:
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            live_mouth = lip_sync.update(envelope.update(rms_dbfs(frame)))
            for name, motor in motors.items():
                if name == "mouth" and board.mouth_mode == "live":
                    drive, tripped = live_mouth, False
                else:
                    drive = drivers[name].update(board.target_volts(name))
                    tripped = drivers[name].max_hold_tripped
                if drive != applied[name]:
                    _apply(motor, drive, supply_volts)
                    applied[name] = drive
                board.report(name, 0.0 if isinstance(drive, Rest) else drive, tripped)
            delayed_audio.append(frame)
            sink.write(delayed_audio.popleft())
            ticks += 1
            if ticks % TICKS_PER_SECOND == 0:
                on_second()
    finally:
        attempt_all([*(motor.stop for motor in motors.values()), sink.close])


def _apply(motor: MotorOutput, drive: Drive, supply_volts: float) -> None:
    if drive == Rest("coast"):
        motor.coast()
    elif isinstance(drive, Rest):
        motor.stop()
    else:
        motor.drive(volts_to_count(drive, supply_volts))
