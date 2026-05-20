"""Unit tests for the ToolInstaller class."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.tool_installer import InstallPlan, InstallResult, ToolInstaller
from lintro.tools.core.tool_registry import ManifestTool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_tool() -> Callable[..., ManifestTool]:
    """Return a factory that builds ManifestTool instances with sensible defaults.

    Returns:
        A callable that accepts keyword overrides and produces ManifestTool.
    """

    def _factory(**overrides: object) -> ManifestTool:
        """Build a ManifestTool with defaults.

        Args:
            **overrides: Fields to override on the dataclass.

        Returns:
            A ManifestTool instance.
        """
        defaults = {
            "name": "faketool",
            "version": "1.2.0",
            "min_version": "1.2.0",
            "install_type": "pip",
            "install_package": "faketool-pkg",
            "version_command": ("faketool", "--version"),
        }
        defaults.update(overrides)  # type: ignore[arg-type]  # test factory merges heterogeneous overrides
        return ManifestTool(**defaults)  # type: ignore[arg-type]  # test factory with dynamic kwargs

    return _factory


@pytest.fixture()
def context() -> RuntimeContext:
    """Return a default RuntimeContext for testing.

    Returns:
        A RuntimeContext with common defaults.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    return RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {
                    PackageManager.UV,
                    PackageManager.PIP,
                    PackageManager.NPM,
                    PackageManager.CARGO,
                    PackageManager.RUSTUP,
                },
            ),
        ),
        is_ci=False,
    )


@pytest.fixture()
def registry() -> MagicMock:
    """Return a mock ToolRegistry.

    Returns:
        A MagicMock standing in for ToolRegistry.
    """
    return MagicMock()


@pytest.fixture()
def installer(registry: MagicMock, context: RuntimeContext) -> ToolInstaller:
    """Return a ToolInstaller wired to mock registry and real context.

    Args:
        registry: Mock ToolRegistry.
        context: RuntimeContext fixture.

    Returns:
        A ToolInstaller instance.
    """
    return ToolInstaller(registry, context)


# ---------------------------------------------------------------------------
# _is_manual_hint (static)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("See https://foo", True),
        ("Install hadolint manually", True),
        ("Upgrade hadolint manually", True),
        ("brew install foo", False),
        ("pip install foo>=1.0", False),
        ("pip install 'httpie>=3.0'", False),
        ("Download from http://example.com", True),
    ],
    ids=[
        "see_url",
        "install_manually",
        "upgrade_manually",
        "brew_command",
        "pip_command",
        "pip_http_package",
        "download_url",
    ],
)
def test_is_manual_hint(hint: str, expected: bool) -> None:
    """Classify install hints as manual or executable.

    Args:
        hint: The hint string to classify.
        expected: Whether it should be treated as manual.
    """
    assert_that(ToolInstaller._is_manual_hint(hint)).is_equal_to(expected)


# ---------------------------------------------------------------------------
# _version_meets_minimum (static)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "minimum", "expected"),
    [
        ("1.2.3", "1.0.0", True),
        ("1.0.0", "1.2.0", False),
        ("1.2.3", "1.2.3", True),
        ("invalid", "1.0.0", False),
    ],
    ids=[
        "newer_than_minimum",
        "older_than_minimum",
        "equal_to_minimum",
        "invalid_version",
    ],
)
def test_version_meets_minimum(
    installed: str,
    minimum: str,
    expected: bool,
) -> None:
    """Compare installed version against a minimum requirement.

    Args:
        installed: The installed version string.
        minimum: The required minimum version string.
        expected: Whether the installed version should meet the minimum.
    """
    assert_that(
        ToolInstaller._version_meets_minimum(installed, minimum),
    ).is_equal_to(expected)


# ---------------------------------------------------------------------------
# _plan_tool
# ---------------------------------------------------------------------------


def test_plan_tool_already_ok(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in already_ok when installed at the current version.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(version="1.2.0")
    plan = InstallPlan()

    with patch.object(installer, "_get_installed_version", return_value="1.2.0"):
        installer._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.already_ok).contains(tool)
    assert_that(plan.to_install).is_empty()
    assert_that(plan.to_upgrade).is_empty()
    assert_that(plan.outdated).is_empty()
    assert_that(plan.skipped).is_empty()


def test_plan_tool_outdated_no_upgrade(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in outdated when version is old and upgrade is False.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(version="2.0.0", min_version="1.0.0")
    plan = InstallPlan()

    with patch.object(installer, "_get_installed_version", return_value="1.5.0"):
        installer._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.outdated).is_length(1)
    assert_that(plan.outdated[0][0]).is_equal_to(tool)
    assert_that(plan.outdated[0][1]).is_equal_to("1.5.0")
    assert_that(plan.to_upgrade).is_empty()


