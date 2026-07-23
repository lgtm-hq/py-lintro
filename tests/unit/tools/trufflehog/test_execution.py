"""Unit tests for the trufflehog plugin check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.trufflehog.trufflehog_issue import TrufflehogIssue
from lintro.plugins.base import ExecutionContext
from lintro.tools.definitions.trufflehog import TrufflehogPlugin
from tests.unit.tools.trufflehog.conftest import (
    make_subprocess_result,
    sample_finding_line,
)


def test_check_clean_scan_passes(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """An empty stdout with exit 0 is a clean scan.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""No secrets here."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout="", returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_detects_secret(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A finding on stdout should be parsed into an issue.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "config.py"
    test_file.write_text("TOKEN = 'ghp_fake'\n")

    output = sample_finding_line(file=str(test_file), line=1)
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout=output, returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_length(1)
    issues = result.issues
    assert issues is not None  # nosec B101 - narrows Optional for type checkers
    issue = issues[0]
    assert isinstance(issue, TrufflehogIssue)
    assert_that(issue.detector_name).is_equal_to("Github")


def test_check_passes_absolute_paths_to_command(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """The scan command must receive an absolute path, not a relative one.

    Relative paths make TruffleHog exit 0 with no output when its working
    directory differs from the caller's, which would silently hide secrets.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "config.py"
    test_file.write_text("TOKEN = 'ghp_fake'\n")

    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        captured["cmd"] = cmd
        return make_subprocess_result(stdout="", returncode=0)

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=fake_run,
    ):
        trufflehog_plugin.check([str(test_file)], {})

    # The resolved absolute path should appear in the command.
    assert_that(captured["cmd"]).contains(str(test_file.resolve()))


def test_check_fails_when_resolved_scan_target_disappears(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A resolved scan-set file disappearing before invoke must fail closed.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')
    prepared = ExecutionContext(
        files=[str(test_file)],
        cwd=str(tmp_path),
        timeout=60,
    )
    test_file.unlink()

    with (
        patch.object(trufflehog_plugin, "_prepare_execution", return_value=prepared),
        patch.object(trufflehog_plugin, "_run_subprocess_result") as run_mock,
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("disappeared before execution")
    assert_that(result.output).contains(str(test_file.resolve()))
    assert_that(result.parse_failures_count).is_equal_to(1)
    run_mock.assert_not_called()


def test_check_fails_when_config_is_absent(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A missing explicit config must not fall back to default detectors.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')
    missing_config = tmp_path / "missing-trufflehog.yaml"
    trufflehog_plugin.set_options(config=str(missing_config))

    with patch.object(trufflehog_plugin, "_run_subprocess_result") as run_mock:
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("config file does not exist")
    assert_that(result.output).contains(str(missing_config))
    run_mock.assert_not_called()


def test_check_unparseable_output_is_not_clean(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Non-empty stdout that yields no findings must not report a clean pass.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout="}{ garbage", returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_benign_missing_path_scan_error_passes(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Lstat missing-path errors outside the scan set must not hard-fail.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /nope/coverage: no such file or directory",'
        '"lstat /nope/lighthouse-reports: no such file or directory"]}'
    )
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout="", stderr=stderr, returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    # Benign scan noise must not surface as a version-incompat parse warning.
    assert_that(result.parse_failures_count in (0, None)).is_true()


def test_check_permission_denied_scan_error_fails(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A permission-denied scan error is a genuine incomplete scan.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["open /secret: permission denied"]}'
    )
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout="", stderr=stderr, returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_missing_scan_set_path_fails(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A missing path that was part of the resolved scan set must fail closed.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')
    resolved = str(test_file.resolve())

    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat ' + resolved + ': no such file or directory"]}'
    )
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(stdout="", stderr=stderr, returncode=0),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_benign_scan_error_with_findings_still_reports_secrets(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Benign missing-path noise must not hide or fail real secret findings.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "config.py"
    test_file.write_text("TOKEN = 'ghp_fake'\n")

    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["lstat /ci-only/coverage: no such file or directory"]}'
    )
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout=sample_finding_line(file=str(test_file), line=1),
            stderr=stderr,
            returncode=0,
        ),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count in (0, None)).is_true()


def test_check_unparseable_scan_error_details_fail_closed(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Scan-error banner without extractable reasons must fail closed.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout="",
            stderr="level=error msg=encountered errors during scan",
            returncode=0,
        ),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_nonzero_exit_without_output_fails(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no stdout is an execution failure.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text('"""Module."""\n')

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout="",
            stderr="fatal: boom",
            returncode=1,
        ),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_nonzero_exit_with_partial_findings_fails(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """A crash after emitting findings is a failure with findings preserved.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text("AWS_KEY = 'AKIA...'\n")

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout=sample_finding_line(file=str(test_file)),
            stderr="panic: scanner aborted",
            returncode=2,
        ),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_check_genuine_scan_error_with_findings_still_fails(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
) -> None:
    """Genuine scan errors fail the run even when another target had findings.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    test_file = tmp_path / "module.py"
    test_file.write_text("AWS_KEY = 'AKIA...'\n")

    stderr = (
        '{"level":"error","msg":"encountered errors during scan",'
        '"errors":["open /etc/shadow: permission denied"]}'
    )
    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        return_value=make_subprocess_result(
            stdout=sample_finding_line(file=str(test_file)),
            stderr=stderr,
            returncode=0,
        ),
    ):
        result = trufflehog_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
