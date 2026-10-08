# Code review of `main` (2026-10-08, commit 63f9ba8)

Scope: every production module (`jack/`, `main.py`, `calibrate.py`, `lipsync_wav.py`, `tools/`), the
deploy scripts, the tests, and `SPEC.md`'s architecture section. Lenses: readability, composability,
separation of concerns, the W-PY rule list, and whether a Python framework would help.

Baseline measured in this review: 1807 tests, 1805 pass. The 2 failures
(`test_unreadable_app_config_says_to_run_as_jack` in `test_calibrate.py` and `test_lipsync_wav.py`)
are an artefact of running the suite as root, where `chmod 0` does not block reads. They pass as a
normal user. Ruff with a broad rule set reports 62 findings, none of them bugs (details in section 5).

## 0. Status (same day, later)

Boss approved the plan and every step in section 7 is done on this branch, in six commits after
this review, with the suite green after each (775 tests, 2 skipped under root):

1. Tooling: `pyproject.toml` has a `[project]` table and the ruff config; every finding fixed; CI
   runs ruff and pytest on 3.11 and 3.13; the two root-sensitive tests skip under uid 0.
2. The demo is retired. The permission layer of this session refused `git rm`, so the four
   modules sit in `docs/archive/retired-demo/` (unimported, untested) with a README; tag
   `pre-show-control-demo` marks the last commit with them live. Delete the directory at will.
3. `jack/application/config.py` (`AppConfig`, `ConfigError`), `jack/adapters/system/deployment.py`
   and `jack/adapters/hardware/motor_hats.py` exist; `main.py` is a 140-line composition root and
   nothing imports it.
