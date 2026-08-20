"""Tests for scripts/generate-man-page.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import click
import pytest
from assertpy import assert_that

from lintro.cli import cli

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate-man-page.py"
_REQUIRED_COMMANDS: tuple[str, ...] = ("check", "format", "list-tools", "versions")


def _load_module() -> ModuleType:
    """Load generate-man-page.py as an importable test module.

    Returns:
        The loaded generator module.

    Raises:
        RuntimeError: If the module spec or loader cannot be resolved.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_man_page_script",
        _SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Provide the loaded man-page generator module.

    Returns:
        The loaded generator module.
    """
    return _load_module()


def test_populate_lazy_commands_fills_group_commands(module: ModuleType) -> None:
    """Force-loading lazy subcommands populates ``cli.commands``.

    Args:
        module: Loaded generate-man-page module.
    """
    ctx = click.Context(cli, info_name="lintro")
    module.populate_lazy_commands(ctx)
    for name in _REQUIRED_COMMANDS:
        assert_that(cli.commands).contains_key(name)
        assert_that(cli.list_commands(ctx)).contains(name)
        loaded = cli.get_command(ctx, name)
        assert loaded is not None
        assert_that(loaded.name).is_equal_to(name)


def test_render_man_page_includes_canonical_commands(module: ModuleType) -> None:
    """The rendered man page lists canonical commands such as check and format.

    Args:
        module: Loaded generate-man-page module.
    """
    text = module.render_man_page()
    assert_that(text).contains(".SH COMMANDS")
    for name in _REQUIRED_COMMANDS:
        assert_that(text).contains(name)
        assert_that(cli.commands).contains_key(name)
