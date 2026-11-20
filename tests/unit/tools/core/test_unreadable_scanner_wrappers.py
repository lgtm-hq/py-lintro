"""An unreadable scanner version is refused however the scanner is launched (#3568).

The PATH case is covered next door. These cases launch the scanner through
another command: ``python -m bandit`` with no console script, which leaves the
snapshot with no resolved executable path, and ``cargo <subcommand>``, where the
path is cargo's. In both the version probe runs and exits 0 but prints no
version, and the run must fail rather than skip the scanner quietly.
"""

from __future__ import annotations

import json
import sys
import sysconfig
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.models.core.tool_outcome import Unavailable
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.cargo_audit.definition import CargoAuditPlugin
from lintro.tools.cargo_deny.definition import CargoDenyPlugin
from lintro.tools.core import snapshots


@pytest.fixture
def module_only_bandit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make ``python -m bandit`` a stub that prints no version, with no script.

    The stub package goes first on ``PYTHONPATH``; the interpreter's scripts
    directory and PATH are empty directories, so the command resolver falls
    back to ``sys.executable -m bandit`` and resolves no executable path.

    Args:
        tmp_path: Directory for the stub, the empty directories and the project.
        monkeypatch: Sets PYTHONPATH, PATH and the scripts directory.

    Returns:
        A project directory holding one Python file.
    """
    stub = tmp_path / "stub" / "bandit"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("", encoding="utf-8")
    (stub / "__main__.py").write_text("print('bandit dev')\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    real_get_path = sysconfig.get_path

    def get_path(name: str, *args: object, **kwargs: object) -> str:
        if name == "scripts":
            return str(empty)
        return real_get_path(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sysconfig, "get_path", get_path)
    monkeypatch.setenv("PYTHONPATH", str(stub.parent))
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    return project


def _bandit_definition() -> ToolDefinition:
    """Return the registered bandit definition.

    Returns:
        bandit's tool definition.
    """
    from lintro.tools import tool_manager

    return tool_manager.get_tool("bandit").definition


def test_module_only_bandit_is_launched_without_an_executable_path(
    module_only_bandit: Path,
) -> None:
    """The setup really is the module-only case: the probe ran, no path resolved.

    Args:
        module_only_bandit: Project directory with the stub in place.
    """
    snap = snapshots.get_tool_snapshot("bandit", cache_root=module_only_bandit)

    assert_that(list(snap.command)).is_equal_to([sys.executable, "-m", "bandit"])
    assert_that(snap.binary_path).is_empty()
    assert_that(snap.version_unreadable).is_true()
    assert_that(snap.probe_error).does_not_contain("not found in PATH")


def test_module_only_bandit_with_unreadable_version_fails_the_run(
    module_only_bandit: Path,
) -> None:
    """The gate refuses it strictly and quotes what it printed.

    Args:
        module_only_bandit: Project directory with the stub in place.
    """
    result = verify_tool_version(_bandit_definition(), cwd=module_only_bandit)

    assert result is not None  # narrow: a refused version returns a result
    execution = result.outcome.execution
    assert isinstance(execution, Unavailable)  # narrow for the attribute reads
    assert_that(execution.strict).is_true()
    assert_that(execution.reason).starts_with(
        "security scanner did not run: Could not read a version from the output "
        f'of `{sys.executable} -m bandit --version`, which printed "bandit dev".',
    )


def test_module_only_bandit_check_command_exits_non_zero(
    module_only_bandit: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro check --tools bandit`` fails, and its JSON says bandit did not run.

    Args:
        module_only_bandit: Project directory with the stub in place.
        monkeypatch: Runs the command from the project directory.
    """
    monkeypatch.chdir(module_only_bandit)

    outcome = CliRunner().invoke(
        cli,
        ["check", ".", "--tools", "bandit", "--output-format", "json"],
    )

    assert_that(outcome.exit_code).is_not_equal_to(0)
    start = outcome.output.index("{")
    data = json.loads(outcome.output[start : outcome.output.rindex("}") + 1])
    bandit = next(r for r in data["results"] if r["tool"] == "bandit")
    assert_that(bandit).contains_entry({"status": "unavailable"}, {"success": False})
    assert_that(bandit["unavailable_reason"]).contains(
        "security scanner did not run",
        '"bandit dev"',
    )


@pytest.mark.parametrize(
    ("definition", "printed"),
    [
        (CargoAuditPlugin().definition, "cargo-audit-audit dev"),
        (CargoDenyPlugin().definition, "cargo-deny dev"),
    ],
    ids=["cargo-audit", "cargo-deny"],
)
def test_cargo_subcommand_scanner_with_unreadable_version_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    definition: ToolDefinition,
    printed: str,
) -> None:
    """A ``cargo <subcommand>`` scanner printing no version is refused too.

    Args:
        tmp_path: Directory for the fake cargo and the cache.
        monkeypatch: Sets PATH and clears the version-lag override.
        definition: The cargo scanner's definition.
        printed: What the fake cargo prints for ``<subcommand> --version``.
    """
    cargo = tmp_path / "cargo"
    cargo.write_text(f"#!/bin/sh\necho '{printed}'\n", encoding="utf-8")
    cargo.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)

    result = verify_tool_version(definition, cwd=tmp_path)

    assert result is not None  # narrow: a refused version returns a result
    assert_that(result.success).is_false()
    assert_that(result.unavailable_reason).contains(
        "security scanner did not run",
        f'"{printed}"',
    )
