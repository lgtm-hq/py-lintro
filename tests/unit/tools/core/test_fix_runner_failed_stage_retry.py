"""The per-file fix runner keeps stage errors through a clean retry (#3401).

A file whose initial check or fix command recorded an execution error
measured nothing for that file. The runner must report the run as an
unmeasured execution failure, so the convergence retry stops on it instead of
letting a clean second pass replace it. The real shfmt plugin drives the
runner with canned subprocess results.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.tools.shfmt.definition import ShfmtPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from tests.unit.tools.subprocess_helpers import subprocess_result

_SHFMT_DIFF = """--- script.sh.orig
+++ script.sh
@@ -1,2 +1,2 @@
 #!/bin/bash
-echo  "hi"
+echo "hi"
"""
_CLEAN = subprocess_result(True, "")


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
    ("failed_pass", "initial_count", "kept_findings", "message"),
    [
        (
            [SubprocessResult(2, "", "shfmt: bad flag", "shfmt: bad flag")],
            None,
            0,
            "check failed before fix",
        ),
        ([OSError("no shfmt")], None, 0, "no shfmt"),
        (
            [subprocess_result(False, _SHFMT_DIFF), OSError("write failed")],
            1,
            1,
            "write failed",
        ),
    ],
    ids=["initial-check-exits-without-verdict", "initial-check-raises", "fix-raises"],
)
def test_errored_stage_then_clean_retry_stays_unmeasured(
    tmp_path: Path,
    failed_pass: list[SubprocessResult | Exception],
    initial_count: int | None,
    kept_findings: int,
    message: str,
) -> None:
    """The errored file keeps its findings; the fix counts stay unknown."""
    script = tmp_path / "script.sh"
    script.write_text('#!/bin/bash\necho  "hi"\n')
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[*failed_pass, _CLEAN, _CLEAN],
    ):
        result = run_fix_with_retry(plugin, [str(script)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(initial_count)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(kept_findings)
    assert_that(result.output).contains(message)


def test_one_errored_file_leaves_the_whole_run_unmeasured(tmp_path: Path) -> None:
    """A clean file beside an errored one cannot make the run measured."""
    clean = tmp_path / "a.sh"
    broken = tmp_path / "b.sh"
    for script in (clean, broken):
        script.write_text("#!/bin/bash\necho hi\n")
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[_CLEAN, OSError("b.sh: permission denied")],
    ):
        result = plugin.fix([str(clean), str(broken)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.outcome.accounting.initial).is_none()
    assert_that(result.output).contains("permission denied")
