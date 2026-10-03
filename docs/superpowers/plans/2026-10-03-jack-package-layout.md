# `jack/` Package Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `motor_test` package to `jack` and group its modules by hexagonal layer, then by function, with a test that keeps imports pointing down a layer.

**Architecture:** A pure move: every module keeps its name and contents, only its directory and the import paths that name it change. One scripted pass does the `git mv`s and rewrites every `motor_test` import and path; a new `tests/test_layers.py` reads each module's imports with `ast` and fails on any that points up a layer or pulls I/O into the pure layers. Docs follow in a second task.

**Tech Stack:** Python 3.13, pytest, `ast`. Run tests with `.venv/bin/python -m pytest -q -W error` from the repo root (`/Users/robbiebyrd/jack`).

**Spec:** `SPEC.md`, "Code structure (hexagonal)" → "Package layout" (commits 817ee8e, c7853e6).

## Global Constraints

- Work directly on `main`; no branches or worktrees. Other sessions may share the checkout: stage only the files a task names or moves (never `git add -A`; `.DS_Store`, `.vscode/`, `commands-to-run.txt` are not ours).
- Do not push. Pushing deploys to the Pi.
- The layout, verbatim from the spec:
  ```
  jack/
    show/
      audio/      pcm, envelope, frame_queue, lip_sync, talk_settings
      motion/     motors, poses, motor_driver, stall_budget, ramp, mouth, speech
      control/    control_board, show_commands, osc_feedback
    application/  ports, talk_loop, calibration, smoke_test
    adapters/
      hardware/   pca9685, tb6612_motor
      audio/      alsa_sink, mumble_voice, wav_source
      network/    osc_server, http_server, control_page.html
      system/     systemd_notify, service_guard
    support/      attempt_all, rate_limited_log
  ```
- Import rule (spec): `show/` imports only `show/` and `support/`; `application/` adds `show/`; `adapters/` may import any layer; `support/` imports only `support/`.
- `show/` has no hardware, network, sound card or systemd; `show/motion/poses.py` may read its TOML files.
- Every directory is a package with an empty `__init__.py`. Imports use full paths, e.g. `from jack.show.motion.poses import load_profiles`.
- `main.py`, `calibrate.py`, `lipsync_wav.py`, `poses.toml`, `tools/` stay at the repo root; tests stay flat in `tests/`.
- Files move with `git mv`. Plans under `docs/` keep their `motor_test` paths.
- Module contents don't change beyond import lines and path mentions in docstrings/comments. Import order within a file is left as rewritten (no import sorter is configured).

## Review Focus

- A leftover `motor_test/__pycache__` (untracked) would keep `import motor_test.x` working locally and hide a missed rewrite: Task 1 deletes it, and its final check greps for any `motor_test` left in code (Task 1, Step 6).
- Imports inside functions (e.g. `tools/touchosc_layout.py:142`, `tests/test_touchosc_layout.py:95`) are indented, so a rewrite anchored at line start would miss them: the rewrite is unanchored, and the grep check catches any miss (Task 1).
- `from motor_test import http_server` / `from motor_test import service_guard` (package-level imports) need the parent package, not the module, rewritten: the script handles that form explicitly (Task 1).
- `control_page.html` is found relative to `http_server.py`'s own directory: it must move with it, and `tests/test_http_server.py` serving the page proves it (Task 1, Step 5).
- The Pi runs `/opt/jack/main.py` as the `jack` user; after a deploy, the untracked `/opt/jack/motor_test/__pycache__` stays behind but is never imported. Confirmed by the post-push journal check (Task 2, Step 4), done only when Boss asks for the push.

---

### Task 1: Move the modules and enforce the layers

**Files:**
- Create: `tests/test_layers.py`
- Create: `jack/__init__.py`, `jack/show/__init__.py`, `jack/show/audio/__init__.py`, `jack/show/motion/__init__.py`, `jack/show/control/__init__.py`, `jack/application/__init__.py`, `jack/adapters/__init__.py`, `jack/adapters/hardware/__init__.py`, `jack/adapters/audio/__init__.py`, `jack/adapters/network/__init__.py`, `jack/adapters/system/__init__.py`, `jack/support/__init__.py` (all empty)
- Move: every file in `motor_test/` per the layout; delete `motor_test/__init__.py`
- Modify (import lines and path mentions only): `main.py`, `calibrate.py`, `lipsync_wav.py`, `tools/touchosc_layout.py`, every `tests/*.py` that imports `motor_test`, and the moved modules themselves

