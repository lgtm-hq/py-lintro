"""A failed ``taplo fmt`` stays unmeasured through a clean retry (#3401).

The real plugin runs canned subprocess results: a dirty ``fmt --check``, a
clean ``lint``, then a ``taplo fmt`` that cannot write. The convergence retry
must stop on that failure rather than let a clean pass replace it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.taplo.definition import TaploPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_FORMAT_ISSUE = """error[formatting]: the file is not properly formatted
  --> test.toml:2:1
   |
 2 | name="test"
   | ^ formatting issue
"""


def test_failed_taplo_fmt_then_clean_retry_stays_unmeasured(
    taplo_plugin: TaploPlugin,
    tmp_path: Path,
) -> None:
    """The failed write keeps its finding and unknown counts."""
    target = tmp_path / "test.toml"
    target.write_text('[package]\nname="test"\n')
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            taplo_plugin,
            "_run_subprocess",
            side_effect=[
                (False, _FORMAT_ISSUE),
                (True, ""),
                (False, "error: permission denied (os error 13)"),
                (True, ""),
                (True, ""),
                (True, ""),
            ],
        ),
    ):
        result = run_fix_with_retry(taplo_plugin, [str(target)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(1)
    assert_that(result.output).contains("permission denied")
