"""Unit tests for node_deps utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.utils.node_deps import (
    get_package_manager_command,
    install_node_deps,
    should_install_deps,
)

# =============================================================================
# Tests for should_install_deps
# =============================================================================


def test_should_install_deps_returns_false_when_no_package_json(
    tmp_path: Path,
) -> None:
    """Return False when package.json doesn't exist."""
    result = should_install_deps(tmp_path)

    assert_that(result).is_false()


def test_should_install_deps_returns_true_when_package_json_exists_no_node_modules(
    tmp_path: Path,
) -> None:
    """Return True when package.json exists but node_modules is missing."""
    (tmp_path / "package.json").write_text("{}")

    result = should_install_deps(tmp_path)

    assert_that(result).is_true()


def test_should_install_deps_returns_false_when_both_exist_with_content(
    tmp_path: Path,
) -> None:
    """Return False when both package.json and node_modules exist with content."""
    (tmp_path / "package.json").write_text("{}")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    # Add a real package directory (not just .bin)
    (node_modules / "lodash").mkdir()

    result = should_install_deps(tmp_path)

    assert_that(result).is_false()


def test_should_install_deps_returns_true_when_node_modules_empty(
    tmp_path: Path,
) -> None:
    """Return True when node_modules exists but is empty."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "node_modules").mkdir()

    result = should_install_deps(tmp_path)

    assert_that(result).is_true()


def test_should_install_deps_returns_true_when_node_modules_only_has_bin(
    tmp_path: Path,
) -> None:
    """Return True when node_modules only contains .bin directory."""
    (tmp_path / "package.json").write_text("{}")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / ".bin").mkdir()

    result = should_install_deps(tmp_path)

    assert_that(result).is_true()


def test_should_install_deps_raises_permission_error_when_cwd_not_writable(
    tmp_path: Path,
) -> None:
    """Raise PermissionError when package.json exists but directory is not writable."""
    (tmp_path / "package.json").write_text("{}")

    with (
        patch("lintro.utils.node_deps.os.access", return_value=False),
        pytest.raises(PermissionError, match="not writable"),
    ):
        should_install_deps(tmp_path)


# =============================================================================
# Tests for get_package_manager_command
# =============================================================================


def test_get_package_manager_command_returns_bun_when_available() -> None:
    """Return bun install with --ignore-scripts when bun is available."""
    with patch("lintro.utils.node_deps.shutil.which") as mock_which:
        mock_which.side_effect = lambda x: "/usr/bin/bun" if x == "bun" else None

        result = get_package_manager_command()

        assert_that(result).is_equal_to(["bun", "install", "--ignore-scripts"])


def test_get_package_manager_command_returns_npm_when_bun_not_available() -> None:
    """Return npm install with --ignore-scripts when bun is not available but npm is."""
    with patch("lintro.utils.node_deps.shutil.which") as mock_which:
        mock_which.side_effect = lambda x: "/usr/bin/npm" if x == "npm" else None

        result = get_package_manager_command()

        assert_that(result).is_equal_to(["npm", "install", "--ignore-scripts"])


def test_get_package_manager_command_returns_none_when_no_package_manager() -> None:
    """Return None when no package manager is available."""
    with patch("lintro.utils.node_deps.shutil.which", return_value=None):
        result = get_package_manager_command()

        assert_that(result).is_none()


# =============================================================================
# Tests for install_node_deps
# =============================================================================


def test_install_node_deps_returns_success_when_deps_already_installed(
    tmp_path: Path,
) -> None:
    """Return success when dependencies are already installed."""
    (tmp_path / "package.json").write_text("{}")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "lodash").mkdir()

    success, output = install_node_deps(tmp_path)

    assert_that(success).is_true()
    assert_that(output).contains("already installed")


def test_install_node_deps_returns_failure_when_cwd_not_writable(
    tmp_path: Path,
) -> None:
    """Return failure when directory is not writable.

    PermissionError from should_install_deps.
    """
    (tmp_path / "package.json").write_text("{}")

    with patch("lintro.utils.node_deps.os.access", return_value=False):
        success, output = install_node_deps(tmp_path)

    assert_that(success).is_false()
    assert_that(output).contains("not writable")


def test_install_node_deps_returns_failure_when_no_package_manager(
    tmp_path: Path,
) -> None:
    """Return failure when no package manager is available."""
    (tmp_path / "package.json").write_text("{}")

    with patch("lintro.utils.node_deps.shutil.which", return_value=None):
        success, output = install_node_deps(tmp_path)

        assert_that(success).is_false()
        assert_that(output).contains("No package manager found")


def test_install_node_deps_runs_bun_install_with_frozen_lockfile(
    tmp_path: Path,
) -> None:
    """Try bun install with frozen lockfile first."""
    (tmp_path / "package.json").write_text("{}")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Installed packages"
    mock_result.stderr = ""

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/bun" if x == "bun" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        success, output = install_node_deps(tmp_path)

        assert_that(success).is_true()
        # Verify frozen lockfile was attempted
        call_args = mock_run.call_args_list[0]
        assert_that(call_args[0][0]).contains("--frozen-lockfile")


def test_install_node_deps_falls_back_to_regular_install_on_frozen_failure(
    tmp_path: Path,
) -> None:
    """Fall back to regular install when frozen lockfile fails."""
    (tmp_path / "package.json").write_text("{}")

    frozen_result = MagicMock()
    frozen_result.returncode = 1
    frozen_result.stderr = "lockfile error"

    regular_result = MagicMock()
    regular_result.returncode = 0
    regular_result.stdout = "Installed"
    regular_result.stderr = ""

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/bun" if x == "bun" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            side_effect=[frozen_result, regular_result],
        ) as mock_run,
    ):
        success, output = install_node_deps(tmp_path)

        assert_that(success).is_true()
        # Verify both attempts were made
        assert_that(mock_run.call_count).is_equal_to(2)


def test_install_node_deps_returns_failure_on_install_error(
    tmp_path: Path,
) -> None:
    """Return failure when both frozen and regular installation fail."""
    (tmp_path / "package.json").write_text("{}")

    failed_result = MagicMock()
    failed_result.returncode = 1
    failed_result.stdout = ""
    failed_result.stderr = "npm ERR! network error"

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            return_value=failed_result,
        ) as mock_run,
    ):
        success, output = install_node_deps(tmp_path)

        assert_that(success).is_false()
        assert_that(output).contains("network error")
        # Verify both npm ci and npm install were attempted
        assert_that(mock_run.call_count).is_equal_to(2)


def test_install_node_deps_retries_on_frozen_timeout(tmp_path: Path) -> None:
    """Retry with regular install when frozen install times out."""
    (tmp_path / "package.json").write_text("{}")

    regular_result = MagicMock()
    regular_result.returncode = 0
    regular_result.stdout = "Installed"
    regular_result.stderr = ""

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            side_effect=[
                subprocess.TimeoutExpired(cmd="npm ci", timeout=120),
                regular_result,
            ],
        ) as mock_run,
    ):
        success, output = install_node_deps(tmp_path, timeout=120)

        assert_that(success).is_true()
        assert_that(mock_run.call_count).is_equal_to(2)


def test_install_node_deps_fails_on_both_attempts_timeout(tmp_path: Path) -> None:
    """Return failure when both frozen and regular install time out."""
    (tmp_path / "package.json").write_text("{}")

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=120),
        ),
    ):
        success, output = install_node_deps(tmp_path, timeout=120)

        assert_that(success).is_false()
        assert_that(output).contains("timed out")


def test_install_node_deps_uses_npm_ci_for_frozen_install(
    tmp_path: Path,
) -> None:
    """Use npm ci for frozen install with npm."""
    (tmp_path / "package.json").write_text("{}")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Installed"
    mock_result.stderr = ""

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: "/usr/bin/npm" if x == "npm" else None,
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        success, _ = install_node_deps(tmp_path)

        assert_that(success).is_true()
        # npm ci is the frozen lockfile equivalent for npm
        # --ignore-scripts prevents lifecycle script execution for security
        call_args = mock_run.call_args_list[0]
        assert_that(call_args[0][0]).is_equal_to(["npm", "ci", "--ignore-scripts"])


@pytest.mark.parametrize(
    ("package_manager", "frozen_cmd", "regular_cmd"),
    [
        (
            "bun",
            ["bun", "install", "--ignore-scripts", "--frozen-lockfile"],
            ["bun", "install", "--ignore-scripts"],
        ),
        (
            "npm",
            ["npm", "ci", "--ignore-scripts"],
            ["npm", "install", "--ignore-scripts"],
        ),
    ],
)
def test_install_node_deps_uses_correct_commands_per_package_manager(
    tmp_path: Path,
    package_manager: str,
    frozen_cmd: list[str],
    regular_cmd: list[str],
) -> None:
    """Verify correct frozen and regular commands per package manager."""
    (tmp_path / "package.json").write_text("{}")

    # First call (frozen) fails, second call (regular) succeeds
    frozen_result = MagicMock()
    frozen_result.returncode = 1
    frozen_result.stderr = "lockfile error"

    regular_result = MagicMock()
    regular_result.returncode = 0
    regular_result.stdout = "Installed"
    regular_result.stderr = ""

    with (
        patch(
            "lintro.utils.node_deps.shutil.which",
            side_effect=lambda x: (
                f"/usr/bin/{package_manager}" if x == package_manager else None
            ),
        ),
        patch(
            "lintro.utils.node_deps.subprocess.run",
            side_effect=[frozen_result, regular_result],
        ) as mock_run,
    ):
        success, _ = install_node_deps(tmp_path)

        assert_that(success).is_true()
        assert_that(mock_run.call_count).is_equal_to(2)
        # Verify frozen command was tried first
        assert_that(mock_run.call_args_list[0][0][0]).is_equal_to(frozen_cmd)
        # Verify regular command was used as fallback
        assert_that(mock_run.call_args_list[1][0][0]).is_equal_to(regular_cmd)
