"""Registry-wide contract: failed or unusable reports cannot imply a clean run.

Patch the stdlib process boundary so discovery, command building, parsing and
ToolResult construction still run. Strict expected failures are a burn-down
list: remove an entry when its linked defect is fixed.
"""

from __future__ import annotations

import io
import re
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from typing import NamedTuple
from unittest.mock import MagicMock

import pytest
from _pytest.mark.structures import ParameterSet
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.registry import ToolRegistry

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
JSON_REPORTS: dict[str, tuple[str, str]] = {
    "bandit": ('{"results": null, "errors": []}', '{"results": [], "errors": []}'),
    "cargo_audit": (
        '{"vulnerabilities": {"found": false, "count": 0, "list": null}}',
        '{"vulnerabilities": {"found": false, "count": 0, "list": []}}',
    ),
    "checkov": (
        '{"results": {"failed_checks": null}}',
        '{"results": {"failed_checks": []}}',
    ),
    "golangci_lint": ('{"Issues": null}', '{"Issues": []}'),
    "html_validate": ("null", "[]"),
    "osv_scanner": ('{"results": null}', '{"results": []}'),
    "oxlint": ('{"diagnostics": null}', '{"diagnostics": []}'),
    "pip_audit": ('{"dependencies": null}', '{"dependencies": []}'),
    "pylint": (
        '{"messages": null, "statistics": {}}',
        '{"messages": [], "statistics": {}}',
    ),
    "ruff": ("null", "[]"),
    "semgrep": ('{"results": null, "errors": []}', '{"results": [], "errors": []}'),
    "shellcheck": ('{"comments": null}', '{"comments": []}'),
    "spectral": ("null", "[]"),
    "sqlfluff": ("null", "[]"),
    "stylelint": ("null", "[]"),
    "vale": ("null", "{}"),
}

NO_JSON_REPORT: dict[str, str] = {
    "actionlint": "Text output.",
    "astro-check": "Text output.",
    "black": "Text output.",
    "buf": "JSON Lines diagnostics; no primary container.",
    "cargo_deny": "JSON Lines diagnostics; no primary container.",
    "clippy": "Cargo JSON Lines messages; no primary container.",
    "commitlint": "Text output.",
    "cppcheck": "XML output.",
    "dotenv_linter": "Text output.",
    "gitleaks": "JSON report is read from a file, not stdout.",
    "hadolint": "Default check uses tty text output.",
    "idiom-review": "No subprocess report; disabled AI review.",
    "import-linter": "Text output.",
    "markdownlint": "Text output.",
    "mypy": "Text output.",
    "oxfmt": "Text output.",
    "prettier": "Text output.",
    "pydoclint": "Text output.",
    "pytest": "Text output; optional JSON report is a file artifact.",
    "rustfmt": "Text diff output.",
    "shfmt": "Text diff output.",
    "svelte-check": "Machine-readable text output, not JSON.",
    "taplo": "Text output.",
    "trufflehog": "JSON Lines findings; no primary container.",
    "tsc": "Text output.",
    "typos": "JSON Lines findings; no primary container.",
    "vue-tsc": "Text output.",
    "yamllint": "Parsable text output, not JSON.",
}

# Empty stdout is the default clean output for tools without an envelope.
CLEAN_OUTPUTS = {name: reports[1] for name, reports in JSON_REPORTS.items()} | {
    "gitleaks": "[]",
    "semgrep": '{"results": [], "errors": [], "paths": {"scanned": ["test.py"]}}',
}
CLEAN_SUCCESS_EXCLUSIONS: dict[str, str] = {}


class KnownFailOpen(NamedTuple):
    """A measured defect's issue reference and narrowly expected exception."""

    reason: str
    raises: type[Exception] = AssertionError


