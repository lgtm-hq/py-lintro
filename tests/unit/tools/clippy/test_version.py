"""Unit tests for Clippy version gating via the shared checker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro._tool_versions import TOOL_VERSIONS
from lintro.enums.tool_name import ToolName
from lintro.tools.clippy.definition import ClippyPlugin
from lintro.tools.core.version_parsing import (
    compare_versions,
    extract_version_from_output,
)


def test_check_skips_when_rustc_banner_is_below_pin(tmp_path: Path) -> None:
    """Clippy.check() skips through shared verify_tool_version, not the instance helper.

    Production never calls ``ClippyPlugin._verify_tool_version``. The shared
    checker parses the rustc/clippy banner under the clippy tool name.

    Args:
        tmp_path: Temporary directory path for a Cargo project.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n')
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")

    mock_run = MagicMock()
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="rustc 1.0.0 (aaaaaaa 2020-01-01)",
        stderr="",
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.get_executable_command",
            return_value=["rustc"],
        ),
        patch("lintro.tools.core.version_parsing.subprocess.run", mock_run),
    ):
        result = ClippyPlugin().check([str(rs_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("Skipping clippy")
    assert_that(result.output).contains("1.0.0")
    assert_that(result.output).contains("Minimum required")


def test_pinned_toolchain_clippy_banner_meets_the_clippy_minimum() -> None:
    """The clippy the pinned toolchain ships must satisfy clippy's own minimum.

    ``cargo clippy --version`` prints ``clippy 0.1.<minor>``, which parses as
    ``1.<minor>.0``. A clippy minimum carrying rustc's patch level (1.98.1)
    was therefore unreachable and clippy skipped inside the tools image even
    though the image shipped exactly the pinned toolchain (#3282).
    """
    minor = TOOL_VERSIONS[ToolName.RUSTC].split(".")[1]
    banner = f"clippy 0.1.{minor} (48a229ceae 2026-09-01)"

    installed = extract_version_from_output(banner, ToolName.CLIPPY)

    assert_that(installed).is_not_none()
    assert_that(
        compare_versions(str(installed), TOOL_VERSIONS[ToolName.CLIPPY]),
    ).is_greater_than_or_equal_to(0)
