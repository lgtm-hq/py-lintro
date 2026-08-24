"""Tests for the ``lintro doctor`` CLI command."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger

from lintro.ai.config import AIConfig
from lintro.cli_utils.commands.doctor import (
    _generate_markdown_report,
    _output_json,
    doctor_command,
)
from lintro.config.lintro_config import LintroConfig
from lintro.enums.install_context import InstallContext, PackageManager
from lintro.enums.install_outcome import InstallOutcome
from lintro.enums.tool_status import ToolStatus
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.install_plan import InstallPlan, InstallResult
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.tool_registry import ManifestTool
from lintro.tools.core.update_channels import VersionAdvisory
from lintro.utils.doctor_report import ToolCheckResult, check_tool

# ── Helpers ──────────────────────────────────────────────────────────


def _make_tool(
    name: str = "ruff",
    version: str = "0.14.0",
    min_version: str | None = None,
    *,
    install_type: str = "pip",
    install_package: str | None = None,
    install_bin: str | None = None,
    update_channel: str | None = None,
    tier: str = "tools",
    category: str = "bundled",
    version_command: tuple[str, ...] | None = None,
) -> ManifestTool:
    """Build a ManifestTool for testing."""
    return ManifestTool(
        name=name,
        version=version,
        min_version=min_version or version,
        install_type=install_type,
        install_package=install_package,
        install_bin=install_bin,
        update_channel=update_channel,
        tier=tier,
        category=category,
        version_command=(
            (name, "--version") if version_command is None else version_command
        ),
        languages=("python",),
        tags=("linter",),
    )


def _make_context(*, has_brew: bool = False) -> RuntimeContext:
    """Build a RuntimeContext for testing."""
    managers = frozenset(
        {
            PackageManager.UV,
            PackageManager.PIP,
            PackageManager.NPM,
            PackageManager.CARGO,
            PackageManager.RUSTUP,
        },
    )
    if has_brew:
        managers = managers | {PackageManager.BREW}
    return RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=managers,
        ),
        is_ci=False,
    )


# ── check_tool advisories ────────────────────────────────────────────


def test_check_tool_outdated_enriches_advisory_from_path() -> None:
    """Outdated tools get a path-based update advisory."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    ctx = _make_context()

    with (
        patch(
            "shutil.which",
            return_value="/Users/me/.local/share/uv/tools/ruff/bin/ruff",
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert_that(result.advisory).is_not_none()
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("uv_tool")
    assert_that(result.advisory.update_command).is_equal_to("uv tool upgrade ruff")
    assert_that(result.upgrade_hint).is_equal_to(
        "uv pip install --upgrade 'ruff>=1.0.0'",
    )


def test_check_tool_honors_manifest_channel_override() -> None:
    """Manifest ``update_channel`` wins when path heuristics are UNKNOWN."""
    tool = _make_tool(
        name="hadolint",
        version="2.12.0",
        min_version="2.10.0",
        install_type="binary",
        update_channel="homebrew",
    )
    ctx = _make_context(has_brew=True)

    with (
        patch("shutil.which", return_value="/opt/mystery/bin/hadolint"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Haskell Dockerfile Linter 2.10.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("homebrew")
    assert_that(result.upgrade_hint).is_equal_to("brew upgrade hadolint")


def test_check_tool_rustc_cargo_bin_stays_rustup() -> None:
    """Rustc under ``~/.cargo/bin`` is upgraded with rustup, not cargo install."""
    tool = _make_tool(
        name="rustc",
        version="1.97.1",
        min_version="1.80.0",
        install_type="rustup",
    )
    ctx = _make_context()

    with (
        patch("shutil.which", return_value="/Users/me/.cargo/bin/rustc"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rustc 1.80.0 (abc 2024-01-01)",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("rustup")
    assert_that(result.upgrade_hint).is_equal_to("rustup update stable")


def test_check_tool_node_modules_advisory_stays_local(
    tmp_path: Path,
) -> None:
    """A project-local prettier must not get a global npm advisory command.

    ``upgrade_hint`` stays the install-strategy command. Without a Node
    project on the context, that strategy installs globally; the advisory
    records the local ``node_modules`` channel separately.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    prettier = tmp_path / "node_modules" / ".bin" / "prettier"
    prettier.parent.mkdir(parents=True)
    prettier.write_text("#!/bin/sh\n")
    tool = _make_tool(
        name="prettier",
        version="3.9.5",
        min_version="3.0.0",
        install_type="npm",
        install_package="prettier",
    )
    ctx = _make_context()

    with (
        patch("shutil.which", return_value=str(prettier)),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="3.0.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("npm")
    assert_that(result.advisory.update_command).is_equal_to(
        "npm install -D prettier@3.9.5",
    )
    assert_that(result.advisory.update_command).does_not_contain("install -g")
    assert_that(result.upgrade_hint).is_equal_to("npm install -g prettier@3.9.5")


def _which_map(mapping: dict[str, str]) -> Any:
    """Return a ``shutil.which`` stand-in for a name-to-path table.

    Args:
        mapping: Command name to absolute path.

    Returns:
        Lookup callable compatible with ``shutil.which``.
    """

    def _lookup(name: str) -> str | None:
        return mapping.get(name)

    return _lookup


def test_check_tool_cargo_audit_wrapper_is_cargo_not_rustup() -> None:
    """``cargo audit --version`` must not advise ``rustup update stable``."""
    tool = _make_tool(
        name="cargo_audit",
        version="0.22.0",
        min_version="0.20.0",
        install_type="cargo",
        install_package="cargo-audit",
        version_command=("cargo", "audit", "--version"),
    )
    ctx = _make_context()

    with (
        patch(
            "shutil.which",
            side_effect=_which_map(
                {
                    "cargo": "/Users/me/.cargo/bin/cargo",
                    "cargo-audit": "/Users/me/.cargo/bin/cargo-audit",
                },
            ),
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="cargo-audit 0.20.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("cargo")
    assert_that(result.advisory.update_command).is_equal_to(
        "cargo install --force cargo-audit",
    )
    assert_that(result.upgrade_hint).is_equal_to("cargo install --force cargo-audit")
    assert_that(result.upgrade_hint).does_not_contain("rustup")


def test_check_tool_vue_tsc_bash_wrapper_uses_vue_tsc_binary() -> None:
    """``bash`` version probes must classify vue-tsc from its own binary."""
    tool = _make_tool(
        name="vue_tsc",
        version="3.3.10",
        min_version="3.0.0",
        install_type="npm",
        install_package="vue-tsc",
        install_bin="vue-tsc",
        version_command=("bash", "scripts/ci/resolve-vue-tsc-version.sh"),
    )
    ctx = _make_context()

    with (
        patch(
            "shutil.which",
            side_effect=_which_map(
                {
                    "bash": "/bin/bash",
                    "vue-tsc": "/proj/node_modules/.bin/vue-tsc",
                },
            ),
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="3.0.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("npm")
    assert_that(result.advisory.update_command).is_equal_to(
        "npm install -D vue-tsc@3.3.10",
    )
    assert_that(result.path).is_equal_to("/proj/node_modules/.bin/vue-tsc")


def test_check_tool_pip_only_host_does_not_emit_uv() -> None:
    """A host with pip and no uv must not be told to run ``uv pip``."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset({PackageManager.PIP}),
        ),
        is_ci=False,
    )

    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert_that(result.upgrade_hint).is_equal_to(
        "pip install --upgrade 'ruff>=1.0.0'",
    )
    assert_that(result.upgrade_hint).does_not_contain("uv ")
    assert result.advisory is not None
    assert_that(result.advisory.channel).is_equal_to(UpdateChannel.STANDALONE)
    assert_that(result.advisory.update_command).is_none()


def test_check_tool_venv_pip_only_does_not_emit_uv() -> None:
    """A ``.venv`` binary on a pip-only host must not emit ``uv pip``."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    ctx = RuntimeContext(
        install_context=InstallContext.PIP,
        platform_label="Linux x86_64",
        environment=InstallEnvironment(
            install_context=InstallContext.PIP,
            available_managers=frozenset({PackageManager.PIP}),
        ),
        is_ci=False,
    )

    def fake_which(name: str) -> str | None:
        if name == "ruff":
            return "/proj/.venv/bin/ruff"
        return None

    with (
        patch("shutil.which", side_effect=fake_which),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert_that(result.upgrade_hint).is_equal_to(
        "pip install --upgrade 'ruff>=1.0.0'",
    )
    assert result.advisory is not None
    assert_that(result.advisory.channel).is_equal_to(UpdateChannel.PIP)
    assert_that(result.advisory.update_command).is_equal_to(
        "pip install --upgrade 'ruff>=1.0.0'",
    )
    assert_that(result.advisory.update_command).does_not_contain("uv ")


def test_doctor_outdated_prints_channel_and_strategy_hint() -> None:
    """Outdated lines show the channel as diagnostic and the strategy command."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    outdated = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OUTDATED,
        installed_version="0.5.0",
        upgrade_hint="uv pip install --upgrade 'ruff>=1.0.0'",
        advisory=VersionAdvisory(
            tool="ruff",
            installed="0.5.0",
            latest_known="1.0.0",
            channel=UpdateChannel.UV_TOOL,
            update_command="uv tool upgrade ruff",
        ),
    )

    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.doctor.collect_tool_checks",
            return_value=[outdated],
        ),
    ):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("installed via uv tool")
    assert_that(result.output.count("installed via uv tool")).is_equal_to(1)
    assert_that(result.output).does_not_contain("Update advisories")
    assert_that(result.output).contains(
        "Upgrade: uv pip install --upgrade 'ruff>=1.0.0'",
    )
    assert_that(result.output).does_not_contain("uv tool upgrade ruff")


def test_doctor_incompatible_prints_channel_and_strategy_hint() -> None:
    """Incompatible lines also keep the strategy upgrade command."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()
    tool = _make_tool(version="1.0.0", min_version="0.9.0")
    incompatible = ToolCheckResult(
        tool=tool,
        status=ToolStatus.INCOMPATIBLE,
        installed_version="0.1.0",
        upgrade_hint="pip install --upgrade 'ruff>=1.0.0'",
        advisory=VersionAdvisory(
            tool="ruff",
            installed="0.1.0",
            latest_known="1.0.0",
            channel=UpdateChannel.PIP,
            update_command="uv pip install --upgrade 'ruff>=1.0.0'",
        ),
    )

    with (
        p1,
        p2,
        patch(
            "lintro.cli_utils.commands.doctor.collect_tool_checks",
            return_value=[incompatible],
        ),
    ):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("installed via pip")
    assert_that(result.output).contains("Upgrade: pip install --upgrade 'ruff>=1.0.0'")
    assert_that(result.output).does_not_contain("uv pip")


# ── _output_json ─────────────────────────────────────────────────────


def test_output_json_includes_advisory_for_outdated_tool() -> None:
    """JSON output carries structured update advisories for outdated tools."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    advisory = VersionAdvisory(
        tool="ruff",
        installed="0.5.0",
        latest_known="1.0.0",
        channel=UpdateChannel.UV_TOOL,
        update_command="uv tool upgrade ruff",
    )
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OUTDATED,
        installed_version="0.5.0",
        upgrade_hint="uv pip install --upgrade 'ruff>=1.0.0'",
        advisory=advisory,
    )
    ctx = _make_context()

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 0, 0, 1, 0, 0)

    data = json.loads(output.getvalue())
    tool_json = data["tools"]["ruff"]
    assert_that(tool_json).contains_key("advisory")
    assert tool_json["advisory"] is not None
    assert_that(tool_json["advisory"]["channel"]).is_equal_to("uv_tool")
    assert_that(tool_json["advisory"]["update_command"]).is_equal_to(
        "uv tool upgrade ruff",
    )


