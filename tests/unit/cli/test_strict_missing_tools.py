"""``--strict-missing-tools`` through the real ``lintro chk`` / ``lintro fmt`` CLI.

Only the capability probe is replaced, so the version gate, outcome model,
exit code and renderers all run as shipped (#3371).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner, Result

from lintro.cli import cli
from lintro.config.config_loader import get_config
from lintro.enums.probe_failure import ProbeFailure
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils import tool_snapshots

Probe = Callable[..., ToolSnapshot]

_INSTALL_HINT = "Install via: https://example.invalid/install (v2.14.0+)"
_UPGRADE_HINT = "Upgrade via: https://example.invalid/upgrade (v2.14.0+)"


def _missing(name: str, **kwargs: object) -> ToolSnapshot:
    """Report a tool whose binary is not on PATH.

    Args:
        name: Tool under inspection.
        **kwargs: Ignored probe options.

    Returns:
        Unavailable evidence carrying an install hint.
    """
    return ToolSnapshot(
        name=name,
        available=False,
        probe_error=f"{name} not found in PATH",
        remediation_hint=_INSTALL_HINT,
        probe_failure=ProbeFailure.BINARY_MISSING,
    )


def _too_old(name: str, **kwargs: object) -> ToolSnapshot:
    """Report an installed tool below lintro's minimum version.

    Args:
        name: Tool under inspection.
        **kwargs: Ignored probe options.

    Returns:
        Available evidence that fails the version check.
    """
    return ToolSnapshot(
        name=name,
        available=True,
        version="2.0.0",
        version_check_passed=False,
        min_version="2.14.0",
        probe_error=f"{name} 2.0.0 is below minimum 2.14.0",
        remediation_hint=_UPGRADE_HINT,
        binary_path=f"/opt/old/{name}",
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project with one Dockerfile and one shell script.

    Args:
        tmp_path: Isolated project directory.
        monkeypatch: Working-directory and environment control.

    Returns:
        The project root, which is also the working directory.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    (tmp_path / "Dockerfile").write_text("FROM alpine:3.20\nRUN echo hi\n")
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho hi\n")
    return tmp_path


def _invoke(monkeypatch: pytest.MonkeyPatch, probe: Probe, *args: str) -> Result:
    """Run the top-level CLI with ``probe`` standing in for capability probing.

    Args:
        monkeypatch: Probe replacement control.
        probe: Snapshot factory for every tool.
        *args: CLI arguments after ``lintro``.

    Returns:
        The Click result.
    """
    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    return CliRunner().invoke(cli, [*args, "--no-art"], catch_exceptions=False)


def _json_row(output: str, tool: str) -> dict[str, Any]:
    """Pick one tool's object out of ``--output-format json`` stdout.

    Args:
        output: Captured stdout.
        tool: Tool name to select.

    Returns:
        The per-tool JSON object.
    """
    payload = json.loads(output[output.index("{") :])
    rows: list[dict[str, Any]] = payload["results"]
    return next(row for row in rows if row["tool"] == tool)


@pytest.mark.parametrize("probe", [_missing, _too_old], ids=["missing", "too-old"])
def test_default_mode_keeps_skip_and_exit_zero(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe: Probe,
) -> None:
    """Without strict mode an absent or outdated tool still passes the run.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
        probe: Missing or below-minimum evidence.
    """
    result = _invoke(monkeypatch, probe, "chk", "--tools", "hadolint", str(project))

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("hadolint", "2.14.0")


@pytest.mark.parametrize(
    ("probe", "reason", "hint"),
    [
        (_missing, "hadolint not found in PATH", _INSTALL_HINT),
        (_too_old, "hadolint 2.0.0 is below minimum 2.14.0", _UPGRADE_HINT),
    ],
    ids=["missing", "too-old"],
)
def test_strict_flag_fails_chk_with_reason_and_hint(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe: Probe,
    reason: str,
    hint: str,
) -> None:
    """``--strict-missing-tools`` turns the skip into a failing row with a hint.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
        probe: Missing or below-minimum evidence.
        reason: Why the tool could not run.
        hint: Install or upgrade guidance the row must carry.
    """
    result = _invoke(
        monkeypatch,
        probe,
        "chk",
        "--strict-missing-tools",
        "--tools",
        "hadolint",
        str(project),
    )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("UNAVAIL", reason, hint)


@pytest.mark.parametrize(
    ("probe", "hint"),
    [(_missing, _INSTALL_HINT), (_too_old, _UPGRADE_HINT)],
    ids=["missing", "too-old"],
)
def test_strict_json_marks_tool_unavailable_without_findings(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe: Probe,
    hint: str,
) -> None:
    """Strict JSON reports a failed, unavailable tool and invents no issue.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
        probe: Missing or below-minimum evidence.
        hint: Install or upgrade guidance the reason must carry.
    """
    result = _invoke(
        monkeypatch,
        probe,
        "chk",
        "--strict-missing-tools",
        "--output-format",
        "json",
        "--tools",
        "hadolint",
        str(project),
    )

    row = _json_row(result.stdout, "hadolint")
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(row).contains_entry(
        {"status": "unavailable"},
        {"success": False},
        {"skipped": False},
        {"unavailable": True},
        {"issues_count": 0},
    )
    assert_that(row["unavailable_reason"]).starts_with("hadolint ").ends_with(hint)


def test_default_json_keeps_below_minimum_as_skipped(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without strict mode the JSON contract for an outdated tool is unchanged.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
    """
    result = _invoke(
        monkeypatch,
        _too_old,
        "chk",
        "--output-format",
        "json",
        "--tools",
        "hadolint",
        str(project),
    )

    row = _json_row(result.stdout, "hadolint")
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(row).contains_entry(
        {"status": "skipped"},
        {"success": True},
        {"skipped": True},
        {"skip_reason": "hadolint 2.0.0 is below minimum 2.14.0"},
    )


