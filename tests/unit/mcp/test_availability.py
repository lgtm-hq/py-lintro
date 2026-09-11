"""Unit tests for optional MCP SDK availability helpers."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click import UsageError

import lintro
from lintro.mcp import is_mcp_available, require_mcp, spec_is_lintro_subpackage

_LINTRO_DIR = Path(lintro.__file__).resolve().parent
_OWN_MCP_INIT = _LINTRO_DIR / "mcp" / "__init__.py"


def _package_spec(init_file: Path) -> ModuleSpec:
    """Build a top-level ``mcp`` package spec rooted at ``init_file``.

    Args:
        init_file: The ``__init__.py`` the spec should point at.

    Returns:
        A spec shaped like ``find_spec("mcp")`` would return for it.
    """
    spec = importlib.util.spec_from_file_location(
        "mcp",
        init_file,
        submodule_search_locations=[str(init_file.parent)],
    )
    assert spec is not None  # narrow type for mypy
    return spec


@pytest.fixture
def fake_sdk(tmp_path: Path) -> Path:
    """Write a stand-in top-level ``mcp`` package outside lintro.

    Args:
        tmp_path: Per-test directory.

    Returns:
        The package's ``__init__.py``.
    """
    package = tmp_path / "site-packages" / "mcp"
    package.mkdir(parents=True)
    init_file = package / "__init__.py"
    init_file.write_text("", encoding="utf-8")
    return init_file


def test_is_mcp_available_true_when_spec_found(fake_sdk: Path) -> None:
    """Availability is true when a module spec for the SDK is located."""
    with patch("importlib.util.find_spec", return_value=_package_spec(fake_sdk)):
        assert_that(is_mcp_available()).is_true()


def test_is_mcp_available_false_when_spec_missing() -> None:
    """Availability is false when no spec can be located."""
    with patch("importlib.util.find_spec", return_value=None):
        assert_that(is_mcp_available()).is_false()


@pytest.mark.parametrize("error", [ImportError("broken"), ValueError("no spec")])
def test_is_mcp_available_false_on_probe_failure(error: Exception) -> None:
    """A broken or half-installed SDK reports unavailable, never raises."""
    with patch("importlib.util.find_spec", side_effect=error):
        assert_that(is_mcp_available()).is_false()


def test_is_mcp_available_does_not_import_the_sdk(fake_sdk: Path) -> None:
    """The probe locates a spec rather than executing the package."""
    with (
        patch("importlib.util.find_spec", return_value=_package_spec(fake_sdk)),
        patch("builtins.__import__", side_effect=AssertionError("imported mcp")),
    ):
        assert_that(is_mcp_available()).is_true()


def test_is_mcp_available_rejects_lintros_own_mcp_subpackage() -> None:
    """A spec resolving to ``lintro/mcp`` is the shadowing package, not the SDK.

    That is what ``find_spec("mcp")`` returns inside a binary whose import
    root is ``lintro/`` (#2577); reporting it as the SDK let ``doctor`` claim
    MCP was available while ``lintro mcp`` crashed.
    """
    with patch(
        "importlib.util.find_spec",
        return_value=_package_spec(_OWN_MCP_INIT),
    ):
        assert_that(is_mcp_available()).is_false()


def test_is_mcp_available_rejects_a_real_shadowing_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``lintro/`` first on ``sys.path`` the live lookup finds itself.

    No stubbing: the real import machinery is asked for ``mcp`` with lintro's
    package directory ahead of site-packages, which is the frozen layout the
    old file-mode build produced, and the probe must still say unavailable.

    Args:
        monkeypatch: Restores ``sys.path`` and ``sys.modules`` afterwards.
    """
    for name in [key for key in sys.modules if key == "mcp" or key.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(_LINTRO_DIR))
    importlib.invalidate_caches()

    found = importlib.util.find_spec("mcp")
    assert found is not None  # narrow type for mypy
    assert_that(found.origin).is_equal_to(str(_OWN_MCP_INIT))
    assert_that(is_mcp_available()).is_false()


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (_package_spec(_OWN_MCP_INIT), True),
        (ModuleSpec("mcp", None, origin="built-in"), False),
        (ModuleSpec("mcp", None), False),
    ],
    ids=["own-subpackage", "built-in", "no-origin"],
)
def test_spec_is_lintro_subpackage_classifies_locations(
    spec: ModuleSpec,
    expected: bool,
) -> None:
    """Only a spec located inside lintro's package is flagged.

    Args:
        spec: The candidate spec.
        expected: Whether it should be treated as lintro's own subpackage.
    """
    assert_that(spec_is_lintro_subpackage(spec)).is_equal_to(expected)


def test_spec_is_lintro_subpackage_checks_search_locations(tmp_path: Path) -> None:
    """A namespace-style spec with no origin is judged by its package directory."""
    inside = ModuleSpec("mcp", None)
    inside.submodule_search_locations = [str(_LINTRO_DIR / "mcp")]
    outside = ModuleSpec("mcp", None)
    outside.submodule_search_locations = [str(tmp_path / "mcp")]

    assert_that(spec_is_lintro_subpackage(inside)).is_true()
    assert_that(spec_is_lintro_subpackage(outside)).is_false()


def test_require_mcp_is_quiet_when_available() -> None:
    """require_mcp returns without raising when the SDK is present."""
    with patch("lintro.mcp.is_mcp_available", return_value=True):
        require_mcp()


def test_require_mcp_raises_usage_error_when_missing() -> None:
    """require_mcp raises a Click UsageError with install guidance."""
    with (
        patch("lintro.mcp.is_mcp_available", return_value=False),
        pytest.raises(UsageError) as exc_info,
    ):
        require_mcp()

    assert_that(str(exc_info.value)).contains("lintro[mcp]")