def test_output_json_includes_null_advisory_when_current() -> None:
    """Doctor JSON always includes advisory so CLI and MCP share one shape."""
    tool = _make_tool(version="1.0.0", min_version="0.3.0")
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OK,
        installed_version="1.0.0",
    )
    ctx = _make_context()

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 0, 0, 0, 0, 0)

    data = json.loads(output.getvalue())
    assert_that(data["tools"]["ruff"]).contains_key("advisory")
    assert_that(data["tools"]["ruff"]["advisory"]).is_none()


def test_doctor_json_includes_advisory_from_probe() -> None:
    """``doctor --json`` surfaces advisories produced by tool checks."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch(
            "shutil.which",
            return_value="/Users/me/.local/share/uv/tools/ruff/bin/ruff",
        ),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=None,
        ),
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.5.0",
            stderr="",
        )
        result = runner.invoke(doctor_command, ["--json"])

    data = json.loads(result.output)
    advisory = data["tools"]["ruff"]["advisory"]
    assert_that(advisory).is_not_none()
    assert_that(advisory["channel"]).is_equal_to("uv_tool")
    assert_that(advisory["update_command"]).is_equal_to("uv tool upgrade ruff")


def test_check_tool_invalid_update_channel_falls_back_to_path() -> None:
    """An invalid manifest ``update_channel`` is ignored like a missing override."""
    tool = _make_tool(
        name="hadolint",
        version="2.12.0",
        min_version="2.10.0",
        install_type="binary",
        update_channel="not-a-real-channel",
    )
    ctx = _make_context(has_brew=True)

    with (
        patch("shutil.which", return_value="/opt/homebrew/bin/hadolint"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Haskell Dockerfile Linter 2.10.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("homebrew")


def test_check_tool_cargo_home_bin_is_cargo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A crate under ``CARGO_HOME/bin`` is classified as cargo, not unknown."""
    cargo_home = tmp_path / "cargo-home"
    tool_bin = cargo_home / "bin" / "cargo-audit"
    tool_bin.parent.mkdir(parents=True)
    tool_bin.write_text("#!/bin/sh\n")
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    tool = _make_tool(
        name="cargo_audit",
        version="0.22.0",
        min_version="0.20.0",
        install_type="cargo",
        install_package="cargo-audit",
        version_command=("cargo", "audit", "--version"),
    )
    ctx = _make_context()

    with (
        patch(
            "shutil.which",
            side_effect=_which_map(
                {
                    "cargo": str(cargo_home / "bin" / "cargo"),
                    "cargo-audit": str(tool_bin),
                },
            ),
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="cargo-audit 0.20.0",
            stderr="",
        )
        result = check_tool(tool=tool, context=ctx)

    assert_that(result.status).is_equal_to(ToolStatus.OUTDATED)
    assert result.advisory is not None
    assert_that(result.advisory.channel.value).is_equal_to("cargo")
    assert_that(result.advisory.update_command).is_equal_to(
        "cargo install --force cargo-audit",
    )


def test_output_json_produces_valid_json() -> None:
    """JSON output is valid and contains expected top-level keys."""
    tool = _make_tool()
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OK,
        installed_version="0.14.4",
        install_hint="uv pip install ruff>=0.14.0",
        upgrade_hint="uv pip install --upgrade ruff>=0.14.0",
    )
    ctx = _make_context()

    from io import StringIO

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 1, 0, 0, 0, 0)

    data = json.loads(output.getvalue())
    assert_that(data).contains_key("context", "tools", "issues", "summary")
    assert_that(data["summary"]["ok"]).is_equal_to(1)


