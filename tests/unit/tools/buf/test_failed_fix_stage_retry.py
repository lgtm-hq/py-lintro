"""A failed buf fix stage stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results. A probe or write that fails
measured nothing, so the convergence retry must stop on it instead of letting
the clean second pass report the run as fully measured.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.buf.definition import BufPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_FORMAT_DIFF = "--- a.proto.orig\t2026-07-07\n+++ a.proto\t2026-07-07\n@@ -1 +1 @@\n"
_CLEAN = SubprocessResult(returncode=0, stdout="", stderr="", output="")
_DIRTY_FORMAT = SubprocessResult(
    returncode=100,
    stdout=_FORMAT_DIFF,
    stderr="",
    output=_FORMAT_DIFF,
)
_BROKEN = SubprocessResult(
    returncode=1,
    stdout="",
    stderr="Failure: permission denied\n",
    output="Failure: permission denied\n",
)


@pytest.fixture(autouse=True)
def _stub_version_check() -> Iterator[None]:
    """Keep the version precheck out of the canned subprocess sequence.

    Yields:
        None: While ``verify_tool_version`` is patched out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield


@pytest.mark.parametrize(
    ("failed_pass", "kept_findings"),
    [
        ([_DIRTY_FORMAT, _CLEAN, _BROKEN], 1),
        ([_BROKEN], 0),
        ([_DIRTY_FORMAT, _BROKEN], 1),
    ],
    ids=["write-fails", "format-probe-fails", "lint-probe-fails"],
)
def test_failed_stage_then_clean_retry_stays_unmeasured(
    buf_plugin: BufPlugin,
    tmp_path: Path,
    failed_pass: list[SubprocessResult],
    kept_findings: int,
) -> None:
    """The failure survives a clean second pass, with its findings kept."""
    proto = tmp_path / "a.proto"
    proto.write_text('syntax="proto3";\n')
    clean_pass = [_CLEAN] * 5
    with patch.object(
        buf_plugin,
        "_run_subprocess_result",
        side_effect=[*failed_pass, *clean_pass],
    ):
        result = run_fix_with_retry(buf_plugin, [str(proto)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(kept_findings)
    assert_that(result.output).contains("permission denied")
