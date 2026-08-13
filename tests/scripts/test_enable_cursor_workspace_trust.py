"""Tests for ``scripts/ci/enable_cursor_workspace_trust.py``.

The dogfood job has to set ``ai.cursor_trust_workspace`` on the ephemeral
checkout: the Cursor ``agent`` CLI will not start non-interactively without
``--trust``, and there is no env overlay for that field.
"""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - subprocess drives the script under test; shell=False
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "enable_cursor_workspace_trust.py"
PROJECT_CONFIG = REPO_ROOT / ".lintro-config.yaml"


@pytest.fixture
def trust_module() -> ModuleType:
    """Load the patcher as an importable module.

    Returns:
        The loaded module exposing ``enable_cursor_workspace_trust``.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(
        "enable_cursor_workspace_trust",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["enable_cursor_workspace_trust"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_config_does_not_grant_workspace_trust() -> None:
    """Local checkouts keep the Cursor ``--trust`` opt-in off."""
    text = PROJECT_CONFIG.read_text(encoding="utf-8")
    assert_that(text).does_not_contain("cursor_trust_workspace: true")


def test_inserts_trust_under_ai_header(*, trust_module: ModuleType) -> None:
    """A config with ``ai:`` and no trust key gains the opt-in."""
    original = "ai:\n  enabled: false\n  review: true\n"
    updated = trust_module.enable_cursor_workspace_trust(text=original)
    assert_that(updated).contains("ai:\n  cursor_trust_workspace: true\n")
    assert_that(updated).contains("  enabled: false\n")


def test_flips_explicit_false(*, trust_module: ModuleType) -> None:
    """An explicit ``false`` in the committed shape is turned on for CI."""
    original = "ai:\n  enabled: false\n  cursor_trust_workspace: false\n"
    updated = trust_module.enable_cursor_workspace_trust(text=original)
    assert_that(updated).contains("  cursor_trust_workspace: true\n")
    assert_that(updated).does_not_contain("cursor_trust_workspace: false")


def test_already_true_is_unchanged(*, trust_module: ModuleType) -> None:
    """A second run must not duplicate the key."""
    original = "ai:\n  cursor_trust_workspace: true\n  enabled: false\n"
    updated = trust_module.enable_cursor_workspace_trust(text=original)
    assert_that(updated).is_equal_to(original)


def test_missing_ai_section_fails(*, trust_module: ModuleType) -> None:
    """No silent no-op when the document has no ``ai:`` mapping."""
    assert_that(trust_module.enable_cursor_workspace_trust).raises(
        ValueError,
    ).when_called_with(text="review:\n  depth: 1\n")


def test_cli_patches_file_in_place(tmp_path: Path) -> None:
    """The script writes the opt-in into ``--config`` and exits 0.

    Args:
        tmp_path: Temporary directory holding a fake config file.
    """
    config = tmp_path / ".lintro-config.yaml"
    config.write_text("ai:\n  enabled: false\n", encoding="utf-8")
    result = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [sys.executable, str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(config.read_text(encoding="utf-8")).contains(
        "cursor_trust_workspace: true",
    )