def test_output_json_includes_unknown_in_issues() -> None:
    """Unknown production tools appear in the issues list."""
    tool = _make_tool()
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.UNKNOWN,
        error="no_version",
        install_hint="uv pip install ruff>=0.14.0",
        upgrade_hint="uv pip install --upgrade ruff>=0.14.0",
    )
    ctx = _make_context()

    from io import StringIO

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 0, 0, 0, 0, 1)

    data = json.loads(output.getvalue())
    assert_that(data["issues"]).is_length(1)
    assert_that(data["issues"][0]["tool"]).is_equal_to("ruff")


# ── _generate_markdown_report ────────────────────────────────────────


def test_markdown_report_contains_headers() -> None:
    """Markdown report includes Environment and Tool Versions sections."""
    env = MagicMock()
    env.lintro.version = "0.58.2"
    env.system.platform_name = "macOS"
    env.system.architecture = "arm64"
    env.python.version = "3.13.0"
    env.node = None
    env.rust = None

    ctx = _make_context()
    tool = _make_tool()
    results_by_cat = {
        "bundled": [
            ToolCheckResult(
                tool=tool,
                status=ToolStatus.OK,
                installed_version="0.14.4",
            ),
        ],
    }

    md = _generate_markdown_report(env, ctx, results_by_cat, [])
    assert_that(md).contains("### Environment")
    assert_that(md).contains("### Tool Versions")
    assert_that(md).contains("ruff")