**Interfaces:**
- Produces: the module paths in the layout above; `tests/test_layers.py`.

- [ ] **Step 1: Write the layer test**

Create `tests/test_layers.py`:

```python
"""Imports only point down a layer, and the pure layers do no I/O (SPEC.md "Package layout")."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "jack"
# Layer -> the layers its modules may import from.
ALLOWED = {
    "show": {"show", "support"},
    "application": {"show", "application", "support"},
    "adapters": {"show", "application", "adapters", "support"},
    "support": {"support"},
}
# Libraries that reach hardware, the network, the sound card or other processes: adapters only.
IO_LIBRARIES = {"smbus2", "alsaaudio", "pymumble_py3", "pythonosc", "socket", "socketserver", "http", "subprocess", "wave"}
MODULES = sorted(PACKAGE.rglob("*.py"))


def layer(path: Path) -> str:
    return path.relative_to(PACKAGE).parts[0]


def imported_modules(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_the_package_has_every_layer_and_nothing_else():
    assert {layer(path) for path in MODULES if path.parent != PACKAGE} == set(ALLOWED)


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_imports_only_point_down_a_layer(path):
    if path.parent == PACKAGE:
        return  # jack/__init__.py
    for name in imported_modules(path):
        parts = name.split(".")
        if parts[0] == "jack":
            assert parts[1] in ALLOWED[layer(path)], f"{path.relative_to(PACKAGE)} imports {name}"


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_only_adapters_do_io(path):
    if path.parent == PACKAGE or layer(path) == "adapters":
        return
    for name in imported_modules(path):
        assert name.split(".")[0] not in IO_LIBRARIES, f"{path.relative_to(PACKAGE)} imports {name}"
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_layers.py`
Expected: `test_the_package_has_every_layer_and_nothing_else` FAILS (no `jack/` package yet: the set of layers is empty); the parametrized tests are skipped for an empty parameter set.

- [ ] **Step 3: Move the modules and rewrite every reference**

