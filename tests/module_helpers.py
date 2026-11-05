"""Helpers for executing a source file as a fresh, test-scoped module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_fresh_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    path: Path | str,
    name: str,
) -> ModuleType:
    """Execute *path* as a new module, registered as *name* for one test.

    The module body runs again, as a first import does, so the copy carries
    its shipped defaults even when the imported module has been patched.
    It is registered in ``sys.modules`` before execution (a dataclass in the
    file resolves its module through it), and ``monkeypatch`` restores the
    previous entry on teardown, so nothing leaks into the next test.

    Args:
        monkeypatch: Pytest fixture that unregisters the module on teardown.
        path: Source file to execute.
        name: Module name to register it under.

    Returns:
        ModuleType: The freshly executed module.

    Raises:
        RuntimeError: When no loader can be built for *path*.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module
