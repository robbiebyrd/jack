"""Jack's talk loop: plays the voices, measures their loudness and drives every motor.

The sound card's blocking write paces the loop at one 20 ms frame per tick, so audio,
mouth and watchdog all run on the same clock. The mouth's target comes from the voice in live
mode and from the show-control board in show mode; every other motor's from the board. One
MotorDriver per motor turns targets into drives. See "Talk loop" in SPEC.md.
"""

from collections import deque
from collections.abc import Callable, Mapping, Sequence

from jack.application.ports import AudioSink, MotorOutput, VoiceSource
from jack.show.audio.envelope import EnvelopeFollower, rms_dbfs
from jack.show.audio.lip_sync import LipSync
from jack.show.audio.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from jack.show.audio.talk_settings import TalkSettings
from jack.show.control.control_board import ControlBoard
from jack.show.motion.motor_driver import Drive, MotorDriver, Rest
from jack.show.motion.poses import MotorProfile
from jack.show.motion.ramp import volts_to_count
from jack.support.attempt_all import attempt_all


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

    Each tick mixes the next frame from every source (silence if none are sounding). The mouth's
    target is that frame's smoothed loudness through lip sync in live mode, or the board's command
    in show mode; every other motor's target is the board's. Each motor's MotorDriver turns the
    target into a drive, so a mode switch never jumps the mouth: the driver carries on from the
    volts and stall budget it has. A motor is written only when its drive changes. The frame plays
    `mouth_lead_ticks` ticks later so the mouth can run ahead of the sound. `on_second` runs once
    per second of audio. Every motor is stopped and the sink closed on the way out, whatever the
    reason, and a failure in one of those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    lip_sync = LipSync(settings, profiles["mouth"])
    drivers = {name: MotorDriver(profiles[name]) for name in motors}
    applied: dict[str, Drive | None] = dict.fromkeys(motors)
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    ticks = 0
    try:
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            level_db = envelope.update(rms_dbfs(frame))
            live_mouth = board.mouth_mode == "live"
            for name, motor in motors.items():
                target = lip_sync.target(level_db) if name == "mouth" and live_mouth else board.target_volts(name)
                drive = drivers[name].update(target)
                if drive != applied[name]:
                    _apply(motor, drive, supply_volts)
                    applied[name] = drive
                board.report(name, _volts(drive), drivers[name].max_hold_tripped)
            delayed_audio.append(frame)
            sink.write(delayed_audio.popleft())
            ticks += 1
            if ticks % TICKS_PER_SECOND == 0:
                on_second()
    finally:
        attempt_all([*(motor.stop for motor in motors.values()), sink.close])


def _volts(drive: Drive | None) -> float:
    """The volts a drive puts across the motor; 0 at rest or before the first write."""
    return 0.0 if drive is None or isinstance(drive, Rest) else drive


def _apply(motor: MotorOutput, drive: Drive, supply_volts: float) -> None:
    if drive == Rest("coast"):
        motor.coast()
    elif isinstance(drive, Rest):
        motor.stop()
    else:
        motor.drive(volts_to_count(drive, supply_volts))
