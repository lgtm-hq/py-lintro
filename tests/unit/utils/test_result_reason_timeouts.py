"""A timeout is ``timed out`` unless the producer names a second failure (#2400).

The per-file check runner sets ``execution_failed`` next to ``timed_out``
when a single file times out, and the verify fold does the same when a fix
timed out and its verification failed. Neither is a second failure, so the
reason stays ``main``'s ``timed out`` and never quotes a finding line. Only a
producer that knows better (trufflehog's batching) names both.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is used; nothing is executed
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.core.verify_pass import _fold_check_verdict
from lintro.tools.shellcheck.definition import ShellcheckPlugin
from lintro.utils.result_reason import incomplete_reason
from tests.unit.tools.subprocess_helpers import subprocess_result

_FINDING_JSON = """{"comments": [{
    "file": "a.sh", "line": 3, "endLine": 3, "column": 1, "endColumn": 4,
    "level": "warning", "code": 2034, "message": "foo appears unused."
}]}"""


@pytest.fixture
def shellcheck_plugin() -> Iterator[ShellcheckPlugin]:
    """Provide a ShellcheckPlugin with the version probe stubbed out.

    Yields:
        ShellcheckPlugin: The plugin under test.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield ShellcheckPlugin()


def test_per_file_timeout_beside_findings_reads_timed_out(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
) -> None:
    """One timed-out file next to another file's findings is only ``timed out``.

    Args:
        shellcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    flagged = tmp_path / "a.sh"
    flagged.write_text("#!/bin/sh\nfoo=1\n", encoding="utf-8")
    slow = tmp_path / "z.sh"
    slow.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        if any(arg.endswith("z.sh") for arg in cmd):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
        return subprocess_result(False, _FINDING_JSON)

    with patch.object(shellcheck_plugin, "_run_subprocess_result", fake_run):
        result = shellcheck_plugin.check([str(flagged), str(slow)], {})

    # The runner's own flags: both set, and a finding reported for a.sh.
    assert_that(result.timed_out).is_true()
    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(incomplete_reason(result)).is_equal_to("timed out")


def test_fix_timeout_then_failed_verify_reads_timed_out() -> None:
    """A fix timeout folded with a failed verification is only ``timed out``."""
    mutation = ToolResult(
        name="ruff",
        success=False,
        timed_out=True,
        output="ruff timed out after 1s",
    )
    verify = ToolResult(
        name="ruff",
        success=False,
        execution_failed=True,
        output="ruff: crashed",
    )

    folded = _fold_check_verdict(mutation, verify)

    assert_that(folded.timed_out).is_true()
    assert_that(folded.execution_failed).is_true()
    assert_that(incomplete_reason(folded)).is_equal_to("timed out")


@pytest.mark.parametrize(
    ("timed_out", "execution_failed", "expected"),
    [
        pytest.param(False, False, "failed with no findings (first line)", id="none"),
        pytest.param(True, False, "timed out", id="timed-out"),
        pytest.param(False, True, "first line", id="execution-failed"),
        pytest.param(True, True, "timed out", id="both"),
    ],
)
def test_flag_combinations_match_main(
    *,
    timed_out: bool,
    execution_failed: bool,
    expected: str,
) -> None:
    """Every timeout/failure flag combination gives ``main``'s reason.

    Args:
        timed_out: The result's timeout flag.
        execution_failed: The result's execution-failure flag.
        expected: The reason ``main`` gives for that combination.
    """
    result = ToolResult(
        name="tool",
        success=False,
        timed_out=timed_out,
        execution_failed=execution_failed,
        output="first line\nsecond line",
    )

    assert_that(incomplete_reason(result)).is_equal_to(expected)


def test_named_second_failure_gives_both_reasons() -> None:
    """Only a producer-named second failure adds ``and failed``."""
    result = ToolResult(
        name="trufflehog",
        success=False,
        timed_out=True,
        execution_failed=True,
        output="a.py:1: finding\nfatal: boom",
        failure_besides_timeout="1 batch(es) timed out and 1 batch(es) failed",
    )

    assert_that(incomplete_reason(result)).is_equal_to(
        "timed out and failed (1 batch(es) timed out and 1 batch(es) failed)",
    )
