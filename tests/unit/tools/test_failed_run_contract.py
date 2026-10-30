"""Registry-wide contract: failed or unusable reports cannot imply a clean run.

Patch the stdlib process boundary so discovery, command building, parsing and
ToolResult construction still run. Strict expected failures are a burn-down
list: remove an entry when its linked defect is fixed.
"""

from __future__ import annotations

import io
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.registry import ToolRegistry

REGISTERED_TOOLS = tuple(sorted(ToolRegistry.get_all()))

# Explicit coverage makes a new registration require a harness decision.
CASE1_TOOLS = frozenset(
    {
        "actionlint",
        "astro-check",
        "bandit",
        "black",
        "buf",
        "cargo_audit",
        "cargo_deny",
        "checkov",
        "clippy",
        "commitlint",
        "cppcheck",
        "dotenv_linter",
        "gitleaks",
        "golangci_lint",
        "hadolint",
        "html_validate",
        "import-linter",
        "markdownlint",
        "mypy",
        "osv_scanner",
        "oxfmt",
        "oxlint",
        "pip_audit",
        "prettier",
        "pydoclint",
        "pylint",
        "pytest",
        "ruff",
        "rustfmt",
        "semgrep",
        "shellcheck",
        "shfmt",
        "spectral",
        "sqlfluff",
        "stylelint",
        "svelte-check",
        "taplo",
        "trufflehog",
        "tsc",
        "typos",
        "vale",
        "vue-tsc",
        "yamllint",
    },
)
CASE1_EXCLUSIONS = {
    "idiom-review": "Disabled by default; its AI review has no subprocess check path.",
}

# Each pair is (null primary container, legitimate empty primary container).
# None entries explain why there is no whole-JSON stdout container to replace.
JSON_REPORTS: dict[str, tuple[str, str] | None] = {
    "actionlint": None,  # Text output.
    "astro-check": None,  # Text output.
    "bandit": ('{"results": null, "errors": []}', '{"results": [], "errors": []}'),
    "black": None,  # Text output.
    "buf": None,  # JSON Lines diagnostics; no primary container.
    "cargo_audit": (
        '{"vulnerabilities": {"found": false, "count": 0, "list": null}}',
        '{"vulnerabilities": {"found": false, "count": 0, "list": []}}',
    ),
    "cargo_deny": None,  # JSON Lines diagnostics; no primary container.
    "checkov": (
        '{"results": {"failed_checks": null}}',
        '{"results": {"failed_checks": []}}',
    ),
    "clippy": None,  # Cargo JSON Lines messages; no primary container.
    "commitlint": None,  # Text output.
    "cppcheck": None,  # XML output.
    "dotenv_linter": None,  # Text output.
    "gitleaks": None,  # JSON report is read from a file, not stdout.
    "golangci_lint": ('{"Issues": null}', '{"Issues": []}'),
    "hadolint": None,  # Default check uses tty text output.
    "html_validate": ("null", "[]"),
    "idiom-review": None,  # No subprocess report; disabled AI review.
    "import-linter": None,  # Text output.
    "markdownlint": None,  # Text output.
    "mypy": None,  # Text output.
    "osv_scanner": ('{"results": null}', '{"results": []}'),
    "oxfmt": None,  # Text output.
    "oxlint": ('{"diagnostics": null}', '{"diagnostics": []}'),
    "pip_audit": ('{"dependencies": null}', '{"dependencies": []}'),
    "prettier": None,  # Text output.
    "pydoclint": None,  # Text output.
    "pylint": (
        '{"messages": null, "statistics": {}}',
        '{"messages": [], "statistics": {}}',
    ),
    "pytest": None,  # Text output; optional JSON report is a file artifact.
    "ruff": ("null", "[]"),
    "rustfmt": None,  # Text diff output.
    "semgrep": ('{"results": null, "errors": []}', '{"results": [], "errors": []}'),
    "shellcheck": ('{"comments": null}', '{"comments": []}'),
    "shfmt": None,  # Text diff output.
    "spectral": ("null", "[]"),
    "sqlfluff": ("null", "[]"),
    "stylelint": ("null", "[]"),
    "svelte-check": None,  # Machine-readable text output, not JSON.
    "taplo": None,  # Text output.
    "trufflehog": None,  # JSON Lines findings; no primary container.
    "tsc": None,  # Text output.
    "typos": None,  # JSON Lines findings; no primary container.
    "vale": ("null", "{}"),
    "vue-tsc": None,  # Text output.
    "yamllint": None,  # Parsable text output, not JSON.
}

