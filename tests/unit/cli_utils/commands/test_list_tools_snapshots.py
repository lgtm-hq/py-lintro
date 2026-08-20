"""Tests for list-tools capability snapshot JSON output."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_result_status import ToolResultStatus
from lintro.tools.core.snapshots import ToolCapabilities, ToolSnapshot


def _snapshot(
    *,
    name: str = "ruff",
    available: bool = True,
    version: str | None = "0.14.0",
) -> ToolSnapshot:
    """Build a minimal ToolSnapshot for list-tools tests."""
    return ToolSnapshot(
        name=name,
        available=available,
        version=version,
        capabilities=ToolCapabilities(can_fix=True),
        probe_error=None if available else "not found",
        remediation_hint="Install ruff",
        binary_path="/usr/bin/ruff" if available else "",
        binary_mtime=1.0 if available else 0.0,
        version_check_passed=available,
        min_version="0.1.0",
    )


def test_list_tools_json_missing_snapshot_reports_unknown_not_unavailable() -> None:
    """Missing snapshots omit ``available`` and use ``status: unknown``."""
    from lintro.cli_utils.commands.list_tools import list_tools

    plugin = MagicMock()
    plugin.definition.description = "Fast linter"
    plugin.definition.execution_class.value = "subprocess"
    plugin.definition.file_patterns = []
    plugin.definition.conflicts_with = []

    with (
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_all_tools",
            return_value={"ruff": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_check_tools",
            return_value={"ruff": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_fix_tools",
            return_value={"ruff": plugin},
        ),
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value={},
        ),
        patch("lintro.cli_utils.commands.list_tools.click.echo") as mock_echo,
    ):
        list_tools(
            output=None,
            show_conflicts=False,
            json_output=True,
        )

    payload = json.loads(mock_echo.call_args[0][0])
    assert_that(payload["ruff"]).does_not_contain_key("available")
    assert_that(payload["ruff"]["status"]).is_equal_to(ToolResultStatus.UNKNOWN)


def test_list_tools_json_available_without_version_reports_unknown() -> None:
    """An available tool with no parsed version shows ``unknown``, not ``ok (?)``."""
    from lintro.cli_utils.commands.list_tools import list_tools

    plugin = MagicMock()
    plugin.definition.description = "In-process tool"
    plugin.definition.execution_class.value = "in_process"
    plugin.definition.file_patterns = []
    plugin.definition.conflicts_with = []

    snap = _snapshot(name="idiom-review", version=None)

    with (
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_all_tools",
            return_value={"idiom-review": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_check_tools",
            return_value={"idiom-review": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_fix_tools",
            return_value={},
        ),
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value={"idiom-review": snap},
        ),
        patch("lintro.cli_utils.commands.list_tools.click.echo") as mock_echo,
    ):
        list_tools(
            output=None,
            show_conflicts=False,
            json_output=True,
        )

    payload = json.loads(mock_echo.call_args[0][0])
    assert_that(payload["idiom-review"]["available"]).is_true()
    assert_that(payload["idiom-review"]["status"]).is_equal_to(ToolResultStatus.UNKNOWN)
    assert_that(payload["idiom-review"]["version"]).is_none()


def test_list_tools_json_unavailable_snapshot_sets_status_and_available() -> None:
    """Unavailable snapshots keep ``available: false`` and remediation hints."""
    from lintro.cli_utils.commands.list_tools import list_tools

    plugin = MagicMock()
    plugin.definition.description = "Docker linter"
    plugin.definition.execution_class.value = "subprocess"
    plugin.definition.file_patterns = []
    plugin.definition.conflicts_with = []

    snap = _snapshot(name="hadolint", available=False, version=None)

    with (
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_all_tools",
            return_value={"hadolint": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_check_tools",
            return_value={"hadolint": plugin},
        ),
        patch(
            "lintro.cli_utils.commands.list_tools.tool_manager.get_fix_tools",
            return_value={},
        ),
        patch(
            "lintro.tools.core.snapshots.probe_all_tools",
            return_value={"hadolint": snap},
        ),
        patch("lintro.cli_utils.commands.list_tools.click.echo") as mock_echo,
    ):
        list_tools(
            output=None,
            show_conflicts=False,
            json_output=True,
        )

    payload = json.loads(mock_echo.call_args[0][0])
    assert_that(payload["hadolint"]["available"]).is_false()
    assert_that(payload["hadolint"]["status"]).is_equal_to(
        ToolResultStatus.UNAVAILABLE,
    )
    assert_that(payload["hadolint"]["remediation_hint"]).is_equal_to("Install ruff")