def test_strict_with_no_matching_files_still_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool with nothing to check is not a strict failure, even if absent.

    Args:
        tmp_path: Project without any Dockerfile.
        monkeypatch: Probe replacement control.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("nothing for hadolint\n")

    result = _invoke(
        monkeypatch,
        _missing,
        "chk",
        "--strict-missing-tools",
        "--tools",
        "hadolint",
        str(tmp_path),
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("UNAVAIL", _INSTALL_HINT)


def test_strict_config_key_fails_below_minimum_tool(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``execution.strict_missing_tools: true`` now covers outdated binaries too.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
    """
    (project / ".lintro-config.yaml").write_text(
        "execution:\n  strict_missing_tools: true\n",
    )

    result = _invoke(monkeypatch, _too_old, "chk", "--tools", "hadolint", str(project))

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("UNAVAIL", _UPGRADE_HINT)


def test_no_strict_flag_overrides_config_for_one_run(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--no-strict-missing-tools`` wins for one run and leaves config intact.

    Args:
        project: Project with a Dockerfile.
        monkeypatch: Probe replacement control.
    """
    (project / ".lintro-config.yaml").write_text(
        "execution:\n  strict_missing_tools: true\n",
    )

    result = _invoke(
        monkeypatch,
        _missing,
        "chk",
        "--no-strict-missing-tools",
        "--tools",
        "hadolint",
        str(project),
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(get_config().execution.strict_missing_tools).is_true()


def test_strict_flag_fails_fmt_for_missing_formatter(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro fmt --strict-missing-tools`` fails when the formatter is absent.

    Args:
        project: Project with a shell script.
        monkeypatch: Probe replacement control.
    """
    result = _invoke(
        monkeypatch,
        _missing,
        "fmt",
        "--strict-missing-tools",
        "--tools",
        "shfmt",
        str(project),
    )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("shfmt not found in PATH", _INSTALL_HINT)