# Measured with this module's unmarked first run: 21 failed, 73 passed.
# Keep exit-code variants separate so a fix cannot hide behind another variant.
KNOWN_FAIL_OPEN: dict[tuple[str, str], str] = {
    ("cargo_deny", "case1"): "#2999 cargo-deny discards the failed exit status",
    ("oxfmt", "case1"): "#3000 batch success ignores the failed exit status",
    ("oxlint", "case1"): "#3000 batch success ignores the failed exit status",
    ("prettier", "case1"): "#3000 batch success ignores the failed exit status",
    ("bandit", "case2-rc0"): "#3024 null results read as clean",
    ("bandit", "case2-rc1"): "#3024 null results read as clean",
    ("cargo_audit", "case2-rc0"): "#3003 null vulnerability list reads as clean",
    ("checkov", "case2-rc0"): "#3026 null failed_checks read as clean",
    (
        "golangci_lint",
        "case2-rc0",
    ): "#3026 null Issues read as clean",
    (
        "html_validate",
        "case2-rc0",
    ): "#3026 null report reads as clean",
    ("oxlint", "case2-rc0"): "#3005 null diagnostics read as clean",
    ("oxlint", "case2-rc1"): "#3005 null diagnostics read as clean",
    ("ruff", "case2-rc0"): "#3026 null report reads as clean",
    ("semgrep", "case2-rc0"): "#3004 null results read as clean",
    ("semgrep", "case2-rc1"): "#3004 null results read as clean",
    ("shellcheck", "case2-rc0"): "#3025 null comments crash the parser",
    ("shellcheck", "case2-rc1"): "#3025 null comments crash the parser",
    ("sqlfluff", "case2-rc0"): "#3026 null report reads as clean",
    ("stylelint", "case2-rc0"): "#3026 null report reads as clean",
    ("vale", "case2-rc0"): "#3026 null report reads as clean",
    ("cargo_audit", "case2-blank-rc0"): "#3003 missing report reads as clean",
}

_CARGO_PROJECT = {
    "Cargo.toml": '[package]\nname = "contract"\nversion = "0.1.0"\n',
    "src/main.rs": "fn main() {}\n",
    "Cargo.lock": "version = 3\n",
}
PROJECT_LAYOUTS = {
    "actionlint": {".github/workflows/ci.yml": "name: CI\non: push\njobs: {}\n"},
    "cargo_audit": _CARGO_PROJECT,
    "cargo_deny": _CARGO_PROJECT,
    "clippy": _CARGO_PROJECT,
    "golangci_lint": {
        "go.mod": "module example.com/contract\n\ngo 1.22\n",
        "main.go": "package main\nfunc main() {}\n",
    },
    "import-linter": {
        "pyproject.toml": '[tool.importlinter]\nroot_package = "contract"\n',
        "contract/__init__.py": "",
    },
    "osv_scanner": {"requirements.txt": "requests==2.32.0\n"},
    "pip_audit": {"requirements.txt": "requests==2.32.0\n"},
    "pytest": {"test_a.py": "def test_example():\n    assert True\n"},
    "rustfmt": _CARGO_PROJECT,
    "spectral": {
        ".spectral.yaml": "extends: spectral:oas\n",
        "api.yaml": "openapi: 3.0.0\ninfo: {title: Contract, version: '1'}\npaths: {}\n",
    },
}


def _failed_run_param(
    tool_name: str,
    case: str,
    returncode: int,
    stdout: str,
) -> Any:
    """Attach a strict expected failure only to a measured defective pair.

    Args:
        tool_name: Registered plugin name.
        case: Failure shape and exit-code variant.
        returncode: Simulated process exit code.
        stdout: Simulated report or error output.

    Returns:
        A pytest parameter with a stable, readable id.
    """
    reason = KNOWN_FAIL_OPEN.get((tool_name, case))
    marks = []
    if reason:
        marks.append(
            pytest.mark.xfail(
                strict=True,
                reason=reason,
                raises=TypeError if tool_name == "shellcheck" else AssertionError,
            ),
        )
    return pytest.param(
        tool_name,
        case,
        returncode,
        stdout,
        marks=marks,
        id=f"{tool_name}-{case}",
    )


FAILED_RUNS = (
    [
        _failed_run_param(name, "case1", 1, "error: invalid configuration\n")
        for name in sorted(CASE1_TOOLS)
    ]
    + [
        _failed_run_param(name, f"case2-rc{rc}", rc, reports[0])
        for name, reports in JSON_REPORTS.items()
        if reports is not None
        for rc in (0, 1)
    ]
    + [_failed_run_param("cargo_audit", f"case2-blank-rc{rc}", rc, "") for rc in (0, 1)]
)


