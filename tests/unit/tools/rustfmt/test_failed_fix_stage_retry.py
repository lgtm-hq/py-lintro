"""A failed ``cargo fmt`` stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results: a dirty ``--check``, then a
``cargo fmt`` that fails. Nothing was measured after the failed write, so the
convergence retry must stop rather than let a clean pass replace it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.rustfmt.definition import RustfmtPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_DIFF = "Diff in src/main.rs:1:\n"


def _run(returncode: int, stdout: str = "", stderr: str = "") -> SubprocessResult:
    """Build one canned cargo invocation.

    Args:
        returncode: Exit status.
        stdout: Standard output.
        stderr: Standard error.

    Returns:
        The subprocess result.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )


def test_failed_cargo_fmt_then_clean_retry_stays_unmeasured(
    rustfmt_plugin: RustfmtPlugin,
    tmp_path: Path,
) -> None:
    """The failed write keeps its finding and unknown counts."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"')
    source = tmp_path / "src" / "main.rs"
    source.parent.mkdir()
    source.write_text("fn main(){}")
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            rustfmt_plugin,
            "_run_subprocess_result",
            side_effect=[
                _run(1, _DIFF),
                _run(1, stderr="error: failed to write src/main.rs\n"),
                _run(0),
                _run(0),
            ],
        ),
    ):
        result = run_fix_with_retry(rustfmt_plugin, [str(source)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(1)
    assert_that(result.output).contains("failed to write")
