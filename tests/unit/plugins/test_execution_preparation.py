"""Unit tests for typed execution preparation and version-lag helpers."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import NoFiles, Skipped, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import (
    PreparedExecution,
    VersionLagPolicy,
    _parse_allow_version_lag,
    _version_lag_allowed,
    prepare_execution,
    verify_tool_version,
)
from lintro.plugins.protocol import ToolDefinition


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, set()),
        ("", set()),
        ("  ", set()),
        ("trufflehog", {"trufflehog"}),
        ("trufflehog,oxlint", {"trufflehog", "oxlint"}),
        (" TruffleHog , OxLint ", {"trufflehog", "oxlint"}),
    ],
    ids=[
        "unset",
        "empty",
        "blank",
        "single",
        "multi",
        "case-and-spaces",
    ],
)
def test_parse_allow_version_lag(raw: str | None, expected: set[str]) -> None:
    """Parse LINTRO_ALLOW_VERSION_LAG into a name set.

    Args:
        raw: Raw env string under test.
        expected: Expected parsed value.
    """
    assert_that(_parse_allow_version_lag(raw)).is_equal_to(expected)


def test_parse_allow_version_lag_star_is_explicit() -> None:
    """The wildcard has an explicit value distinct from absent configuration."""
    assert_that(_parse_allow_version_lag(" * ")).is_same_as(VersionLagPolicy.ALLOW_ALL)


def test_prepared_execution_requires_cwd() -> None:
    """A runnable preparation cannot be constructed without a working directory."""
    with pytest.raises(TypeError, match="cwd"):
        PreparedExecution(files=[], rel_files=[], timeout=2.5)  # type: ignore[call-arg]


def test_prepared_execution_is_frozen() -> None:
    """Resolved metadata cannot be reassigned after preparation."""
    prepared = PreparedExecution(files=[], rel_files=[], cwd="/project", timeout=2.5)
    with pytest.raises(FrozenInstanceError):
        prepared.cwd = "/elsewhere"  # type: ignore[misc]
    assert_that(prepared.cwd).is_equal_to("/project")
    assert_that(prepared.timeout).is_equal_to(2.5)


def test_prepare_execution_returns_resolved_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real discovery preserves relative paths and fractional timeout overrides.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture to set the invocation's working directory.
    """
    (tmp_path / ".git").mkdir()
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    target = source_dir / "example.py"
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        prepared = prepare_execution(
            paths=[str(source_dir)],
            options={"timeout": 0.25},
            definition=ToolDefinition(
                name="fake-tool",
                description="Test",
                file_patterns=["*.py"],
            ),
            exclude_patterns=[],
            include_venv=False,
            current_options={"timeout": 45},
        )

    assert_that(prepared).is_instance_of(PreparedExecution)
    prepared = cast("PreparedExecution", prepared)
    assert_that(prepared.files).is_equal_to([str(target)])
    assert_that(prepared.rel_files).is_equal_to(["src/example.py"])
    assert_that(prepared.cwd).is_equal_to(str(tmp_path))
    assert_that(prepared.timeout).is_equal_to(0.25)