def test_plan_tool_outdated_with_upgrade(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in to_upgrade when version is old and upgrade is True.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(version="2.0.0")
    plan = InstallPlan()

    with (
        patch.object(installer, "_get_installed_version", return_value="1.0.0"),
        patch.object(
            installer,
            "_get_install_command",
            return_value="pip install --upgrade faketool-pkg>=2.0.0",
        ),
    ):
        installer._plan_tool(plan, tool, upgrade=True)

    assert_that(plan.to_upgrade).is_length(1)
    assert_that(plan.to_upgrade[0][0]).is_equal_to(tool)
    assert_that(plan.to_upgrade[0][1]).is_equal_to("1.0.0")
    assert_that(plan.outdated).is_empty()


def test_plan_tool_missing_installable(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in to_install when not installed and hint is executable.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool()
    plan = InstallPlan()

    with (
        patch.object(installer, "_get_installed_version", return_value=None),
        patch.object(installer, "_check_prerequisites", return_value=None),
        patch.object(
            installer,
            "_get_install_command",
            return_value="pip install faketool-pkg>=1.2.0",
        ),
    ):
        installer._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.to_install).is_length(1)
    assert_that(plan.to_install[0][0]).is_equal_to(tool)
    assert_that(plan.skipped).is_empty()


def test_plan_tool_missing_manual_hint(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in manual when not installed and hint is manual.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="binary")
    plan = InstallPlan()

    with (
        patch.object(installer, "_get_installed_version", return_value=None),
        patch.object(installer, "_check_prerequisites", return_value=None),
        patch.object(
            installer,
            "_get_install_command",
            return_value="See https://github.com/example/releases",
        ),
        patch.object(installer, "_has_install_script", return_value=False),
    ):
        installer._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.manual).is_length(1)
    assert_that(plan.manual[0][0]).is_equal_to(tool)
    assert_that(plan.to_install).is_empty()


def test_plan_tool_missing_with_install_script(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place binary tool in to_install with script hint when script exists.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="binary")
    plan = InstallPlan()

    with (
        patch.object(installer, "_get_installed_version", return_value=None),
        patch.object(installer, "_check_prerequisites", return_value=None),
        patch.object(
            installer,
            "_get_install_command",
            return_value="See https://github.com/example/releases",
        ),
        patch.object(installer, "_has_install_script", return_value=True),
    ):
        installer._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.to_install).is_length(1)
    assert_that(plan.to_install[0][0]).is_equal_to(tool)
    assert_that(plan.to_install[0][1]).contains("install-tools.sh")
    assert_that(plan.skipped).is_empty()


def test_plan_tool_upgrade_manual_hint(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place tool in manual when upgrade hint is manual.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(
        version="2.0.0",
        min_version="1.0.0",
        install_type="binary",
    )
    plan = InstallPlan()

    with (
        patch.object(installer, "_get_installed_version", return_value="1.0.0"),
        patch.object(
            installer,
            "_get_install_command",
            return_value="See https://github.com/example/releases",
        ),
        patch.object(installer, "_has_install_script", return_value=False),
    ):
        installer._plan_tool(plan, tool, upgrade=True)

    assert_that(plan.manual).is_length(1)
    assert_that(plan.manual[0][0]).is_equal_to(tool)
    assert_that(plan.to_upgrade).is_empty()


def test_plan_tool_manual_no_cargo(
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place cargo tool in manual when cargo is not available.

    Args:
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {
                    PackageManager.UV,
                    PackageManager.PIP,
                    PackageManager.NPM,
                    PackageManager.RUSTUP,
                },
            ),
        ),
        is_ci=False,
    )
    inst = ToolInstaller(registry, ctx)
    tool = make_tool(install_type="cargo")
    plan = InstallPlan()

    with patch.object(inst, "_get_installed_version", return_value=None):
        inst._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.manual).is_length(1)
    assert_that(plan.manual[0][1]).contains("cargo")


def test_plan_tool_manual_no_npm(
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Place npm tool in manual when bun and npm are both unavailable.

    Args:
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {
                    PackageManager.UV,
                    PackageManager.PIP,
                    PackageManager.CARGO,
                    PackageManager.RUSTUP,
                },
            ),
        ),
        is_ci=False,
    )
    inst = ToolInstaller(registry, ctx)
    tool = make_tool(install_type="npm", name="eslint")
    plan = InstallPlan()

    with patch.object(inst, "_get_installed_version", return_value=None):
        inst._plan_tool(plan, tool, upgrade=False)

    assert_that(plan.manual).is_length(1)
    assert_that(plan.manual[0][1]).contains("npm")


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


