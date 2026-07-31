"""Tests for classify-lint-timeout.py (tool-execution timeout flakes, #1653)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_OUTPUT = "mypy execution timed out (120.0s limit exceeded).\n"


def _load_module() -> ModuleType:
    """Load classify-lint-timeout.py as an importable test module.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: If the module spec cannot be created.
    """
    script_path = _REPO_ROOT / "scripts" / "ci" / "classify-lint-timeout.py"
    spec = importlib.util.spec_from_file_location("classify_lint_timeout", script_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotations (evaluated lazily under
    # ``from __future__ import annotations``) can resolve via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _tool(
    name: str,
    *,
    success: bool = True,
    issues_count: int = 0,
    output: str = "",
    issues: list[dict[str, Any]] | None = None,
    skipped: bool = False,
    exit_code: int | None = None,
) -> dict[str, Any]:
    """Build one per-tool result object for a stub lintro report.

    Args:
        name: Tool name.
        success: Whether the tool run succeeded.
        issues_count: Reported issue count.
        output: Captured tool output.
        issues: Optional serialized issue list.
        skipped: Whether the tool self-skipped.
        exit_code: Optional recorded process exit code.

    Returns:
        The per-tool result dictionary.
    """
    result: dict[str, Any] = {
        "tool": name,
        "success": success,
        "issues_count": issues_count,
        "skipped": skipped,
        "skip_reason": None,
        "output": output,
    }
    if issues is not None:
        result["issues"] = issues
    if exit_code is not None:
        result["exit_code"] = exit_code
    return result


def _report(
    results: list[dict[str, Any]],
    *,
    total_issues: int = 0,
) -> dict[str, Any]:
    """Build a stub lintro JSON report.

    Args:
        results: Per-tool result objects.
        total_issues: Value for ``summary.total_issues``.

    Returns:
        The report dictionary.
    """
    return {
        "results": results,
        "summary": {
            "total_issues": total_issues,
            "total_fixed": 0,
            "total_remaining": total_issues,
        },
    }


def test_timeout_with_zero_findings_is_a_flake() -> None:
    """A timed-out tool with an otherwise clean run classifies as a flake."""
    report = _report(
        [
            _tool("ruff"),
            _tool("mypy", success=False, output=_TIMEOUT_OUTPUT),
        ],
    )

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_true()
    assert_that(verdict.timed_out_tools).is_equal_to(("mypy",))


def test_timeout_exit_code_is_recognized_without_a_message() -> None:
    """Exit 124 alone is enough evidence of a killed tool process."""
    report = _report([_tool("bandit", success=False, exit_code=124)])

    assert_that(mod.classify(report).timeout_flake).is_true()


def test_findings_alongside_a_timeout_are_never_absorbed() -> None:
    """A real finding from another tool must keep the gate red."""
    report = _report(
        [
            _tool(
                "ruff",
                success=False,
                issues_count=1,
                issues=[{"file": "a.py", "line": 1, "code": "E501", "message": "x"}],
            ),
            _tool("mypy", success=False, output=_TIMEOUT_OUTPUT),
        ],
        total_issues=1,
    )

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_false()
    assert_that(verdict.reason).contains("findings")


def test_timed_out_tool_that_reported_issues_is_never_absorbed() -> None:
    """A timeout must never mask findings the timed-out tool did report."""
    report = _report(
        [
            _tool(
                "mypy",
                success=False,
                issues_count=2,
                issues=[
                    {"file": "a.py", "line": 1, "code": "x", "message": "m"},
                    {"file": "b.py", "line": 2, "code": "y", "message": "n"},
                ],
                output=_TIMEOUT_OUTPUT,
            ),
        ],
    )

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_false()
    assert_that(verdict.reason).contains("mypy")


def test_other_tool_failure_blocks_classification() -> None:
    """A non-timeout tool failure is a real failure, not infra noise."""
    report = _report(
        [
            _tool("bandit", success=False, output="bandit crashed"),
            _tool("mypy", success=False, output=_TIMEOUT_OUTPUT),
        ],
    )

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_false()
    assert_that(verdict.reason).contains("non-timeout")


def test_clean_run_without_a_timeout_is_not_a_flake() -> None:
    """A normal passing run must not be reported as a flake."""
    report = _report([_tool("ruff"), _tool("mypy")])

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_false()
    assert_that(verdict.timed_out_tools).is_empty()


def test_skipped_tools_are_ignored() -> None:
    """Self-skipped tools carry no verdict and must not block classification."""
    report = _report(
        [
            _tool("stylelint", success=False, skipped=True, output="no config"),
            _tool("mypy", success=False, output=_TIMEOUT_OUTPUT),
        ],
    )

    assert_that(mod.classify(report).timeout_flake).is_true()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not-a-report",
        {"results": "nope"},
        {"results": []},
        {"results": [], "summary": {}},
        {"results": [], "summary": {"total_issues": "0"}},
    ],
)
def test_malformed_reports_fail_closed(payload: Any) -> None:
    """Absence of evidence is never treated as evidence of a flake."""
    assert_that(mod.classify(payload).timeout_flake).is_false()


def test_total_issues_nonzero_fails_closed() -> None:
    """A summary that reports findings blocks classification outright."""
    report = _report(
        [_tool("mypy", success=False, output=_TIMEOUT_OUTPUT)],
        total_issues=3,
    )

    assert_that(mod.classify(report).timeout_flake).is_false()


def test_unsafe_tool_names_are_sanitized() -> None:
    """Tool names are sanitized before reaching the line-oriented output file."""
    report = _report(
        [_tool("mypy\ntimeout-flake=true", success=False, output=_TIMEOUT_OUTPUT)],
    )

    verdict = mod.classify(report)

    assert_that(verdict.timeout_flake).is_true()
    assert_that(verdict.timed_out_tools).is_equal_to(("unknown",))


def test_main_writes_github_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI appends its verdict to GITHUB_OUTPUT."""
    report_path = tmp_path / "results.json"
    report_path.write_text(
        json.dumps(
            _report([_tool("mypy", success=False, output=_TIMEOUT_OUTPUT)]),
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "gh-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    exit_code = mod.main(["--report", str(report_path)])

    assert_that(exit_code).is_equal_to(0)
    written = output_path.read_text(encoding="utf-8")
    assert_that(written).contains("timeout-flake=true")
    assert_that(written).contains("timed-out-tools=mypy")


def test_main_reports_false_for_a_clean_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean report yields timeout-flake=false rather than an error."""
    report_path = tmp_path / "results.json"
    report_path.write_text(json.dumps(_report([_tool("ruff")])), encoding="utf-8")
    output_path = tmp_path / "gh-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    exit_code = mod.main(["--report", str(report_path)])

    assert_that(exit_code).is_equal_to(0)
    assert_that(output_path.read_text(encoding="utf-8")).contains("timeout-flake=false")


def test_main_returns_usage_error_for_missing_report(tmp_path: Path) -> None:
    """An unreadable report is a usage error, not a silent false verdict."""
    assert_that(mod.main(["--report", str(tmp_path / "absent.json")])).is_equal_to(2)


def test_main_fails_closed_on_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated report classifies as not-a-flake instead of crashing."""
    report_path = tmp_path / "results.json"
    report_path.write_text("{ not json", encoding="utf-8")
    output_path = tmp_path / "gh-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    exit_code = mod.main(["--report", str(report_path)])

    assert_that(exit_code).is_equal_to(0)
    assert_that(output_path.read_text(encoding="utf-8")).contains("timeout-flake=false")