def _run_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_name: str,
    returncode: int,
    stdout: str,
) -> ToolResult:
    """Run the actual plugin against real files and a fake stdlib process.

    Args:
        monkeypatch: Fixture restoring process patches and working directory.
        tmp_path: Isolated project directory.
        tool_name: Registered plugin to instantiate with fresh option state.
        returncode: Exit code for both buffered and streaming process paths.
        stdout: Output for both process paths.

    Returns:
        The plugin's observable check result.
    """
    plugin = type(ToolRegistry.get(tool_name))()
    layout = PROJECT_LAYOUTS.get(tool_name)
    if layout is None:
        pattern = plugin.definition.file_patterns[0]
        layout = {pattern.replace("*", "contract"): "\n"}
    for relative_path, content in layout.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def completed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Return the requested process result without launching a tool.

        Args:
            args: Command assembled by the real plugin.
            **kwargs: Ignored subprocess execution settings.

        Returns:
            A completed process with the requested exit code and output.
        """
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    process = MagicMock()
    process.stdout = io.StringIO(stdout)
    process.returncode = returncode
    process.wait.return_value = returncode
    process.poll.return_value = returncode
    process.communicate.return_value = (stdout, "")
    process.__enter__.return_value = process
    monkeypatch.setattr("subprocess.run", completed)
    monkeypatch.setattr("subprocess.Popen", MagicMock(return_value=process))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/mock/bin/tool"))
    for gate in (
        "lintro.plugins.execution_preparation.verify_tool_version",
        "lintro.plugins.base.verify_tool_version",
        "lintro.tools.osv_scanner.definition.verify_tool_version",
    ):
        monkeypatch.setattr(gate, MagicMock(return_value=None))
    return plugin.check([str(tmp_path)], {})


def test_registry_has_explicit_failed_run_coverage() -> None:
    """Require a subprocess coverage decision and a JSON decision for every tool."""
    registered = set(ToolRegistry.get_all())
    assert_that(registered).is_not_empty().is_equal_to(set(REGISTERED_TOOLS))
    assert_that(CASE1_TOOLS & CASE1_EXCLUSIONS.keys()).is_empty()
    assert_that(CASE1_TOOLS | CASE1_EXCLUSIONS.keys()).is_equal_to(registered)
    assert_that(set(JSON_REPORTS)).is_equal_to(registered)
    assert_that(all(CASE1_EXCLUSIONS.values())).is_true()
    covered_pairs = {(param.values[0], param.values[1]) for param in FAILED_RUNS}
    assert_that(set(KNOWN_FAIL_OPEN) - covered_pairs).is_empty()


@pytest.mark.parametrize(("tool_name", "case", "returncode", "stdout"), FAILED_RUNS)
def test_failed_run_never_reports_clean_success(
    tool_name: str,
    case: str,
    returncode: int,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed or null-report run needs an observable failure or explanation.

    Args:
        tool_name: Registered plugin under test.
        case: Failure shape, including the exit-code variant for JSON reports.
        returncode: Simulated subprocess exit code.
        stdout: Invalid output or the tool's own null-container envelope.
        monkeypatch: Fixture for process boundary and working-directory patches.
        tmp_path: Real project files used by discovery.
    """
    result = _run_tool(monkeypatch, tmp_path, tool_name, returncode, stdout)
    if result.no_files:
        pytest.fail(f"{tool_name}: harness did not discover targets: {result.output}")
    explained = (
        result.timed_out or result.skipped or (result.parse_failures_count or 0) > 0
    )
    assert_that(
        result.success is True and result.issues_count == 0 and not explained,
    ).described_as(f"{tool_name} {case}: {result}").is_false()


@pytest.mark.parametrize(
    ("tool_name", "stdout"),
    [
        pytest.param(name, reports[1], id=name)
        for name, reports in JSON_REPORTS.items()
        if reports is not None
    ],
)
def test_empty_json_report_is_a_clean_success(
    tool_name: str,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prove the same harness accepts a legitimate empty report on exit zero.

    Args:
        tool_name: JSON-reporting plugin under test.
        stdout: The null case's envelope with an empty container instead.
        monkeypatch: Fixture for process boundary and working-directory patches.
        tmp_path: Real project files used by discovery.
    """
    result = _run_tool(monkeypatch, tmp_path, tool_name, 0, stdout)
    assert_that(result.no_files).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.timed_out).is_false()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.success).described_as(str(result)).is_true()
    assert_that(result.issues_count).is_zero()
