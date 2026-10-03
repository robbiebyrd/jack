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
IO_LIBRARIES = {
    "smbus2", "alsaaudio", "pymumble_py3", "pythonosc", "socket", "socketserver", "http", "subprocess", "wave",
}
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