4. One `MotorDriver` per motor; `LipSync.target()` names the volts only. The hand-over protocol is
   gone. Two behaviour changes rode along, both safer: rest-pulse ticks spend the stall budget for
   every motor (a flickering show command can't stack pulses), and a tripped max hold rests one
   tick before its pulse.
5. `show_commands.py` is `commands.py`, `routes.py` and `osc_protocol.py`; `status()` returns a
   typed `Status` whose `to_json()` is the old document.
6. `logging` everywhere, with `RateLimit` as a filter on the one handler.

Not done, by choice: `pydantic-settings` (a new dependency on the Pi for ~40 lines of parsing that
`config.py` now holds cleanly) and the keyword-only callback signatures in 4.7.

## 1. Verdict

The codebase is in good shape. It is small (about 3,100 lines of production Python, 4,900 of tests),
the hexagonal layering in `SPEC.md` is real and machine-enforced (`tests/test_layers.py`), every
module has a docstring that says what it is for and why, and the hardware is behind two tiny
`Protocol`s so the whole show logic runs on a laptop. Most of the W-PY rules are already met.

There is no Python framework for "animatronic on a Pi with an audio-paced control loop, OSC and
HTTP show control". The candidates that exist (ROS 2, Viam, asyncio-based rewrites, DI containers)
would add weight without removing code. The recommendation is to keep the current architecture and
adopt three targeted tools instead: `ruff` (enforces the rule list), the stdlib `logging` module, and
optionally `pydantic-settings` for `jack.env` parsing. Section 6 has the comparison.

The real wins are structural and local. In order of value:

1. Unify the mouth's two drivers (`MouthController` and `MotorDriver`) so the stall-budget
   hand-over between them disappears.
2. Move configuration parsing out of `main.py` into the package; stop the tools importing `main`.
3. Retire the pre-show-control demo code, whose mouth voltages now contradict `poses.toml`.
4. Split `show_commands.py` into wire-format handling, route parsing, and command application.
5. Give `ControlBoard.status()` a type instead of a dict whose keys are spread over four consumers.

## 2. What is here

```
main.py            composition root + env parsing + retired demo code     (325 lines)
calibrate.py       CLI: drive one motor by hand                            (53)
lipsync_wav.py     CLI: play a WAV through the talk loop                   (105)
jack/show/         pure show logic, no I/O                                 (~1,500)
  audio/           pcm, envelope, frame_queue, lip_sync, talk_settings
  motion/          motors, poses, motor_driver, stall_budget, ramp, mouth, speech
  control/         control_board, show_commands, osc_feedback
jack/application/  ports (Protocols), talk_loop, calibration, smoke_test   (~300)
jack/adapters/     hardware (pca9685, tb6612), audio (alsa, mumble, roc, wav),
                   network (osc, http, control page), system (sd_notify, guard)  (~700)
jack/support/      attempt_all, rate_limited_log                           (~45)
tools/             touchosc_layout (generates touchosc/jack.tosc)          (164)
deploy/            systemd units, install, git-pull updater, health logger
tests/             41 modules, flat, fakes not mocks                         (4,943)
```

Runtime shape: one talk-loop thread paced by ALSA's blocking write (20 ms per tick); OSC, HTTP,
OSC-feedback and roc-recv reader threads, plus pymumble's thread; all meet at `ControlBoard`
(locked) and the `FrameQueue`s (locked). Config comes from `poses.toml` (repo, then `/etc/jack`)
and `JACK_*` environment variables.

## 3. Strengths worth keeping

- **Layering is enforced, not aspirational.** `test_layers.py` parses every module's imports and
  fails on an upward import or on I/O libraries in the pure layers. Keep this test whatever else
  changes.
- **Ports are minimal.** `MotorOutput`, `VoiceSource`, `AudioSink` each have one to three methods.
  Adapters are thin and each is the only importer of its library (`alsaaudio`, `pymumble_py3`,
  `pythonosc`, `smbus2`).
- **Validation at the edge, once.** `poses.py` and `TalkSettings` refuse bad config before any
  motor moves, with messages that name the file and the fix.
- **Tests check sequencing with recording fakes**, not mock call counts, and they cover the
  deploy scripts (`test_install.py`, `test_jack_update.py`, `test_flash_leds.py`) and the systemd
  unit.
- **Safety invariants are explicit**: every exit path brakes (`attempt_all`), stall budgets are
  enforced in the domain, and the dead-man timeout lives in one place.
- **Docstrings explain intent and hardware history** ("Boss, 2026-10-02"), which is exactly what a
  reader of a calibration constant needs.

## 4. Findings: readability, composability, separation of concerns

Ordered by expected payoff.

### 4.1 The mouth has two drivers and a hand-over protocol between them

`MouthController` (`jack/show/audio/lip_sync.py`) and `MotorDriver`
(`jack/show/motion/motor_driver.py`) each own: a slew-per-tick, a rest-pulse countdown, a
`StallBudget`, a `_close`/`_to_rest` method, and a "budget spent" property. Because the mouth has
both, `talk_loop.py` has to hand the budget back and forth on every mode switch
(`drivers["mouth"].resume_from(...)`, `MouthController(..., drivers["mouth"].budget_spent)`), and
`StallBudget.take_over` exists only for that.

Proposed shape: lip sync produces a *target voltage* from the level (gain, cap, "waiting for
quiet"); one `MotorDriver` per motor, the mouth included, turns a target into a drive (slew, budget,
rest pulse, brake/coast). The loop becomes "pick a target source for the mouth, then update every
driver". The hand-over, `resume_from`, `take_over` and `budget_spent` all go away, and a mode switch
is just "the mouth's target comes from somewhere else now".

Care point: the two slew rules differ today. `MotorDriver._slewed` lets a drive ease off
instantly and slew-limits driving harder; `MouthController` slew-limits opening and closes
instantly. Those are the same rule once "harder" is read as "further from rest", so one
implementation should cover both. The lip-sync tests (`test_lip_sync.py`, 176 lines) are the spec
to preserve while doing it.

### 4.2 `main.py` is three modules

`main.py` holds (a) configuration parsing from the environment (`talk_settings`,
`show_control_config`, `roc_config`, `_env_number`, `_env_port`, the two migration guards
`MOVED_TO_POSES` and `REPLACED_BY_MOUTH_GAIN`), (b) the composition root (`build_motors`,
`start_roc_voice`, `main`), and (c) retired demo code (`MOUTH_DEMO`, `speaking_cycle`,
`demo_cycle`, `_mouth_only`). `calibrate.py` and `lipsync_wav.py` then `from main import ...` to
reach constants and `build_motors`, so the app's entry script is also a library for the tools.

Suggested split:

- `jack/application/config.py`: the `*Config` dataclasses, the env readers, and the migration
  guards. Pure, testable, and the place `SETTING_ENV_PREFIX` belongs. Return typed values; today
  `_env_number(..., default: float)` is typed as returning `float | None`, which forces every
  caller to pretend a default might not apply.
- `jack/application/wiring.py` (or `jack/app.py`): `build_motors`, `start_roc_voice`, and the
  constants the tools share (`I2C_BUS`, `PWM_FREQ_HZ`, `SUPPLY_VOLTS`, `ALSA_DEVICE`, `POSES_PATHS`).
  `build_motors(bus)` should take the `I2CBus` protocol, not an untyped `bus`.
- `main.py` shrinks to signal handling plus `main()`.

The migration guards (variables that moved to `poses.toml` or were replaced by the mouth gain)
are policy about old `/etc/jack/jack.env` files, with no expiry. Give them a dated comment and a
removal plan, or move them to `install.sh`, which already edits that file.

### 4.3 Retired code is still live and now wrong

`SPEC.md` records that `run_profiles_loop`, `MOUTH_DEMO`, `speaking_cycle` and `demo_cycle` stay in
the repo by Boss's decision (2026-09-28). Since then the mouth was recalibrated with `sign = 1`
("positive volts open the mouth", `poses.toml` and the README), but `jack/show/motion/mouth.py`
still documents and encodes "fully open holds only while -6 V is applied", and `speech.py` builds
phrases from those voltages. A reader comparing the two files will trust the wrong one.

Affected: `mouth.py` (`CLOSE`, `RELAX`, `open_fully`, `OPEN_RAMP_S`), `speech.py`,
`application/smoke_test.py`, `ramp.py`'s `ramp_profile` and `square_profile`, the four demo
symbols in `main.py`, and about 400 lines of tests for them. `calibration.py` still uses
`mouth.Segment`, `hold`, `ramp` and `describe`, so the segment vocabulary stays; only the stale
constants and the demo players go.

Recommendation: delete, with a tag (for example `pre-show-control-demo`) so it stays reachable.
This reverses a recorded decision, so it needs Boss's say-so. If they stay, at minimum fix the
sign in `mouth.py` and move the players under `tools/` so the package does not carry them.

### 4.4 `show_commands.py` mixes four concerns

At 331 lines it is the largest module and the hardest to read, because one file handles: the
command vocabulary (`SetValue`, `StartPose`, ...), route parsing shared by OSC and HTTP (`_build`),
OSC-specific wire quirks (TouchOSC buttons, release-of-0, whole-number float ports, bundles of
rules in `osc_command`), answering queries and subscriptions (`_answer`), and rate-limited logging.
`osc_command` in particular reassigns `args`, has four early returns, and needs the module
docstring to be understood.

Suggested split, all still in `jack/show/control/`:

- `commands.py`: the dataclasses, `Command`, `CommandError`, `apply`.
- `routes.py`: `_build`, `_argument_names`, `_parts`, `_number`, `_only` (the HTTP path and the
  OSC address both land here).
- `osc_protocol.py`: button and release handling, `_port`, `osc_command`, `handle_osc`, `_answer`,
  `OscContext`.

`http_command` is a one-line alias for `_build`; after the split it can go.

### 4.5 `status()` is an untyped dict with five consumers

`ControlBoard.status()` returns nested dicts. Its keys are read in `osc_feedback.state_messages`,
serialised by `http_server`, pasted into the journal by `deploy/jack-health.sh`, and asserted in
tests. A `Status` / `MotorStatus` pair of frozen dataclasses with a `to_json()` would make the
contract one definition, and let `state_messages` take typed fields instead of `motor.get("command") or {}`.

### 4.6 Logging is a callable passed by hand

Every module that logs takes a `log: Callable[[str], None]` and `main` passes `print`. That keeps
the pure layers free of I/O, but it also means: no levels, no logger names in the journal, a custom
`RateLimitedLog`, and `http_server` writing to `sys.stderr` directly while everything else goes
to stdout. The stdlib `logging` module is just as testable (`caplog`) and is not I/O in the sense
`test_layers.py` guards against. Suggested: one `logging.getLogger("jack.<module>")` per module,
`RateLimitedLog` rewritten as a `logging.Filter`, and `print` reserved for the two CLIs' operator
output. `PYTHONUNBUFFERED=1` in `jack.service` already covers stdout.

### 4.7 Smaller points

- `run_talk_loop` takes 9 positional arguments and `run_calibration` 8. Group the show's parts
  (`motors`, `profiles`, `settings`, `board`, `supply_volts`) into one object, or make the
  callbacks keyword-only.
- `poses.py`'s `_blamed_on` / `_in_file` / `all_files()` context managers exist to prefix errors
  with the file that set a key. It works, but the `_profile` function is 70 lines of `with` blocks.
  A small `Origin` record per key plus one `raise_for(key, message)` helper reads more directly.
  (A pydantic model with field validators would be the library route; see section 6.)
- `TalkSettings.__post_init__` evaluates `self.mouth_lead_ticks` and `self.max_backlog_frames`
  for their side effect (ruff B018). Name the check: `_ = self.mouth_lead_ticks` or a
  `_check_whole_ticks()` method.
- `OscEndpoint.close()` and the `stop` event returned by `start_feedback` are never used by
  `main`; either wire them into the `finally` (symmetry with `roc_voice.close()`) or drop them.
- `MumbleVoice.connected` is written from pymumble's thread and read from the talk loop without
  the lock. Safe in CPython, but it is the one shared field in the class that is not under
  `_lock`; a comment or the lock would stop a reader wondering.
- `after_tail(source, ...)` in `lipsync_wav.py` and `watchdog_noting_mumble(mumble_client, ...)`
  in `main.py` take untyped objects whose one attribute matters (`finished`, `is_alive()`). Tiny
  Protocols document the contract.
- `tests/test_main.py` imports `main` as a module to test config parsing. After 4.2 those tests
  move to `test_config.py` and `main` stops being imported by tests.
- `tests/test_calibrate.py` and `test_lipsync_wav.py`: mark the unreadable-config tests
  `skipif(os.geteuid() == 0)` so the suite is green in a root container.
- No `[project]` table in `pyproject.toml` (no `requires-python`, no name), no linter config, no
  CI. Section 7 proposes the minimum.

## 5. The W-PY rule list, checked

| Rule | Status | Notes |
|---|---|---|
| No bare `except:` | Pass | None. |
| No empty except blocks | Pass | None. |
| No `assert` for validation | Pass | Only a `raise AssertionError("unreachable")` in `poses.py:74`; `RuntimeError` would be the conventional choice. |
| No mutable default arguments | Pass | `environ: Mapping = os.environ` is a module-level mapping used read-only; acceptable, `None` sentinel would be stricter. |
| No wildcard imports | Pass | |
| Public functions annotated | Mostly | 10 arguments and 1 return unannotated (ruff ANN): `connect_mumble` return, `build_motors(bus)`, `AlsaSink(pcm)`, `on_sound(user, chunk)`, `after_tail(source)`, `watchdog_noting_mumble(mumble_client)`, `_duration(segments)`, three in `tools/touchosc_layout.py`. |
| `X \| None`, `list[str]` | Pass | No `Optional`/`List`/`Dict` anywhere. |
| Catch specific exceptions | Pass with 4 exceptions | `except Exception` in `attempt_all`, `http_server._respond`, and twice in `osc_server.start_feedback`; each is a process-boundary catch with a comment. Keep, but mark `# noqa: BLE001` with the reason once ruff is on. |
| `raise X from Y` | Pass | Used consistently, including `from None` where the cause is noise. |
| Custom exceptions from `Exception` | Pass | `CommandError(ValueError)`. |
| Naming | Pass | snake_case / PascalCase / UPPER_SNAKE throughout. |
| Imports grouped stdlib → third-party → local | Partial | Groups are right; the local group is unsorted in 12 files (ruff I001, auto-fixable). |
| Late-binding closures | Pass | The lambdas in `tb6612_motor._release` close over `self` only. |
| `is` only for singletons | Pass | |
| No list modification during iteration | Pass | `_drop_expired` snapshots first; `_current` deletes outside iteration. |
| File encoding specified | N/A | Every open is binary (`rb`, `read_bytes`); `str.encode()` defaults to UTF-8. |
| `setdefault` for accumulation | Pass | `mumble_voice.on_sound`, `poses._merge`. |
| Async: task refs, CancelledError, no blocking | N/A | No asyncio. Threads are daemon threads whose handles are intentionally dropped; `time.sleep(0.005)` is in synchronous driver code. |

Other ruff findings worth fixing: three `zip()` calls without `strict=True` where length
mismatch would be a bug (`smoke_test.py:50`, `pcm.py:35`, `show_commands.py:118`); two
`dict` comprehensions that are `dict.fromkeys`; an unused loop variable in `speech.py:38`;
a Unicode minus sign in a comment in `motors.py:15`.

## 6. Frameworks: options and a recommendation

The search covered animatronics and show-control projects, robotics middleware, OSC libraries,
hardware driver libraries, configuration libraries, and dependency-injection containers.

| Option | What it would replace | Fit | Verdict |
|---|---|---|---|
| **Keep the hand-rolled hexagonal app** | Nothing | The layering, ports and fakes already do what an app framework would do; the codebase is 2.9k lines. | **Recommended.** |
| **ROS 2 (rclpy)** on Jazzy or Lyrical | Threads and `ControlBoard` become nodes and topics; OSC/HTTP become bridge nodes. | Needs Ubuntu 24.04 on the Pi rather than Raspberry Pi OS; the 20 ms audio-paced tick would cross node boundaries and gain jitter; four motors on one board gain nothing from distribution. Kilted support ends November 2026. | Not recommended. |
| **Viam** (viam-server + Python SDK) | Motor drivers and the control API, config-driven. | Cloud-managed machine config and API keys; a show prop should not need the internet to move. Motor API is generic DC-motor, no stall budget or hold semantics. | Not recommended. |
| **EymOS, PyRCF, Meta-ROS** | Middleware / control loop. | EymOS is a small messaging layer; PyRCF's docs say it is for simulation, not real robots; Meta-ROS is a 2026 paper. | Not mature enough. |
| **Hobby projects** (JordiOrriols/animatronic, ChatterPi, Bechele2) | Whole app. | Each is a single-purpose project (WebSocket puppeteering, audio-driven servos, recorded sequences); none is a framework or exposes OSC. Useful as references only. | No. |
| **asyncio rewrite** (python-osc `AsyncIOOSCUDPServer`, `aiohttp`) | The four daemon threads. | ALSA's blocking write paces the loop and pymumble is thread-based, so the loop would still run in an executor; the threads are few and well-contained behind locks. | Not now. Revisit only if more network protocols arrive. |
| **Adafruit CircuitPython `pca9685` + `adafruit_motor` via Blinka** | `pca9685.py`, `tb6612_motor.py` (~150 lines). | Waveshare's board wires each TB6612 channel as PWM + IN1 + IN2, while `adafruit_motor.DCMotor` expects two PWM channels; the `I2CBus` protocol with a recording fake would be lost, and the current driver avoids Waveshare's duty-scaling bug on purpose. | Not worth it. |
| **DI containers** (dependency-injector, lagom, punq, wireup) | `main()`'s wiring. | `main()` is already an explicit, 40-line composition root; a container adds registration without removing anything. | No. |
| **pydantic-settings** | `talk_settings`, `show_control_config`, `roc_config`, `_env_number`, `_env_port` (~120 lines). | Typed env parsing with prefixes and validators is exactly this job, and `TalkSettings` is already a validated dataclass. Cost: a dependency on pydantic on the Pi (Debian ships `python3-pydantic`; check `pydantic-settings` availability under the "everything from apt, pip `--no-deps`" deploy rule). | Optional, good fit. A plain dataclass loader in `config.py` (4.2) gets 80% of the benefit with no dependency. |
| **pydantic for `poses.toml`** | `poses.py`'s hand validation (~200 lines). | Field validators map well, but the "which file set this key" blame messages are custom and worth keeping; a model would need an origin map alongside. | Optional; do 4.7 first. |
| **`logging` (stdlib)** | `log` callables, `RateLimitedLog`, `print`. | See 4.6. | **Recommended.** |
| **ruff** (dev only) | Nothing; enforces this review's rule list. | Already installed on the dev machine; 14 of the 62 findings auto-fix. | **Recommended.** |
| **typer / click** | argparse in the two CLIs. | `lipsync_wav.build_parser` already derives flags from `TalkSettings`; argparse is fine. | No. |

Sources consulted:
[JordiOrriols/animatronic](https://github.com/JordiOrriols/animatronic),
[ChatterPi](https://hackaday.io/project/181612-chatterpi),
[Animatronic control systems overview](https://zappedmyself.com/animatronics/animatronic-control-systems/),
[ROS 2 Kilted Kaiju release notes](https://docs.ros.org/en/kilted/Releases/Release-Kilted-Kaiju.html),
[ROS 2 Kilted announcement](https://www.openrobotics.org/blog/2025/5/23/ros-2-kilted-kaiju-released),
[DigiKey: talking to microcontrollers from ROS 2](https://www.digikey.com/en/maker/tutorials/2025/intro-to-ros-part-12-talking-to-microcontrollers),
[Viam: control a motor](https://docs.viam.com/how-tos/control-motor),
[Viam: develop an app](https://docs.viam.com/how-tos/develop-app),
[Viam: GPIO servo on a Pi](https://www.viam.com/post/gpio-servo-control-raspberry-pi-viam),
[EymOS on PyPI](https://pypi.org/project/eymos/1.0.0),
[PyRCF docs](https://pyrcf.readthedocs.io/en/latest/intro.html),
[Meta-ROS (arXiv)](https://arxiv.org/html/2601.21011v1),
[The Construct: alternatives to ROS](https://www.theconstruct.ai/?p=50112),
[python-osc server docs](https://python-osc.readthedocs.io/en/latest/server.html),
[oscpy on PyPI](https://pypi.org/project/oscpy/0.5.0),
[aiosc on piwheels](https://www.piwheels.org/project/aiosc/),
[Waveshare Motor Driver HAT wiki](https://waveshare.com/wiki/Motor_Driver_HAT),
[BeagleBoard: PCA9685 motor drivers with Adafruit libraries](https://docs.beagleboard.io/boards/beagley/ai/demos/pca9685-motor-drivers.html),
[dependency-injector on PyPI](https://simple-repository.app.cern.ch/project/dependency-injector),
[lagom on PyPI](https://pypi.org/project/lagom/1.0.0b2),
[punq on PyPI](https://pypi.org/project/punq/0.5/),
[wireup on PyPI](https://pypi.org/project/wireup/0.8.1),
[Hexagonal architecture in Python](https://douwevandermeij.medium.com/hexagonal-architecture-in-python-7468c2606b63),
[Pydantic BaseSettings vs Dynaconf](https://leapcell.io/blog/pydantic-basesettings-vs-dynaconf-a-modern-guide-to-application-configuration),
[Typed Settings: why](https://typed-settings.readthedocs.io/en/latest/why.html),
[Python configuration management 2026](https://dasroot.net/posts/2026/01/python-configuration-management-pydantic-settings-dynaconf/).

## 7. Suggested order of work

Each step is independently shippable and keeps the suite green.

1. **Tooling (no behaviour change).** Add a `[tool.ruff]` section to `pyproject.toml` selecting
   `E, W, F, I, B, UP, ANN, BLE, S101, PL, SIM, RUF` with `line-length = 120`, fix the 62
   findings, add `requires-python` and a `[project]` table, add a GitHub Actions workflow that runs
   ruff and pytest on 3.11 and 3.13, and skip the two root-sensitive tests under uid 0.
2. **Retire the demo code** (needs Boss's OK; section 4.3).
3. **Extract `jack/application/config.py`** and the wiring helpers from `main.py`; tools stop
   importing `main` (4.2).
4. **One driver per motor** (4.1). The biggest conceptual simplification; do it with the lip-sync
   tests as the contract.
5. **Split `show_commands.py`** (4.4) and **type `status()`** (4.5).
6. **Adopt `logging`** (4.6), then decide on `pydantic-settings` once `config.py` exists and the
   Pi's package availability is checked.
