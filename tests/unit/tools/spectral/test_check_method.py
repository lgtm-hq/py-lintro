"""Tests for SpectralPlugin.check."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess symbols are only referenced for patching/exception types; no process is spawned
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.spectral import SpectralPlugin

MOCK_OUTPUT = (
    '[{"code": "operation-operationId", "path": ["paths", "/users", "get"], '
    '"message": "Operation must have \\"operationId\\".", "severity": 1, '
    '"range": {"start": {"line": 6, "character": 8}}, "source": "openapi.yaml"}]'
)


def _mock_ctx(
    tmp_path: Path,
    *,
    files: list[str] | None = None,
    rel_files: list[str] | None = None,
) -> MagicMock:
    """Build a mock execution context for check().

    Args:
        tmp_path: Temporary directory for the fake target file.
        files: Absolute files discovered for this execution.
        rel_files: Paths relative to the execution directory.

    Returns:
        MagicMock: A context object mimicking _prepare_execution output.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.files = files or [str(tmp_path / "openapi.yaml")]
    ctx.rel_files = rel_files or [Path(path).name for path in ctx.files]
    return ctx


def _process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> SubprocessResult:
    """Build a separated-stream subprocess result for Spectral tests.

    Args:
        returncode: Simulated Spectral process exit code.
        stdout: Simulated JSON standard output.
        stderr: Simulated diagnostic standard error.

    Returns:
        SubprocessResult with a backward-compatible combined output.
    """
    output = "\n".join(part for part in (stdout, stderr) if part)
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=output,
    )


def test_check_with_issues(spectral_plugin: SpectralPlugin, tmp_path: Path) -> None:
    """Check returns parsed issues and marks failure.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(returncode=1, stdout=MOCK_OUTPUT),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issues = result.issues or []
    assert_that(issues).is_length(1)
    issue = cast(SpectralIssue, issues[0])
    assert_that(issue).is_instance_of(SpectralIssue)
    assert_that(issue.code).is_equal_to("operation-operationId")
    assert_that(issue.doc_url).contains(
        "github.com/stoplightio/spectral",
        "openapi-rules.md#operation-operationid",
    )


def test_warning_findings_fail_even_when_spectral_exits_zero(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Parsed warnings make the tool fail independently of process exit code.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout=MOCK_OUTPUT),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_check_without_issues(spectral_plugin: SpectralPlugin, tmp_path: Path) -> None:
    """Check returns success and suppresses output when clean.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="[]", stderr="[Warning] runner noise"),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_fails_when_successful_stdout_is_not_json(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Successful but malformed stdout cannot become a clean pass.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="not-json"),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("not-json")


def test_check_discovers_parent_ruleset_and_builds_json_command(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check discovers a parent ruleset and passes the required CLI flags.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (tmp_path / ".git").mkdir()
    ruleset = tmp_path / ".spectral.yaml"
    ruleset.write_text('extends: ["spectral:oas"]\n')
    spec = specs_dir / "openapi.yaml"
    spec.write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="[]"),
        ) as mock_run,
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(
            tmp_path,
            files=[str(spec)],
            rel_files=["specs/openapi.yaml"],
        )
        spectral_plugin.check([str(specs_dir)], {})

    command = mock_run.call_args.kwargs["cmd"]
    assert_that(command).contains(
        "lint",
        "--format",
        "json",
        "--ignore-unknown-format",
        "--ruleset",
    )
    assert_that(command).contains(str(ruleset.absolute()), "specs/openapi.yaml")
    assert_that(mock_run.call_args.kwargs["cwd"]).is_equal_to(str(tmp_path))
    assert_that(mock_run.call_args.kwargs["timeout"]).is_equal_to(30)