# Measured from the unmarked first run.
# Keep exit-code variants separate so a fix cannot hide behind another variant.
KNOWN_FAIL_OPEN: dict[tuple[str, str], KnownFailOpen] = {
    ("bandit", "case2-rc0"): KnownFailOpen("#3024 null results read as clean"),
    ("bandit", "case2-rc1"): KnownFailOpen("#3024 null results read as clean"),
    ("checkov", "case2-rc0"): KnownFailOpen("#3026 null failed_checks read as clean"),
    ("golangci_lint", "case2-rc0"): KnownFailOpen("#3026 null Issues read as clean"),
    ("html_validate", "case2-rc0"): KnownFailOpen("#3026 null report reads as clean"),
    ("shellcheck", "case2-rc0"): KnownFailOpen(
        "#3025 null comments crash the parser",
        TypeError,
    ),
    ("shellcheck", "case2-rc1"): KnownFailOpen(
        "#3025 null comments crash the parser",
        TypeError,
    ),
    ("sqlfluff", "case2-rc0"): KnownFailOpen("#3026 null report reads as clean"),
    ("stylelint", "case2-rc0"): KnownFailOpen("#3026 null report reads as clean"),
    ("vale", "case2-rc0"): KnownFailOpen("#3026 null report reads as clean"),
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
) -> ParameterSet:
    """Attach a strict expected failure only to a measured defective pair.

    Args:
        tool_name: Registered plugin name.
        case: Failure shape and exit-code variant.
        returncode: Simulated process exit code.
        stdout: Simulated report or error output.

    Returns:
        A pytest parameter with a stable, readable id.
    """
    defect = KNOWN_FAIL_OPEN.get((tool_name, case))
    marks: list[pytest.MarkDecorator] = []
    if defect:
        marks.append(
            pytest.mark.xfail(
                strict=True,
                reason=defect.reason,
                raises=defect.raises,
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
        for rc in (0, 1)
    ]
    + [_failed_run_param("cargo_audit", f"case2-blank-rc{rc}", rc, "") for rc in (0, 1)]
)


def _fallback_layout(tool_name: str, patterns: list[str]) -> dict[str, str]:
    """Derive a concrete filename only from an unambiguous file pattern.

    Args:
        tool_name: Plugin needing a project layout.
        patterns: The plugin's declared discovery patterns.

    Returns:
        A minimal project with one discoverable file.
    """
    if not patterns:
        pytest.fail(f"{tool_name}: add an explicit PROJECT_LAYOUTS entry (no patterns)")
    pattern = patterns[0]
    while pattern.startswith("**/"):
        pattern = pattern.removeprefix("**/")
    if pattern.count("*") > 1 or any(char in pattern for char in "?[]"):
        pytest.fail(
            f"{tool_name}: add an explicit PROJECT_LAYOUTS entry for {pattern!r}",
        )
    return {pattern.replace("*", "contract"): "\n"}


def _run_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_name: str,
    returncode: int,
    stdout: str,
) -> tuple[ToolResult, list[list[str]]]:
    """Run the actual plugin against real files and a fake stdlib process.

    Args:
        monkeypatch: Fixture restoring process patches and working directory.
        tmp_path: Isolated project directory.
        tool_name: Registered plugin to instantiate with fresh option state.
        returncode: Exit code for both buffered and streaming process paths.
        stdout: Output for both process paths.

    Returns:
        The plugin's observable check result and every process argv.
    """
    plugin = type(ToolRegistry.get(tool_name))()
    layout = PROJECT_LAYOUTS.get(tool_name)
    if layout is None:
        layout = _fallback_layout(tool_name, plugin.definition.file_patterns)
    for relative_path, content in layout.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    commands: list[list[str]] = []

    def record(args: list[str]) -> None:
        """Record argv and emulate tools that write their report to a file.

        Args:
            args: Command assembled by the real plugin.
        """
        commands.append(list(args))
        if tool_name == "gitleaks" and "--report-path" in args:
            report_path = Path(args[args.index("--report-path") + 1])
            report_path.write_text(stdout, encoding="utf-8")

    def completed(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str | bytes]:
        """Return the requested process result without launching a tool.

        Args:
            args: Command assembled by the real plugin.
            **kwargs: Subprocess settings, including text mode and check.

        Returns:
            A completed process with the requested exit code and output.

        Raises:
            subprocess.CalledProcessError: If check is true and the exit is nonzero.
        """
        record(args)
        if tool_name == "black" and args[1:6] == [
            "check",
            "--select",
            "E501",
            "--output-format",
            "json",
        ]:
            # Black's text report and its optional Ruff JSON report are
            # independent subprocesses; this harness varies Black's verdict.
            # Supplement failures are covered in black/test_execution.py.
            return subprocess.CompletedProcess[str | bytes](args, 0, "[]", "")
        text_mode = kwargs.get("text", kwargs.get("universal_newlines", False))
        output = stdout if text_mode else stdout.encode("utf-8")
        stderr = "" if text_mode else b""
        if kwargs.get("check") and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                args,
                output=output,
                stderr=stderr,
            )
        return subprocess.CompletedProcess[str | bytes](
            args,
            returncode,
            output,
            stderr,
        )

    def popen(args: list[str], **kwargs: object) -> MagicMock:
        """Give every streaming invocation an independent process and stream.

        Args:
            args: Command assembled by the real plugin.
            **kwargs: Subprocess settings, including text mode.

        Returns:
            A fresh process with unread output and the requested exit code.
        """
        record(args)
        text_mode = kwargs.get("text", kwargs.get("universal_newlines", False))
        process = MagicMock()
        process.stdout = (
            io.StringIO(stdout) if text_mode else io.BytesIO(stdout.encode())
        )
        process.returncode = returncode
        process.wait.return_value = returncode
        process.poll.return_value = returncode
        process.communicate.return_value = (
            (stdout, "") if text_mode else (stdout.encode(), b"")
        )
        process.__enter__.return_value = process
        return process

    monkeypatch.setattr("subprocess.run", completed)
    monkeypatch.setattr("subprocess.Popen", MagicMock(side_effect=popen))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/mock/bin/tool"))
    for gate in (
        "lintro.plugins.execution_preparation.verify_tool_version",
        "lintro.plugins.base.verify_tool_version",
        "lintro.tools.osv_scanner.definition.verify_tool_version",
    ):
        monkeypatch.setattr(gate, MagicMock(return_value=None))
    return plugin.check([str(tmp_path)], {}), commands


