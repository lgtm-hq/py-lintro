"""A failed typos fix stage stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results. A detection, write or re-check
pass that fails measured nothing about what remains, so the convergence retry
must stop on it rather than let a clean second pass replace it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.typos.definition import TyposPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

# Assembled at runtime so the repository's own typos run leaves it alone.
_MISSPELLING = "".join(("te", "h"))
_TYPO = json.dumps(
    {
        "type": "typo",
        "path": "fixme.txt",
        "line_num": 1,
        "byte_offset": 0,
        "typo": _MISSPELLING,
        "corrections": ["the"],
    },
)
_FOUND = SubprocessResult(returncode=2, stdout=_TYPO, stderr="", output=_TYPO)
_CLEAN = SubprocessResult(returncode=0, stdout="", stderr="", output="")
_BROKEN = SubprocessResult(
    returncode=1,
    stdout="",
    stderr="error: read-only file system\n",
    output="error: read-only file system\n",
)


@pytest.mark.parametrize(
    ("failed_pass", "initial_count"),
    [
        ([_BROKEN], None),
        ([_FOUND, _BROKEN], 1),
        ([_FOUND, _CLEAN, _BROKEN], 1),
    ],
    ids=["detect-fails", "write-fails", "recheck-fails"],
)
def test_failed_stage_then_clean_retry_stays_unmeasured(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
    failed_pass: list[SubprocessResult],
    initial_count: int | None,
) -> None:
    """The failure survives a clean second pass with unknown fix counts."""
    target = tmp_path / "fixme.txt"
    target.write_text(f"{_MISSPELLING} cat\n")
    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[*failed_pass, _CLEAN, _CLEAN, _CLEAN],
    ):
        result = run_fix_with_retry(typos_plugin, [str(target)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(initial_count)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(initial_count or 0)
    assert_that(result.output).contains("read-only file system")
