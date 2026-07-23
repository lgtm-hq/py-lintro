"""Tests for oxlint type-aware doctor checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_status import ToolStatus
from lintro.tools.definitions import oxlint_doctor
from lintro.tools.definitions.oxlint_doctor import (
    TSGOLINT_INSTALL_HINT,
    OxlintCheckResult,
    check_oxlint_type_aware,
    oxlintrc_type_aware_enabled,
)

MODULE = "lintro.tools.definitions.oxlint_doctor"


def _status(results: list[OxlintCheckResult], name: str) -> ToolStatus:
    """Return the status for the check with the given name.

    Args:
        results: List of OxlintCheckResult.
        name: The check name to look up.

    Returns:
        ToolStatus: The status for the matching check.
    """
    return next(r.status for r in results if r.name == name)


# =============================================================================
# oxlintrc_type_aware_enabled
# =============================================================================


def test_oxlintrc_type_aware_enabled_true() -> None:
    """options.typeAware=true in .oxlintrc.json enables type-aware."""
    with patch(
        f"{MODULE}.load_native_tool_config",
        return_value={"options": {"typeAware": True}},
    ):
        assert_that(oxlintrc_type_aware_enabled()).is_true()


def test_oxlintrc_type_aware_enabled_false_when_absent() -> None:
    """Missing options.typeAware does not enable type-aware."""
    with patch(f"{MODULE}.load_native_tool_config", return_value={"rules": {}}):
        assert_that(oxlintrc_type_aware_enabled()).is_false()


def test_oxlintrc_type_aware_enabled_non_dict_options() -> None:
    """Non-dict options key does not enable type-aware."""
    with patch(
        f"{MODULE}.load_native_tool_config",
        return_value={"options": "nope"},
    ):
        assert_that(oxlintrc_type_aware_enabled()).is_false()


# =============================================================================
# check_oxlint_type_aware - disabled
# =============================================================================


def test_check_returns_empty_when_disabled() -> None:
    """No checks are produced when type-aware is not enabled anywhere."""
    with patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False):
        results = check_oxlint_type_aware(option_enabled=False)
    assert_that(results).is_empty()


# =============================================================================
# check_oxlint_type_aware - tsgolint resolution
# =============================================================================


def test_check_tsgolint_present_reports_ok() -> None:
    """Resolvable oxlint-tsgolint reports OK."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value="node_modules/.bin/x"),
        patch(f"{MODULE}._detect_typescript_version", return_value="7.1.0"),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    assert_that(_status(results, "oxlint.type-aware.tsgolint")).is_equal_to(
        ToolStatus.OK,
    )


def test_check_tsgolint_missing_reports_hint() -> None:
    """Unresolvable oxlint-tsgolint reports MISSING with install hint."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value=None),
        patch(f"{MODULE}._detect_typescript_version", return_value="7.1.0"),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    tsgolint = next(r for r in results if r.name == "oxlint.type-aware.tsgolint")
    assert_that(tsgolint.status).is_equal_to(ToolStatus.MISSING)
    assert_that(tsgolint.hint).is_equal_to(TSGOLINT_INSTALL_HINT)


def test_check_enabled_via_oxlintrc_only() -> None:
    """options.typeAware alone (option disabled) still triggers checks."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=True),
        patch(f"{MODULE}._resolve_tsgolint", return_value=None),
        patch(f"{MODULE}._detect_typescript_version", return_value="7.1.0"),
    ):
        results = check_oxlint_type_aware(option_enabled=False)

    assert_that(results).is_not_empty()
    assert_that(_status(results, "oxlint.type-aware.tsgolint")).is_equal_to(
        ToolStatus.MISSING,
    )


# =============================================================================
# check_oxlint_type_aware - TypeScript version
# =============================================================================


def test_check_typescript_ok() -> None:
    """TypeScript >= 7.0 reports OK."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value="tsgolint"),
        patch(f"{MODULE}._detect_typescript_version", return_value="7.0.0"),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    assert_that(_status(results, "oxlint.type-aware.typescript")).is_equal_to(
        ToolStatus.OK,
    )


def test_check_typescript_incompatible() -> None:
    """TypeScript < 7.0 reports INCOMPATIBLE."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value="tsgolint"),
        patch(f"{MODULE}._detect_typescript_version", return_value="5.6.2"),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    assert_that(_status(results, "oxlint.type-aware.typescript")).is_equal_to(
        ToolStatus.INCOMPATIBLE,
    )


def test_check_typescript_missing() -> None:
    """Absent TypeScript reports MISSING."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value="tsgolint"),
        patch(f"{MODULE}._detect_typescript_version", return_value=None),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    assert_that(_status(results, "oxlint.type-aware.typescript")).is_equal_to(
        ToolStatus.MISSING,
    )


def test_check_typescript_unparseable_is_unknown() -> None:
    """An unparseable TypeScript version reports UNKNOWN."""
    with (
        patch(f"{MODULE}.oxlintrc_type_aware_enabled", return_value=False),
        patch(f"{MODULE}._resolve_tsgolint", return_value="tsgolint"),
        patch(f"{MODULE}._detect_typescript_version", return_value="not-a-version"),
    ):
        results = check_oxlint_type_aware(option_enabled=True)

    assert_that(_status(results, "oxlint.type-aware.typescript")).is_equal_to(
        ToolStatus.UNKNOWN,
    )


# =============================================================================
# Resolver behavior
# =============================================================================


def test_resolve_tsgolint_prefers_node_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """node_modules/.bin/oxlint-tsgolint is preferred when present.

    Args:
        tmp_path: Temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "oxlint-tsgolint").write_text("#!/bin/sh\n")

    resolved = oxlint_doctor._resolve_tsgolint()
    assert_that(resolved).contains("oxlint-tsgolint")


def test_resolve_tsgolint_bunx_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bunx is used as a fallback when no local/PATH binary exists.

    Args:
        tmp_path: Temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)

    def _which(name: str) -> str | None:
        return "/usr/bin/bunx" if name == "bunx" else None

    with patch(f"{MODULE}.shutil.which", side_effect=_which):
        resolved = oxlint_doctor._resolve_tsgolint()

    assert_that(resolved).is_equal_to("bunx oxlint-tsgolint")


def test_resolve_tsgolint_none_when_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None is returned when nothing resolves.

    Args:
        tmp_path: Temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)

    with patch(f"{MODULE}.shutil.which", return_value=None):
        resolved = oxlint_doctor._resolve_tsgolint()

    assert_that(resolved).is_none()