Run this from the repo root (it is a one-off; don't commit it):

```bash
.venv/bin/python - <<'EOF'
import re
import subprocess
from pathlib import Path

LAYOUT = {
    "show/audio": "pcm envelope frame_queue lip_sync talk_settings",
    "show/motion": "motors poses motor_driver stall_budget ramp mouth speech",
    "show/control": "control_board show_commands osc_feedback",
    "application": "ports talk_loop calibration smoke_test",
    "adapters/hardware": "pca9685 tb6612_motor",
    "adapters/audio": "alsa_sink mumble_voice wav_source",
    "adapters/network": "osc_server http_server",
    "adapters/system": "systemd_notify service_guard",
    "support": "attempt_all rate_limited_log",
}
HOME = {module: directory for directory, modules in LAYOUT.items() for module in modules.split()}


def git(*args):
    subprocess.run(["git", *args], check=True)


# Packages, each with an empty __init__.py.
packages = {"jack"} | {f"jack/{directory}" for directory in LAYOUT} | {"jack/show", "jack/adapters"}
for package in sorted(packages):
    Path(package).mkdir(parents=True, exist_ok=True)
    Path(package, "__init__.py").touch()
    git("add", f"{package}/__init__.py")

# Moves.
leftover = sorted(path.stem for path in Path("motor_test").glob("*.py"))
assert set(leftover) - {"__init__"} == set(HOME), set(leftover) ^ set(HOME)
for module, directory in HOME.items():
    git("mv", f"motor_test/{module}.py", f"jack/{directory}/{module}.py")
git("mv", "motor_test/control_page.html", "jack/adapters/network/control_page.html")
git("rm", "-q", "motor_test/__init__.py")
subprocess.run(["rm", "-rf", "motor_test"], check=True)  # only an untracked __pycache__ is left


def dotted(module):
    return "jack." + HOME[module].replace("/", ".")


def rewrite(text):
    text = re.sub(r"from motor_test\.(\w+) import", lambda m: f"from {dotted(m[1])}.{m[1]} import", text)
    text = re.sub(r"from motor_test import (\w+)\b", lambda m: f"from {dotted(m[1])} import {m[1]}", text)
    text = re.sub(r"motor_test/(\w+)\.(py|html)",
                  lambda m: f"jack/{HOME[m[1] if m[1] != 'control_page' else 'http_server']}/{m[1]}.{m[2]}", text)
    return text


tracked = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True, check=True).stdout.split()
for name in tracked:
    path = Path(name)
    if not path.exists() or name.startswith("docs/"):
        continue
    old = path.read_text()
    new = rewrite(old)
    if new != old:
        path.write_text(new)
        print("rewrote", name)
EOF
```

Expected: the moves run without error and it prints `rewrote ...` for `main.py`, `calibrate.py`, `lipsync_wav.py`, `tools/touchosc_layout.py`, the importing tests, and the moved modules.

- [ ] **Step 4: Run the layer test**

Run: `.venv/bin/python -m pytest -q -W error tests/test_layers.py`
Expected: all PASS (one per module for each parametrized test, plus the layer-set test).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: all PASS — the 1623 existing tests plus the new layer tests. `tests/test_http_server.py` serving `/` proves `control_page.html` moved with `http_server.py`.

- [ ] **Step 6: Check nothing still names `motor_test` in code**

Run: `rg -n "motor_test" --glob '!docs/**' --glob '!SPEC.md' --glob '!README.md' --glob '!TODO.md' . ; ls motor_test 2>&1`
Expected: no matches, and `ls: motor_test: No such file or directory`.

Also run the three entry scripts' help to prove they import: `.venv/bin/python calibrate.py --help >/dev/null && .venv/bin/python lipsync_wav.py --help >/dev/null && .venv/bin/python -m tools.touchosc_layout --help >/dev/null && echo ok`
Expected: `ok`. (`main.py` needs the Pi's hardware and is covered by `tests/test_main.py`.)

- [ ] **Step 7: Commit**

```bash
git add tests/test_layers.py jack main.py calibrate.py lipsync_wav.py tools/touchosc_layout.py tests
git status --short   # only the moves, the new __init__.py files, test_layers.py and import rewrites; nothing of .DS_Store/.vscode/commands-to-run.txt
git commit -m "Rename motor_test to jack, grouped by hexagonal layer and function"
```

---

### Task 2: Docs use the new paths

**Files:**
- Modify: `SPEC.md`, `README.md`, `TODO.md`

**Interfaces:**
- Consumes: the layout from Task 1.

- [ ] **Step 1: Rewrite the paths**

Run from the repo root:

```bash
.venv/bin/python - <<'EOF'
import re
from pathlib import Path

LAYOUT = {
    "show/audio": "pcm envelope frame_queue lip_sync talk_settings",
    "show/motion": "motors poses motor_driver stall_budget ramp mouth speech",
    "show/control": "control_board show_commands osc_feedback",
    "application": "ports talk_loop calibration smoke_test",
    "adapters/hardware": "pca9685 tb6612_motor",
    "adapters/audio": "alsa_sink mumble_voice wav_source",
    "adapters/network": "osc_server http_server control_page",
    "adapters/system": "systemd_notify service_guard",
    "support": "attempt_all rate_limited_log",
}
HOME = {module: directory for directory, modules in LAYOUT.items() for module in modules.split()}
for name in ("SPEC.md", "README.md", "TODO.md"):
    path = Path(name)
    old = path.read_text()
    new = re.sub(r"motor_test/(\w+)\.(py|html)", lambda m: f"jack/{HOME[m[1]]}/{m[1]}.{m[2]}", old)
    if new != old:
        path.write_text(new)
        print("rewrote", name)
EOF
rg -n "motor_test" SPEC.md README.md TODO.md
```

Expected: the only remaining match is SPEC.md's "Package layout" line `` `jack/` (formerly `motor_test/`) ``. Any other match (e.g. a bare `motor_test` package mention) is reworded by hand to name `jack` or the specific new path.

- [ ] **Step 2: Read the SPEC's "Code structure" section once through**

Run: `rg -n "jack/(show|application|adapters|support)" SPEC.md | head -40`
Expected: each module's description names its new path. Check by eye that no sentence still describes a module as living in the wrong layer (e.g. `talk_settings` as application).

- [ ] **Step 3: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: all PASS (docs-only change; this proves nothing else moved).

```bash
git add SPEC.md README.md TODO.md
git commit -m "Docs name the jack/ package's new module paths"
```

- [ ] **Step 4 (only after Boss asks for the push): confirm the Pi runs the new layout**

After `git push origin main`, wait for the deploy, then:
Run: `ssh 10.10.0.54 'git -c safe.directory=/opt/jack -C /opt/jack log --oneline -1; systemctl is-active jack; journalctl -u jack --since "-2min" --no-pager | tail -3'`
Expected: the new commit, `active`, and the `Talking: ...` startup line with no traceback.