def test_plan_with_tools_list(
    installer: ToolInstaller,
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Plan resolves specific tool names via the registry.

    Args:
        installer: ToolInstaller fixture.
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    tool_a = make_tool(name="tool_a")
    tool_b = make_tool(name="tool_b")
    registry.__contains__ = MagicMock(side_effect=lambda n: n in {"tool_a", "tool_b"})
    registry.get = MagicMock(
        side_effect=lambda n: {"tool_a": tool_a, "tool_b": tool_b}[n],
    )

    with patch.object(installer, "_plan_tool") as mock_plan_tool:
        installer.plan(tools=["tool_a", "tool_b"])

    assert_that(mock_plan_tool.call_count).is_equal_to(2)


def test_plan_deduplicates_tools(
    installer: ToolInstaller,
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Plan deduplicates tool names while preserving order.

    Args:
        installer: ToolInstaller fixture.
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    tool_a = make_tool(name="tool_a")
    registry.__contains__ = MagicMock(return_value=True)
    registry.get = MagicMock(return_value=tool_a)

    with patch.object(installer, "_plan_tool") as mock_plan_tool:
        installer.plan(tools=["tool_a", "tool_a", "tool_a"])

    assert_that(mock_plan_tool.call_count).is_equal_to(1)


def test_plan_with_profile(
    installer: ToolInstaller,
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Plan resolves tools from a profile via registry.tools_for_profile.

    Args:
        installer: ToolInstaller fixture.
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    tool_a = make_tool(name="profiled_tool")
    registry.tools_for_profile = MagicMock(return_value=[tool_a])

    with patch.object(installer, "_plan_tool") as mock_plan_tool:
        installer.plan(profile="recommended")

    registry.tools_for_profile.assert_called_once_with("recommended", None)
    assert_that(mock_plan_tool.call_count).is_equal_to(1)


def test_plan_with_upgrade_flag(
    installer: ToolInstaller,
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Plan passes the upgrade flag through to _plan_tool.

    Args:
        installer: ToolInstaller fixture.
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    tool_a = make_tool(name="upgradable")
    registry.__contains__ = MagicMock(return_value=True)
    registry.get = MagicMock(return_value=tool_a)

    with patch.object(installer, "_plan_tool") as mock_plan_tool:
        installer.plan(tools=["upgradable"], upgrade=True)

    call_kwargs = mock_plan_tool.call_args
    assert_that(call_kwargs.kwargs["upgrade"]).is_true()


# ---------------------------------------------------------------------------
# _check_prerequisites
# ---------------------------------------------------------------------------


def test_check_prerequisites_cargo_missing(
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return skip reason when cargo is not available for a cargo tool.

    Args:
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {
                    PackageManager.UV,
                    PackageManager.PIP,
                    PackageManager.NPM,
                    PackageManager.RUSTUP,
                },
            ),
        ),
        is_ci=False,
    )
    inst = ToolInstaller(registry, ctx)
    tool = make_tool(install_type="cargo")

    result = inst._check_prerequisites(tool)

    assert_that(result).is_not_none()
    assert_that(result).contains("cargo")


def test_check_prerequisites_npm_missing(
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return skip reason when npm and bun are both unavailable for npm tool.

    Args:
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {
                    PackageManager.UV,
                    PackageManager.PIP,
                    PackageManager.CARGO,
                    PackageManager.RUSTUP,
                },
            ),
        ),
        is_ci=False,
    )
    inst = ToolInstaller(registry, ctx)
    tool = make_tool(install_type="npm", name="eslint")

    result = inst._check_prerequisites(tool)

    assert_that(result).is_not_none()
    assert_that(result).contains("npm")


