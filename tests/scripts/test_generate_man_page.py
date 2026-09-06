"""Tests for scripts/generate-man-page.py.

`click_man` reads `Group.commands` directly, which is empty until the lazy
subcommand tables are materialized (#1305). `render_man_page` therefore has to
call `cli.load_all_commands(ctx)` first; drop that call and the man page still
renders, silently, with no subcommands at all. These tests make that failure
loud.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.cli import _COMMAND_MODULES

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate-man-page.py"


def _load_generator() -> ModuleType:
    """Load generate-man-page.py as a module.

    Returns:
        ModuleType: The loaded generator module.

    Raises:
        RuntimeError: If the script cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location("generate_man_page", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load generate-man-page.py module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rendered() -> str:
    """Render the man page once for the assertions below.

    Returns:
        str: The generated man page text.
    """
    return str(_load_generator().render_man_page(date="2026-01-01"))


@pytest.mark.parametrize("canonical", sorted(_COMMAND_MODULES))
def test_man_page_documents_every_command(canonical: str, rendered: str) -> None:
    """Every lazily loaded subcommand reaches the generated man page.

    Args:
        canonical: Canonical command name under test.
        rendered: Rendered man page fixture.
    """
    # click-man renders one cross-reference per subcommand; the whole set is
    # present only when `load_all_commands` materialized the lazy tables.
    assert_that(rendered).contains(f"lintro-{canonical}(1)")


def test_man_page_carries_the_version(rendered: str) -> None:
    """The generator still stamps the package version it is given.

    Args:
        rendered: Rendered man page fixture.
    """
    from lintro import __version__

    assert_that(rendered).contains(__version__)
