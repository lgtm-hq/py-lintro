"""Fixtures for fingerprint baseline behavior."""

from pathlib import Path

import pytest

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue


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


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make the test workspace the invocation root and disable automatic installs."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  auto_install_deps: false\n  parallel: false\n",
        encoding="utf-8",
    )
    from lintro.config.config_loader import clear_config_cache

    clear_config_cache()
    return tmp_path