def test_check_prerequisites_pip_missing(
    registry: MagicMock,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return skip reason when uv and pip are both unavailable for pip tool.

    Args:
        registry: Mock ToolRegistry.
        make_tool: ManifestTool factory.
    """
    from lintro.tools.core.install_strategies.environment import InstallEnvironment

    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset(
                {PackageManager.NPM, PackageManager.CARGO, PackageManager.RUSTUP},
            ),
        ),
        is_ci=False,
    )
    inst = ToolInstaller(registry, ctx)
    tool = make_tool(install_type="pip", name="ruff")

    result = inst._check_prerequisites(tool)

    assert_that(result).is_not_none()
    assert_that(result).contains("uv/pip")


def test_check_prerequisites_all_met(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return None when all prerequisites are met.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip")

    result = installer._check_prerequisites(tool)

    assert_that(result).is_none()


# ---------------------------------------------------------------------------
# _get_install_command
# ---------------------------------------------------------------------------


def test_get_install_command_delegates_to_context(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Generate an install command by delegating to context.install_hint_for_tool.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip", install_package="mypkg", version="1.0.0")

    result = installer._get_install_command(tool)

    # Context has_uv=True so prefix is "uv pip install"
    assert_that(result).contains("pip install")
    assert_that(result).contains("mypkg")


def test_get_install_command_upgrade_pip(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Add --upgrade flag for pip tools when upgrade=True.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip", install_package="mypkg", version="2.0.0")

    result = installer._get_install_command(tool, upgrade=True)

    assert_that(result).contains("--upgrade")
    assert_that(result).contains("mypkg")


def test_get_install_command_upgrade_cargo(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Add --force flag for cargo tools when upgrade=True.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="cargo", install_package="cargo-pkg")

    result = installer._get_install_command(tool, upgrade=True)

    assert_that(result).contains("--force")
    assert_that(result).contains("cargo install")


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


def test_execute_runs_installs_and_upgrades(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Execute runs _run_install for each to_install and to_upgrade entry.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool_a = make_tool(name="tool_a")
    tool_b = make_tool(name="tool_b")

    plan = InstallPlan(
        to_install=[(tool_a, "pip install tool_a")],
        to_upgrade=[(tool_b, "1.0.0", "pip install --upgrade tool_b")],
    )

    fake_result = InstallResult(
        tool=tool_a,
        success=True,
        message="ok",
        duration_seconds=0.1,
    )

    with patch.object(installer, "_run_install", return_value=fake_result) as mock_run:
        results = installer.execute(plan)

    assert_that(mock_run.call_count).is_equal_to(2)
    assert_that(results).is_length(2)


def test_execute_empty_plan(installer: ToolInstaller) -> None:
    """Return an empty list when the plan has no work.

    Args:
        installer: ToolInstaller fixture.
    """
    plan = InstallPlan()

    results = installer.execute(plan)

    assert_that(results).is_empty()


# ---------------------------------------------------------------------------
# _run_install
# ---------------------------------------------------------------------------


def test_run_install_success(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return a successful InstallResult when subprocess returns 0.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip")

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = installer._run_install(tool, "pip install faketool-pkg>=1.2.0")

    assert_that(result.success).is_true()
    assert_that(result.message).contains("successfully")
    assert_that(result.duration_seconds).is_greater_than_or_equal_to(0.0)


def test_run_install_failure(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return a failed InstallResult when subprocess returns non-zero.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip")

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: package not found",
        )
        result = installer._run_install(tool, "pip install faketool-pkg>=1.2.0")

    assert_that(result.success).is_false()
    assert_that(result.message).contains("failed")


def test_run_install_timeout(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return a timeout InstallResult when subprocess times out.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip")

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=300)
        result = installer._run_install(tool, "pip install faketool-pkg>=1.2.0")

    assert_that(result.success).is_false()
    assert_that(result.message).contains("timed out")


def test_run_install_os_error(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return an OS error InstallResult when subprocess raises OSError.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="pip")

    with patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("No such file or directory")
        result = installer._run_install(tool, "pip install faketool-pkg>=1.2.0")

    assert_that(result.success).is_false()
    assert_that(result.message).contains("OS error")


def test_run_install_manual_hint_rejected(
    installer: ToolInstaller,
    make_tool: Callable[..., ManifestTool],
) -> None:
    """Return failure when a manual hint slips through to _run_install.

    Args:
        installer: ToolInstaller fixture.
        make_tool: ManifestTool factory.
    """
    tool = make_tool(install_type="binary")

    with patch.object(installer, "_install_via_script", return_value=None):
        result = installer._run_install(
            tool,
            "See https://github.com/example/releases",
        )

    assert_that(result.success).is_false()
    assert_that(result.message).contains("Manual install required")


# ---------------------------------------------------------------------------
# _is_brew_managed (static)
# ---------------------------------------------------------------------------


def test_is_brew_managed_true() -> None:
    """Return True when brew list returns exit code 0.

    Simulates Homebrew managing the package.
    """
    with (
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/brew",
        ),
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = ToolInstaller._is_brew_managed("faketool")

    assert_that(result).is_true()


def test_is_brew_managed_false() -> None:
    """Return False when brew list returns non-zero exit code.

    Simulates a package not managed by Homebrew.
    """
    with (
        patch(
            "lintro.tools.core.tool_installer.shutil.which",
            return_value="/usr/local/bin/brew",
        ),
        patch("lintro.tools.core.tool_installer.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1)
        result = ToolInstaller._is_brew_managed("faketool")

    assert_that(result).is_false()


def test_is_brew_managed_no_brew() -> None:
    """Return False when brew is not in PATH.

    Simulates a system without Homebrew installed.
    """
    with patch("lintro.tools.core.tool_installer.shutil.which", return_value=None):
        result = ToolInstaller._is_brew_managed("faketool")

    assert_that(result).is_false()
