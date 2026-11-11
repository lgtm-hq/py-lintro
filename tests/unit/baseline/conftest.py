"""Fixtures for fingerprint baseline behavior."""

import pytest

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from tests.baseline_fixtures import workspace as workspace


@pytest.fixture
def finding() -> RuffIssue:
    """Return a diagnostic whose numeric content must remain significant."""
    return RuffIssue(file="src/a.py", line=7, code="F401", message="Unused value 42")


@pytest.fixture
def dirty_result(finding: RuffIssue) -> ToolResult:
    """Return an ordinary inspected lint verdict with one finding."""
    return ToolResult(
        name="ruff",
        success=False,
        issues=[finding],
        issues_count=1,
        inspected_files=True,
        findings_complete=True,
    )


@pytest.fixture(autouse=True)
def isolate_native_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject native processes in baseline units, including version probes."""

    def unexpected_process(*args: object, **kwargs: object) -> None:
        """Fail immediately when a test has not supplied its subprocess double."""
        pytest.fail("Baseline unit tests must double the subprocess boundary")

    monkeypatch.setattr("subprocess.run", unexpected_process)
    monkeypatch.setattr("subprocess.Popen", unexpected_process)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *args, **kwargs: None,
    )