def test_find_ruleset_supports_all_declared_filenames(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Discovery recognizes every declared Spectral ruleset filename.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    for index, filename in enumerate(
        (".spectral.yaml", ".spectral.yml", ".spectral.json", ".spectral.js"),
    ):
        case_dir = tmp_path / str(index)
        nested = case_dir / "specs"
        nested.mkdir(parents=True)
        ruleset = case_dir / filename
        ruleset.write_text("rules: {}\n")

        assert_that(
            spectral_plugin._find_ruleset(search_dir=str(nested)),
        ).is_equal_to(str(ruleset))


def test_find_ruleset_returns_none_without_config(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """An empty project tree has no implicit Spectral ruleset.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    assert_that(
        spectral_plugin._find_ruleset(
            search_dir=str(tmp_path),
            stop_dir=str(tmp_path),
        ),
    ).is_none()


def test_check_per_call_ruleset_reaches_command(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """A per-call ruleset override reaches the Spectral command.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    spec = tmp_path / "openapi.yaml"
    spec.write_text("openapi: 3.0.0\n")
    ruleset = tmp_path / "custom.spectral.yaml"
    ruleset.write_text("rules: {}\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="[]"),
        ) as mock_run,
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        spectral_plugin.check([str(spec)], {"ruleset": ruleset.name})

    command = mock_run.call_args.kwargs["cmd"]
    assert_that(command).contains("--ruleset", str(ruleset.absolute()))


def test_check_configured_ruleset_reaches_command(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """A ruleset set on the plugin reaches the Spectral command.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    spec = tmp_path / "openapi.yaml"
    spec.write_text("openapi: 3.0.0\n")
    ruleset = tmp_path / "configured.spectral.yaml"
    ruleset.write_text("rules: {}\n")
    spectral_plugin.set_options(ruleset=ruleset.name)

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="[]"),
        ) as mock_run,
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        spectral_plugin.check([str(spec)], {})

    assert_that(mock_run.call_args.kwargs["cmd"]).contains(
        "--ruleset",
        str(ruleset.absolute()),
    )


def test_check_searches_all_input_paths_for_ruleset(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Ruleset discovery considers later input paths, not only the first.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_spec = first_dir / "first.yaml"
    second_spec = second_dir / "second.yaml"
    first_spec.write_text("openapi: 3.0.0\n")
    second_spec.write_text("openapi: 3.0.0\n")
    ruleset = second_dir / ".spectral.yaml"
    ruleset.write_text('extends: ["spectral:oas"]\n')

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(stdout="[]"),
        ) as mock_run,
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(
            tmp_path,
            files=[str(first_spec), str(second_spec)],
            rel_files=["first/first.yaml", "second/second.yaml"],
        )
        spectral_plugin.check([str(first_spec), str(second_spec)], {})

    assert_that(mock_run.call_args.kwargs["cmd"]).contains(
        "--ruleset",
        str(ruleset.absolute()),
    )


def test_missing_explicit_ruleset_fails_closed(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """An unreadable explicit ruleset is a runtime failure, not a skip.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    missing_ruleset = tmp_path / "missing.spectral.yaml"
    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(
                returncode=2,
                stderr=f"Could not read ruleset at {missing_ruleset}",
            ),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check(
            [str(tmp_path / "openapi.yaml")],
            {"ruleset": str(missing_ruleset)},
        )

    assert_that(result.success).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.output).contains("Could not read ruleset")


def test_check_skips_without_ruleset(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully (success, no run) when no ruleset is found.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(spectral_plugin, "_find_ruleset", return_value=None),
        patch.object(spectral_plugin, "_run_subprocess_result") as mock_run,
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no ruleset")
    assert_that(result.skipped).is_true()
    assert_that(result.skip_reason).contains("no ruleset")
    mock_run.assert_not_called()


def test_check_returns_early_when_skipped(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check returns the early result when preparation signals a skip.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early

    with patch.object(spectral_plugin, "_prepare_execution", return_value=ctx):
        result = spectral_plugin.check([str(tmp_path)], {})

    assert_that(result).is_same_as(early)


def test_check_handles_timeout(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces a timeout as a failed result.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            side_effect=subprocess.TimeoutExpired(cmd=["spectral"], timeout=30),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("timed out")


def test_check_runtime_error_is_not_clean(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable findings fails instead of passing.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")
    runtime_error = "Error: Cannot find module 'tslib'\n"

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess_result",
            return_value=_process(returncode=2, stderr=runtime_error),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("tslib")