def _assert_tool_ran(
    tool_name: str,
    result: ToolResult,
    commands: list[list[str]],
) -> None:
    """Require discovered targets and an actual check invocation without a skip.

    Args:
        tool_name: Plugin under test, for actionable diagnostics.
        result: Observable plugin result.
        commands: All argv recorded at the stdlib process boundary.
    """
    if result.no_files:
        pytest.fail(f"{tool_name}: harness did not discover targets: {result.output}")
    # Harness preconditions must fail even for a known fail-open parameter.
    try:
        assert_that(
            any(
                not {"--version", "-V", "version"}.intersection(argv)
                for argv in commands
            ),
        ).described_as(f"{tool_name}: no check command ran: {commands}").is_true()
        assert_that(result.skipped).described_as(f"{tool_name}: {result}").is_false()
    except AssertionError as exc:
        pytest.fail(str(exc))


def test_registry_has_explicit_failed_run_coverage() -> None:
    """Require a subprocess coverage decision and a JSON decision for every tool."""
    registered = set(ToolRegistry.get_all())
    assert_that(registered).is_not_empty()
    assert_that(CASE1_TOOLS & CASE1_EXCLUSIONS.keys()).is_empty()
    assert_that(CASE1_TOOLS | CASE1_EXCLUSIONS.keys()).is_equal_to(registered)
    assert_that(JSON_REPORTS.keys() & NO_JSON_REPORT.keys()).is_empty()
    assert_that(JSON_REPORTS.keys() | NO_JSON_REPORT.keys()).is_equal_to(registered)
    assert_that(set(CLEAN_OUTPUTS) - CASE1_TOOLS).is_empty()
    assert_that(set(CLEAN_SUCCESS_EXCLUSIONS) - CASE1_TOOLS).is_empty()
    for reasons in (CASE1_EXCLUSIONS, NO_JSON_REPORT, CLEAN_SUCCESS_EXCLUSIONS):
        assert_that(all(reason.strip() for reason in reasons.values())).is_true()
    covered_pairs = {(param.values[0], param.values[1]) for param in FAILED_RUNS}
    assert_that(set(KNOWN_FAIL_OPEN) - covered_pairs).is_empty()
    for defect in KNOWN_FAIL_OPEN.values():
        assert_that(re.match(r"^#\d+\b", defect.reason)).is_not_none()


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
    result, commands = _run_tool(
        monkeypatch,
        tmp_path,
        tool_name,
        returncode,
        stdout,
    )
    _assert_tool_ran(tool_name, result, commands)
    explained = result.timed_out or (result.parse_failures_count or 0) > 0
    assert_that(
        result.success is True and result.issues_count == 0 and not explained,
    ).described_as(f"{tool_name} {case}: {result}").is_false()


@pytest.mark.parametrize(
    "tool_name",
    sorted(CASE1_TOOLS - CLEAN_SUCCESS_EXCLUSIONS.keys()),
)
def test_clean_run_is_a_clean_success(
    tool_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prove the same harness accepts every tool's clean output on exit zero.

    Args:
        tool_name: Subprocess plugin under test.
        monkeypatch: Fixture for process boundary and working-directory patches.
        tmp_path: Real project files used by discovery.
    """
    result, commands = _run_tool(
        monkeypatch,
        tmp_path,
        tool_name,
        0,
        CLEAN_OUTPUTS.get(tool_name, ""),
    )
    _assert_tool_ran(tool_name, result, commands)
    assert_that(result.timed_out).is_false()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.success).described_as(str(result)).is_true()
    assert_that(result.issues_count).is_zero()
    assert_that(result.inspected_files).is_true()
