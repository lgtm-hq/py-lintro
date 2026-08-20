"""Tests for the ``lintro versions`` CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.versions import versions_command
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.update_channels import VersionAdvisory
from lintro.tools.core.version_parsing import ToolVersionInfo


def _outdated_ruff() -> ToolVersionInfo:
    """Build an outdated ruff version record with an advisory.

    Returns:
        ToolVersionInfo: Fixture version info for ruff.
    """
    return ToolVersionInfo(
        name="ruff",
        min_version="0.9.0",
        recommended_version="0.9.0",
        current_version="0.6.9",
        version_check_passed=True,
        below_recommended=True,
        advisory=VersionAdvisory(
            tool="ruff",
            installed="0.6.9",
            latest_known="0.9.0",
            channel=UpdateChannel.UV_TOOL,
            update_command="uv tool upgrade ruff",
        ),
    )


def test_versions_json_includes_advisory() -> None:
    """JSON output carries the structured advisory for outdated tools."""
    with patch(
        "lintro.cli_utils.commands.versions.get_all_tool_versions",
        return_value={"ruff": _outdated_ruff()},
    ):
        result = CliRunner().invoke(versions_command, ["--json"])

    assert_that(result.exit_code).is_equal_to(0)
    data = json.loads(result.output)
    assert_that(data["ruff"]["advisory"]["channel"]).is_equal_to("uv_tool")
    assert_that(data["ruff"]["advisory"]["update_command"]).is_equal_to(
        "uv tool upgrade ruff",
    )
    assert_that(data["ruff"]["below_recommended"]).is_true()


def test_versions_human_output_renders_advisory_line() -> None:
    """Human output prints update advisories under the versions table."""
    with patch(
        "lintro.cli_utils.commands.versions.get_all_tool_versions",
        return_value={"ruff": _outdated_ruff()},
    ):
        result = CliRunner().invoke(versions_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Update advisories")
    assert_that(result.output).contains("installed via uv tool")
    assert_that(result.output).contains("upgrade ruff")
    assert_that(result.output).contains("OUTDATED")