# ── CLI invocation ───────────────────────────────────────────────────


def _patch_doctor_deps() -> tuple[Any, Any]:
    """Patch ManifestRegistry.load and RuntimeContext.detect for CLI tests.

    Returns:
        Tuple of two context-manager patches.
    """
    tool = _make_tool()
    registry = MagicMock()
    registry.all_tools = MagicMock(return_value=[tool])
    registry.__contains__ = lambda self, name: name == "ruff"
    registry.get.return_value = tool
    ctx = _make_context()

    return (
        patch(
            "lintro.cli_utils.commands.doctor.ManifestRegistry.load",
            return_value=registry,
        ),
        patch(
            "lintro.cli_utils.commands.doctor.RuntimeContext.detect",
            return_value=ctx,
        ),
    )


def test_doctor_all_ok_exit_0() -> None:
    """Exit code 0 when all tools pass."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_doctor_missing_tool_exit_1() -> None:
    """Exit code 1 when a tool is missing."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with p1, p2, patch("shutil.which", return_value=None):
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)


def test_doctor_json_output_valid() -> None:
    """--json produces valid JSON."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=None,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--json"])

    data = json.loads(result.output)
    assert_that(data).contains_key("tools", "summary")


def test_doctor_fix_incompatible_with_json() -> None:
    """--fix --json raises a usage error."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.collect_full_environment",
            return_value=MagicMock(),
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--fix", "--json"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("--fix cannot be combined")


