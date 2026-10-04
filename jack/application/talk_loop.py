"""Jack's talk loop: plays the voices, measures their loudness and drives every motor.

The sound card's blocking write paces the loop at one 20 ms frame per tick, so audio,
mouth and watchdog all run on the same clock. The mouth follows the voice in live mode;
every other motor follows the show-control board. See "Talk loop" in SPEC.md.
"""

from collections import deque
from collections.abc import Callable, Mapping, Sequence

from jack.support.attempt_all import attempt_all
from jack.show.control.control_board import ControlBoard
from jack.show.audio.envelope import EnvelopeFollower, rms_dbfs
from jack.show.audio.lip_sync import MouthController
from jack.show.motion.motor_driver import Drive, MotorDriver, Rest
from jack.show.audio.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from jack.application.ports import AudioSink, MotorOutput, VoiceSource
from jack.show.motion.poses import MotorProfile
from jack.show.motion.ramp import volts_to_count
from jack.show.audio.talk_settings import TalkSettings


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
    motor follows the board through its MotorDriver. Switching the mouth to show hands its driver
    the volts lip sync left it at; switching to live starts a fresh, closed lip sync. A motor is
    written only when its drive changes. The frame plays `mouth_lead_ticks` ticks later so the
    mouth can run ahead of the sound. `on_second` runs once per second of audio. Every motor is
    stopped and the sink closed on the way out, whatever the reason, and a failure in one of
    those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    lip_sync = MouthController(settings, profiles["mouth"])
    drivers = {name: MotorDriver(profiles[name]) for name in motors}
    applied: dict[str, Drive | None] = {name: None for name in motors}
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    mouth_mode = board.mouth_mode
    ticks = 0
    try:
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            level_db = envelope.update(rms_dbfs(frame))
            mode = board.mouth_mode
            if mode != mouth_mode:
                if mode == "show":
                    # Take over from where lip sync left the mouth, so it slews or rest-pulses from there,
                    # with the stall budget lip sync spent: a mode switch is not a rest.
                    drivers["mouth"].resume_from(_volts(applied["mouth"]), lip_sync.budget_spent)
                else:
                    # A fresh lip sync starts closed and opens with its own slew, and the budget show spent.
                    lip_sync = MouthController(settings, profiles["mouth"], drivers["mouth"].budget_spent)
                mouth_mode = mode
            for name, motor in motors.items():
                if name == "mouth" and mouth_mode == "live":
                    drive, tripped = lip_sync.update(level_db), False
                else:
                    drive = drivers[name].update(board.target_volts(name))
                    tripped = drivers[name].max_hold_tripped
                if drive != applied[name]:
                    _apply(motor, drive, supply_volts)
                    applied[name] = drive
                board.report(name, _volts(drive), tripped)
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
