"""Tests for the Nuitka bytecode user plugin and its wiring (#2484).

Nuitka lives in the ``build`` dependency group, which the CI test jobs do not
install, so everything here runs against the plugin's plain-string selection
logic. The single test that needs the real Nuitka ``ModuleName`` type skips
when the build group is absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_DIR = _REPO_ROOT / "scripts" / "build"
_PLUGIN_PATH = _BUILD_DIR / "nuitka_bytecode_plugin.py"


def _load(*, name: str, path: Path) -> ModuleType:
    """Load a build script as a module without running its entry point.

    Args:
        name: Module name to register under.
        path: Path to the script.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: If the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin_module() -> ModuleType:
    """Load the Nuitka user plugin.

    Importable with or without Nuitka installed; the plugin falls back to an
    ``object`` base class when Nuitka is absent.

    Returns:
        The loaded plugin module.
    """
    return _load(name="nuitka_bytecode_plugin", path=_PLUGIN_PATH)


def test_pygments_is_the_configured_bytecode_namespace(
    plugin_module: ModuleType,
) -> None:
    """The namespace list is what the C-unit audit identified.

    Args:
        plugin_module: The loaded plugin module.
    """
    assert_that(plugin_module.BYTECODE_NAMESPACES).is_equal_to(("pygments",))


@pytest.mark.parametrize(
    "module_name",
    ["pygments", "pygments.lexers", "pygments.lexers.python"],
)
def test_configured_namespaces_and_their_children_match(
    plugin_module: ModuleType,
    module_name: str,
) -> None:
    """A configured namespace and every module under it is selected.

    Args:
        plugin_module: The loaded plugin module.
        module_name: Dotted module name expected to match.
    """
    assert_that(plugin_module.is_bytecode_namespace(module_name)).is_true()


@pytest.mark.parametrize(
    "module_name",
    ["lintro", "lintro.cli", "pygmentsfoo", "pygmentsfoo.bar", "httpx", ""],
)
def test_other_modules_do_not_match(
    plugin_module: ModuleType,
    module_name: str,
) -> None:
    """Matching is on dotted boundaries, not a bare prefix.

    Args:
        plugin_module: The loaded plugin module.
        module_name: Dotted module name expected not to match.
    """
    assert_that(plugin_module.is_bytecode_namespace(module_name)).is_false()


def test_the_namespace_list_is_injectable(plugin_module: ModuleType) -> None:
    """A caller-supplied namespace tuple overrides the default.

    Args:
        plugin_module: The loaded plugin module.
    """
    assert_that(
        plugin_module.is_bytecode_namespace("rich.syntax", namespaces=("rich",)),
    ).is_true()
    assert_that(
        plugin_module.is_bytecode_namespace("pygments", namespaces=("rich",)),
    ).is_false()


def test_plugin_hook_returns_bytecode_for_a_configured_namespace(
    plugin_module: ModuleType,
) -> None:
    """``decideCompilation`` maps the selection onto Nuitka's hook contract.

    Args:
        plugin_module: The loaded plugin module.
    """
    plugin = plugin_module.NuitkaPluginLintroBytecode()
    assert_that(plugin.decideCompilation("pygments.lexers.python")).is_equal_to(
        "bytecode",
    )
    assert_that(plugin.decideCompilation("lintro.cli")).is_none()
    assert_that(plugin.isAlwaysEnabled()).is_true()


def test_plugin_hook_accepts_a_real_nuitka_module_name(
    plugin_module: ModuleType,
) -> None:
    """The hook works on Nuitka's own ``ModuleName``, not just ``str``.

    Skipped where the ``build`` dependency group is not installed; the CI test
    jobs install only the dev group.

    Args:
        plugin_module: The loaded plugin module.
    """
    pytest.importorskip("nuitka", reason="nuitka lives in the build group")
    from nuitka.utils.ModuleNames import ModuleName

    plugin = plugin_module.NuitkaPluginLintroBytecode()
    assert_that(
        plugin.decideCompilation(ModuleName("pygments.lexers.python")),
    ).is_equal_to("bytecode")
    assert_that(plugin.decideCompilation(ModuleName("lintro.cli"))).is_none()


@pytest.mark.parametrize(
    ("module_name", "script_name"),
    [
        ("build_macos", "build_macos.py"),
        ("build_linux", "build_linux.py"),
    ],
)
def test_build_commands_wire_the_plugin_and_drop_the_httpx_cli(
    module_name: str,
    script_name: str,
) -> None:
    """Both platform builds pass the plugin and skip ``httpx._main``.

    Args:
        module_name: Module name to load the script under.
        script_name: File name of the build script.
    """
    module = _load(name=module_name, path=_BUILD_DIR / script_name)
    with patch.object(Path, "exists", return_value=True):
        cmd = (
            module.build_nuitka_command(arch="arm64")
            if module_name == "build_macos"
            else module.build_nuitka_command()
        )
    assert_that(cmd).contains(f"--user-plugin={module.BYTECODE_PLUGIN}")
    assert_that(cmd).contains("--nofollow-import-to=httpx._main")
    # The library itself stays in the closure; only its CLI entry point goes.
    assert_that(cmd).contains("--include-package=httpx")
    assert_that(module.BYTECODE_PLUGIN.is_file()).is_true()
