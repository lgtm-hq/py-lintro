"""Real Black mixed native verdict regression."""

from pathlib import Path

import pytest

from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.tools.black.definition import BlackPlugin
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.fingerprint_baseline import snapshot
from tests.integration._tools import require_tool

pytestmark = require_tool("black")


def _artifact(result: ToolResult, root: Path) -> RunArtifact:
    """Represent the failing check before acknowledgements are applied."""
    return RunArtifact(
        tool_results=[result],
        workspace_root=root,
        total_issues=result.issues_count,
        exit_code=1,
    )


@pytest.mark.parametrize("syntax_error", [False, True])
def test_real_black_mixed_verdict_keeps_native_error(
    workspace: Path,
    syntax_error: bool,
) -> None:
    """Real Black's exit 123 cannot be hidden by an acknowledged format finding."""
    dirty = workspace / "a.py"
    dirty.write_text("x=1\n", encoding="utf-8")
    plugin = BlackPlugin()
    baseline = snapshot([plugin.check([str(dirty)], {})], root=workspace)
    assert len(baseline.entries) == 1
    files = [str(dirty)]
    if syntax_error:
        broken = workspace / "b.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        files.append(str(broken))
    result = plugin.check(files, {})
    assert result.issues_count == 1
    artifact = _artifact(result, workspace)

    apply_baseline(artifact, baseline)
    result = artifact.tool_results[0]

    assert artifact.exit_code == int(syntax_error)
    assert result.success is not syntax_error
    if syntax_error:
        assert result.output and "cannot parse" in result.output.lower()
        assert result.execution_failed
        assert result.baselined_count == 0
    else:
        assert result.baselined_count == 1