def test_doctor_post_fix_blocks_only_non_retryable_outcomes() -> None:
    """A failed install is suppressed afterwards; a timed-out one stays retryable.

    Exercises the real path — installer outcomes flow through
    ``unresolved_tool_names`` into the follow-up quick fix — rather than
    stubbing ``_run_fix``'s return value.
    """
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    failed_tool = _make_tool(name="ruff")
    timed_out_tool = _make_tool(name="clippy", install_type="rustup")
    plan = InstallPlan(
        to_install=[
            (failed_tool, "uv pip install ruff"),
            (timed_out_tool, "rustup component add clippy"),
        ],
    )
    execute_results = [
        InstallResult(
            tool=failed_tool,
            outcome=InstallOutcome.FAILED,
            message="Command failed (exit 1)",
            command="uv pip install ruff",
            step=1,
            total_steps=2,
        ),
        InstallResult(
            tool=timed_out_tool,
            outcome=InstallOutcome.TIMED_OUT,
            message="Installation timed out (5 min)",
            command="rustup component add clippy",
            step=2,
            total_steps=2,
        ),
    ]

    with (
        p1,
        p2,
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.tool_installer.ToolInstaller.plan",
            return_value=plan,
        ),
        patch(
            "lintro.tools.core.tool_installer.ToolInstaller.execute",
            return_value=execute_results,
        ),
        patch(
            "lintro.cli_utils.commands.doctor._fixable_results",
            side_effect=lambda results: [
                ToolCheckResult(tool=failed_tool, status=ToolStatus.MISSING),
                ToolCheckResult(tool=timed_out_tool, status=ToolStatus.MISSING),
            ],
        ),
    ):
        result = runner.invoke(doctor_command, ["--fix"])

    # The failure is non-retryable, so it is blocked from the follow-up fix.
    assert_that(result.output).contains("previous attempt did not resolve it")
    blocked_section = result.output.split("Needs manual action")[-1]
    assert_that(blocked_section).contains("ruff")
    # The timeout stays retryable and is still offered.
    assert_that(result.output).contains("lintro install")
    assert_that(result.output).does_not_contain(
        "Re-running the same command will not help for: ruff, clippy",
    )


