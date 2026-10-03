"""Imports only point down a layer, and the pure layers do no I/O (SPEC.md "Package layout")."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "jack"
PACKAGE_INIT = PACKAGE / "__init__.py"
# Layer -> the layers its modules may import from.
ALLOWED = {
    "show": {"show", "support"},
    "application": {"show", "application", "support"},
    "adapters": {"show", "application", "adapters", "support"},
    "support": {"support"},
}
# What the pure layers (everything but adapters/) may import: none of it reaches hardware, the network,
# the sound card, files or other processes. A new import fails here until someone decides it belongs.
PURE_IMPORTS = {
    "array", "collections", "contextlib", "dataclasses", "enum", "itertools", "math", "random", "threading", "typing",
    "jack",
}
# poses.py reads its own TOML files (the spec's one exception).
POSES = PACKAGE / "show" / "motion" / "poses.py"
POSES_IMPORTS = {"pathlib", "tomllib"}
MODULES = sorted(PACKAGE.rglob("*.py"))
LAYERED = [path for path in MODULES if path != PACKAGE_INIT]
PURE = [path for path in LAYERED if path.relative_to(PACKAGE).parts[0] != "adapters"]


def layer(path: Path) -> str:
    return path.relative_to(PACKAGE).parts[0]


def module_id(path: Path) -> str:
    return str(path.relative_to(PACKAGE))


def imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    return [node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.Import | ast.ImportFrom)]


def imported_modules(path: Path) -> list[str]:
    names = []
    for node in imports(path):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        else:
            names.append(node.module or "")
    return names


def test_the_package_has_every_layer_and_nothing_else():
    assert {layer(path) for path in LAYERED} == set(ALLOWED)


def test_only_the_package_init_sits_directly_in_jack():
    assert [path for path in MODULES if path.parent == PACKAGE] == [PACKAGE_INIT]


@pytest.mark.parametrize("path", MODULES, ids=module_id)
def test_imports_use_full_paths(path):
    relative = [node.lineno for node in imports(path) if isinstance(node, ast.ImportFrom) and node.level > 0]
    assert not relative, f"{module_id(path)} has relative imports on lines {relative}"


@pytest.mark.parametrize("path", LAYERED, ids=module_id)
def test_imports_only_point_down_a_layer(path):
    for name in imported_modules(path):
        parts = name.split(".")
        if parts[0] == "jack":
            assert len(parts) > 1 and parts[1] in ALLOWED[layer(path)], f"{module_id(path)} imports {name}"


@pytest.mark.parametrize("path", PURE, ids=module_id)
def test_pure_layers_import_only_what_does_no_io(path):
    allowed = PURE_IMPORTS | (POSES_IMPORTS if path == POSES else set())
    for name in imported_modules(path):
        assert name.split(".")[0] in allowed, f"{module_id(path)} imports {name}"


@pytest.mark.parametrize("path", PURE, ids=module_id)
def test_pure_layers_open_no_files(path):
    calls = [
        node.lineno for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert not calls, f"{module_id(path)} calls open() on lines {calls}"