@pytest.mark.parametrize(
    "empty_paths",
    [True, False],
    ids=["empty-input", "no-matches"],
)
def test_prepare_execution_no_files_returns_result(
    tmp_path: Path,
    empty_paths: bool,
) -> None:
    """No files remains a successful non-verdict with no version check required.

    Args:
        tmp_path: Empty directory for discovery.
        empty_paths: Whether to omit paths altogether.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        side_effect=AssertionError("No binary needed"),
    ):
        result = prepare_execution(
            paths=[] if empty_paths else [str(tmp_path)],
            options={},
            definition=ToolDefinition(
                name="fake-tool",
                description="Test",
                file_patterns=["*.py"],
            ),
            exclude_patterns=[],
            include_venv=False,
            current_options={},
            no_files_message="Nothing requested.",
        )

    assert_that(result).is_instance_of(ToolResult)
    result = cast("ToolResult", result)
    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.outcome).is_equal_to(ToolOutcome(NoFiles()))
    assert_that(result.inspected_files).is_false()
    assert_that(result.initial_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).is_equal_to(
        "Nothing requested." if empty_paths else "No .py files found to check.",
    )


def test_prepare_execution_preserves_version_skip(tmp_path: Path) -> None:
    """An early version result retains its optional metadata and identity.

    Args:
        tmp_path: Temporary project directory.
    """
    target = tmp_path / "example.py"
    target.touch()
    skipped = ToolResult(
        name="fake-tool",
        success=True,
        skipped=True,
        skip_reason="Unavailable",
        inspected_files=False,
    )
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=skipped,
    ):
        result = prepare_execution(
            paths=[str(target)],
            options={},
            definition=ToolDefinition(
                name="fake-tool",
                description="Test",
                file_patterns=["*.py"],
            ),
            exclude_patterns=[],
            include_venv=False,
            current_options={},
        )

    assert_that(result).is_same_as(skipped)
    result = cast("ToolResult", result)
    assert_that(result.output).is_none()
    assert_that(result.remaining_issues_count).is_none()


def test_version_rejection_produces_explicit_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real version producer preserves its diagnosis and unknown counters.

    Args:
        monkeypatch: Restores the version-lag override after forcing enforcement.
    """
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    from lintro.tools.core.snapshots import ToolSnapshot

    version = ToolSnapshot(
        name="ruff",
        available=True,
        version="0.13.0",
        min_version="0.14.0",
        version_check_passed=False,
        probe_error="Ruff is below minimum",
        remediation_hint="Install Ruff",
    )
    with patch(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        return_value=version,
    ):
        result = verify_tool_version(ToolDefinition(name="ruff", description="Test"))

    assert result is not None  # narrow the successful version-check alternative
    assert_that(result.outcome).is_equal_to(
        ToolOutcome(Skipped("Ruff is below minimum")),
    )
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.output).contains("Ruff is below minimum", "Install Ruff")
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()


def test_version_lag_allowed_for_listed_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listed tools are allowed; others are not.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "trufflehog,oxlint")
    assert_that(_version_lag_allowed("trufflehog")).is_true()
    assert_that(_version_lag_allowed("TRUFFLEHOG")).is_true()
    assert_that(_version_lag_allowed("ruff")).is_false()


def test_version_lag_allowed_star(monkeypatch: pytest.MonkeyPatch) -> None:
    """Star allowlists every tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "*")
    assert_that(_version_lag_allowed("anything")).is_true()


def test_version_lag_disallowed_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env keeps full min-version enforcement.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    assert_that(_version_lag_allowed("trufflehog")).is_false()


def test_verify_tool_version_uses_cached_snapshot() -> None:
    """Version gates read ToolSnapshot flags, not a live subprocess probe.

    The snapshot must resolve from the execution directory.
    """
    from unittest.mock import patch

    from lintro.plugins.execution_preparation import verify_tool_version
    from lintro.tools.core.snapshots import ToolCapabilities, ToolSnapshot

    definition = type("Def", (), {"name": "html_validate"})()
    snap = ToolSnapshot(
        name="html_validate",
        available=True,
        version="1.0.0",
        capabilities=ToolCapabilities(),
        version_check_passed=True,
        min_version="1.0.0",
    )

    with patch(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        return_value=snap,
    ) as mock_snap:
        result = verify_tool_version(definition, cwd="/tmp/target-project")

    assert_that(result).is_none()
    mock_snap.assert_called_once_with(
        "html_validate",
        force=False,
        cache_root=Path("/tmp/target-project"),
    )


def test_tool_definition_preserves_positional_constructor() -> None:
    """The added stdin capability cannot shift the public v2 plugin API."""
    from lintro.enums.tool_type import ToolType

    definition = ToolDefinition("custom", "formatter", True, ToolType.FORMATTER)
    assert definition.can_fix is True
    assert definition.tool_type is ToolType.FORMATTER
    assert definition.supports_stdin is None
    assert definition.in_process is False
