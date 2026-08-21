"""Tests for the ``lintro versions`` CLI command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.versions import versions_command
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.update_channels import VersionAdvisory
from lintro.tools.core.version_parsing import ToolVersionInfo, check_tool_version


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


def test_versions_json_includes_null_advisory_when_current() -> None:
    """JSON always includes the advisory key so MCP and CLI share one shape."""
    current = ToolVersionInfo(
        name="ruff",
        min_version="0.9.0",
        recommended_version="0.9.0",
        current_version="0.9.0",
        version_check_passed=True,
        below_recommended=False,
    )
    with patch(
        "lintro.cli_utils.commands.versions.get_all_tool_versions",
        return_value={"ruff": current},
    ):
        result = CliRunner().invoke(versions_command, ["--json"])

    assert_that(result.exit_code).is_equal_to(0)
    data = json.loads(result.output)
    assert_that(data["ruff"]).contains_key("advisory")
    assert_that(data["ruff"]["advisory"]).is_none()


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
    assert_that(result.output).contains("OUTDATED")


def test_versions_human_status_is_outdated_when_below_recommended() -> None:
    """Meeting the minimum but trailing the pin shows OUTDATED, not PASS."""
    current = ToolVersionInfo(
        name="ruff",
        min_version="0.6.0",
        recommended_version="0.9.0",
        current_version="0.9.0",
        version_check_passed=True,
        below_recommended=False,
    )
    with patch(
        "lintro.cli_utils.commands.versions.get_all_tool_versions",
        return_value={"ruff": current, "black": _outdated_ruff()},
    ):
        result = CliRunner().invoke(versions_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("PASS")
    assert_that(result.output).contains("OUTDATED")


def test_versions_json_advisory_from_check_tool_version() -> None:
    """JSON advisories come from version probes, not a prebuilt fixture."""

    def fake_which(name: str) -> str | None:
        mapping = {
            "cargo": "/Users/me/.cargo/bin/cargo",
            "cargo-audit": "/Users/me/.cargo/bin/cargo-audit",
        }
        return mapping.get(name)

    with (
        patch("shutil.which", side_effect=fake_which),
        patch(
            "lintro.tools.core.version_parsing.subprocess.run",
        ) as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="cargo-audit 0.20.0\n",
            stderr="",
        )
        info = check_tool_version(
            "cargo_audit",
            ["cargo", "audit"],
            append_version=True,
        )

    assert_that(info.advisory).is_not_none()
    assert info.advisory is not None
    assert_that(info.advisory.channel).is_equal_to(UpdateChannel.CARGO)
    assert_that(info.advisory.update_command).is_equal_to(
        "cargo install --force cargo-audit",
    )
    assert_that(info.advisory.update_command).does_not_contain("rustup")
    assert_that(info.binary_path).is_equal_to("/Users/me/.cargo/bin/cargo-audit")

    with patch(
        "lintro.cli_utils.commands.versions.get_all_tool_versions",
        return_value={"cargo_audit": info},
    ):
        result = CliRunner().invoke(versions_command, ["--json"])

    assert_that(result.exit_code).is_equal_to(0)
    data = json.loads(result.output)
    assert_that(data["cargo_audit"]["advisory"]["channel"]).is_equal_to("cargo")
    assert_that(data["cargo_audit"]["advisory"]["update_command"]).is_equal_to(
        "cargo install --force cargo-audit",
    )
    assert_that(data["cargo_audit"]["binary_path"]).is_equal_to(
        "/Users/me/.cargo/bin/cargo-audit",
    )


def test_versions_json_binary_path_resolved_when_current() -> None:
    """Wrapper probes still export the tool binary when the version is current."""

    def fake_which(name: str) -> str | None:
        mapping = {
            "cargo": "/Users/me/.cargo/bin/cargo",
            "cargo-audit": "/Users/me/.cargo/bin/cargo-audit",
        }
        return mapping.get(name)

    with (
        patch("shutil.which", side_effect=fake_which),
        patch(
            "lintro.tools.core.version_parsing.subprocess.run",
        ) as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="cargo-audit 99.0.0\n",
            stderr="",
        )
        info = check_tool_version(
            "cargo_audit",
            ["cargo", "audit"],
            append_version=True,
        )

    assert_that(info.version_check_passed).is_true()
    assert_that(info.advisory).is_none()
    assert_that(info.binary_path).is_equal_to("/Users/me/.cargo/bin/cargo-audit")


def test_check_tool_version_failed_probe_still_resolves_binary_path() -> None:
    """A failed cargo wrapper probe still exports the crate binary path."""

    def fake_which(name: str) -> str | None:
        mapping = {
            "cargo": "/Users/me/.cargo/bin/cargo",
            "cargo-audit": "/Users/me/.cargo/bin/cargo-audit",
        }
        return mapping.get(name)

    with (
        patch("shutil.which", side_effect=fake_which),
        patch(
            "lintro.tools.core.version_parsing.subprocess.run",
        ) as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: no such command: `audit`",
        )
        info = check_tool_version(
            "cargo_audit",
            ["cargo", "audit"],
            append_version=True,
        )

    assert_that(info.version_check_passed).is_false()
    assert_that(info.binary_path).is_equal_to("/Users/me/.cargo/bin/cargo-audit")
    assert_that(info.advisory).is_none()


def test_check_tool_version_clippy_resolves_cargo_clippy() -> None:
    """Clippy probes via cargo but JSON binary_path is cargo-clippy."""

    def fake_which(name: str) -> str | None:
        mapping = {
            "cargo": "/Users/me/.cargo/bin/cargo",
            "cargo-clippy": "/Users/me/.cargo/bin/cargo-clippy",
        }
        return mapping.get(name)

    with (
        patch("shutil.which", side_effect=fake_which),
        patch(
            "lintro.tools.core.version_parsing.subprocess.run",
        ) as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="clippy 0.1.90 (hash 2026-01-01)\n",
            stderr="",
        )
        info = check_tool_version(
            "clippy",
            ["cargo", "clippy"],
            append_version=True,
        )

    assert_that(info.binary_path).is_equal_to("/Users/me/.cargo/bin/cargo-clippy")
