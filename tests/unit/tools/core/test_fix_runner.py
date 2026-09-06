"""Tests for the shared per-file fix runner (issue #2311).

The runner's happy paths are exercised by the per-tool suites; these tests
cover the execution-failure and verification branches that a tool's own tests
do not reach, using the three definitions that drive the three verify modes.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is constructed here
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.core.fix_runner import PerFileFixPolicy, VerifyMode
from lintro.tools.definitions.dotenv_linter import DotenvLinterPlugin
from lintro.tools.definitions.shfmt import ShfmtPlugin
from lintro.tools.definitions.sqlfluff import SqlfluffPlugin

#: A minimal shfmt diff, enough for the parser to report one issue.
SHFMT_DIFF: str = """--- script.sh.orig
+++ script.sh
@@ -1,2 +1,2 @@
 #!/bin/bash
-echo  "hi"
+echo "hi"
"""


def _sqlfluff_violations(count: int) -> str:
    """Render ``count`` sqlfluff violations in the JSON format the plugin asks for.

    Args:
        count: How many violations the lint run should report.

    Returns:
        A sqlfluff JSON lint report.
    """
    violations = ",\n".join(f"""            {{
                "start_line_no": {line},
                "start_line_pos": 1,
                "end_line_no": {line},
                "end_line_pos": 6,
                "code": "LT01",
                "description": "Keywords must be upper case.",
                "name": "capitalisation.keywords"
            }}""" for line in range(1, count + 1))
    return f"""[
    {{
        "filepath": "query.sql",
        "violations": [
{violations}
        ]
    }}
]"""


#: One dotenv-linter finding in its default text format.
DOTENV_FINDING: str = ".env:1 LowercaseKey: The foo key should be in uppercase\n"


@pytest.fixture(autouse=True)
def _stub_version_check() -> Iterator[None]:
    """Stub the version precheck for the whole test, not just construction.

    Yields:
        None: While ``verify_tool_version`` is patched out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield


@pytest.fixture
def shell_script(tmp_path: Path) -> Path:
    """Write a shell script for the shfmt-backed cases.

    Args:
        tmp_path: Temporary directory for the file.

    Returns:
        Path to the created script.
    """
    script = tmp_path / "script.sh"
    script.write_text('#!/bin/bash\necho  "hi"\n')
    return script


def test_check_step_execution_error_is_reported_as_a_failure(
    shell_script: Path,
) -> None:
    """An OS error while detecting issues fails the file without fix metrics.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShfmtPlugin()
    with patch.object(plugin, "_run_subprocess", side_effect=OSError("no shfmt")):
        result = plugin.fix([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no shfmt")


def test_fix_command_timeout_reports_every_issue_as_remaining(
    shell_script: Path,
) -> None:
    """A timeout during the fix leaves the detected issues outstanding.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[
            (False, SHFMT_DIFF),
            subprocess.TimeoutExpired(cmd=["shfmt"], timeout=1),
        ],
    ):
        result = plugin.fix([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_command_execution_error_reports_every_issue_as_remaining(
    shell_script: Path,
) -> None:
    """An OS error during the fix leaves the detected issues outstanding.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[(False, SHFMT_DIFF), OSError("write failed")],
    ):
        result = plugin.fix([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).contains("write failed")


def test_failed_fix_without_verification_keeps_the_initial_issues(
    shell_script: Path,
) -> None:
    """VerifyMode.NEVER trusts the fix exit status and reports no progress.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[(False, SHFMT_DIFF), (False, "shfmt: cannot write")],
    ):
        result = plugin.fix([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).contains("cannot write")


def test_verification_failure_conservatively_keeps_the_initial_issues(
    tmp_path: Path,
) -> None:
    """A broken verification run must not be read as "everything was fixed".

    Args:
        tmp_path: Temporary directory for the SQL file.
    """
    query = tmp_path / "query.sql"
    query.write_text("select 1\n")
    plugin = SqlfluffPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[
            (False, _sqlfluff_violations(1)),
            (True, "Fixed 1 file(s)"),
            (False, "not valid json"),
        ],
    ):
        result = plugin.fix([str(query)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_verification_after_a_successful_fix_scores_the_survivors(
    tmp_path: Path,
) -> None:
    """VerifyMode.AFTER_SUCCESS re-reads the file to count what survived.

    Args:
        tmp_path: Temporary directory for the dotenv file.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[
            (False, DOTENV_FINDING),
            (True, ""),
            (True, ""),
        ],
    ):
        result = plugin.fix([str(env_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_partial_fix_scores_only_the_issues_that_disappeared(tmp_path: Path) -> None:
    """VerifyMode.ALWAYS counts survivors from a fresh lint, not the exit status.

    Args:
        tmp_path: Temporary directory for the SQL file.
    """
    query = tmp_path / "query.sql"
    query.write_text("select 1\n")
    plugin = SqlfluffPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[
            (False, _sqlfluff_violations(3)),
            (False, "1 unfixable violation"),
            (False, _sqlfluff_violations(1)),
        ],
    ):
        result = plugin.fix([str(query)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.issues).is_length(1)


def test_a_verifying_policy_requires_a_verification_failure_message() -> None:
    """A silent verify failure must not be able to read as a clean fix."""
    with pytest.raises(ValueError, match="verify_failure_message is required"):
        PerFileFixPolicy(
            check_failure_message="mytool check failed",
            verify=VerifyMode.ALWAYS,
        )