def test_doctor_post_fix_quick_fix_skips_tools_that_did_not_resolve() -> None:
    """After --fix, a tool whose command did not resolve it is not re-suggested."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("shutil.which", return_value=None),
        patch(
            "lintro.cli_utils.commands.doctor._run_fix",
            return_value=["ruff"],
        ) as mock_fix,
    ):
        result = runner.invoke(doctor_command, ["--fix"])

    assert_that(mock_fix.call_count).is_equal_to(1)
    # Suggested once before the fix attempt, and never again afterwards.
    assert_that(result.output.count("Quick fix: lintro install ruff")).is_equal_to(1)
    assert_that(result.output).contains("Needs manual action")
    assert_that(result.output).contains("previous attempt did not resolve it")


def test_doctor_quick_fix_lists_missing_tool_before_any_fix() -> None:
    """Without --fix, an installable missing tool is offered as a quick fix."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with p1, p2, patch("shutil.which", return_value=None):
        result = runner.invoke(doctor_command, [])

    assert_that(result.output).contains("Quick fix: lintro install ruff")


def test_doctor_tools_filter_known_tool() -> None:
    """--tools with a known tool name succeeds."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, ["--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("ruff")


def test_doctor_oxlint_type_aware_failure_exit_1() -> None:
    """A failing oxlint type-aware check causes exit 1 and shows the hint."""
    from lintro.tools.definitions.oxlint_doctor import OxlintCheckResult

    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    failing = [
        OxlintCheckResult(
            name="oxlint.type-aware.tsgolint",
            status=ToolStatus.MISSING,
            message="oxlint-tsgolint not resolvable (node_modules / bunx)",
            hint="bun add -d oxlint-tsgolint@latest",
        ),
    ]

    with (
        p1,
        p2,
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch(
            "lintro.cli_utils.commands.doctor.check_oxlint_type_aware",
            return_value=failing,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.14.4", stderr="")
        result = runner.invoke(doctor_command, [])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Oxlint type-aware")
    assert_that(result.output).contains("bun add -d oxlint-tsgolint@latest")


def test_doctor_unknown_tool_name_exit_1() -> None:
    """--tools with unknown name prints error and exits 1."""
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()

    with p1, p2:
        result = runner.invoke(doctor_command, ["--tools", "nonexistent"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Unknown tools")


def test_doctor_resolves_ai_checks_from_a_raw_ai_mapping() -> None:
    """Doctor parses the raw ``ai:`` mapping before running AI checks.

    Issue #724 PR 3 made ``LintroConfig.ai`` a plain dict, so ``doctor`` now
    resolves it through the AI facade. This pins that the checks still receive
    a typed configuration and that a typo'd key is reported to the user.
    """
    runner = CliRunner()
    p1, p2 = _patch_doctor_deps()
    lintro_config = LintroConfig(
        ai={"enabled": True, "lint": True, "provdier": "anthropic"},
    )
    received: list[Any] = []

    def _record(config: Any) -> list[Any]:
        """Record the AI config the doctor command resolved.

        Args:
            config: The configuration passed to the AI checks.

        Returns:
            An empty list of AI check results.
        """
        received.append(config)
        return []

    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )

    try:
        with (
            p1,
            p2,
            patch(
                "lintro.config.config_loader.get_config",
                return_value=lintro_config,
            ),
            patch(
                "lintro.cli_utils.commands.doctor.check_ai_configuration",
                side_effect=_record,
            ),
            patch("subprocess.run") as mock_run,
            patch("shutil.which", return_value="/usr/bin/ruff"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="ruff 0.14.4",
                stderr="",
            )
            result = runner.invoke(doctor_command, [])
    finally:
        logger.remove(handler_id)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(received).is_length(1)
    assert_that(received[0]).is_instance_of(AIConfig)
    assert_that(received[0].lint_enabled).is_true()
    assert_that("".join(messages)).contains(
        "Unknown AI config keys ignored: provdier",
    )


def test_output_json_includes_optional_extras() -> None:
    """JSON output carries the optional-extras block for machine consumers."""
    tool = _make_tool()
    result = ToolCheckResult(
        tool=tool,
        status=ToolStatus.OK,
        installed_version="0.14.4",
        install_hint="uv pip install ruff>=0.14.0",
        upgrade_hint="uv pip install --upgrade ruff>=0.14.0",
    )
    ctx = _make_context()

    from io import StringIO

    output = StringIO()
    with patch("click.echo", side_effect=output.write):
        _output_json([result], ctx, None, 1, 0, 0, 0, 0)

    data = json.loads(output.getvalue())
    extras = {entry["name"]: entry for entry in data["optional_extras"]}
    assert_that(extras).contains_key("mcp")
