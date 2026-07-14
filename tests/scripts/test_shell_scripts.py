"""Tests for shell scripts in the scripts/ directory.

This module tests the shell scripts to ensure they follow best practices,
have correct syntax, and provide appropriate help/usage information.
"""

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

from assertpy import assert_that


def _run_resolve_pipeline_relevance(
    env: dict[str, str],
    output_file: Path,
) -> subprocess.CompletedProcess[str]:
    """Run resolve-pipeline-relevance.sh with a GITHUB_OUTPUT file.

    Args:
        env: EVENT_NAME/CHANGES_JSON environment for the script.
        output_file: File to expose as GITHUB_OUTPUT.

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    script_path = Path("scripts/ci/resolve-pipeline-relevance.sh").resolve()
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GITHUB_OUTPUT": str(output_file),
            **env,
        },
    )


def test_detect_changes_help() -> None:
    """detect-changes.sh should provide help and exit 0."""
    script_path = Path("scripts/ci/detect-changes.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_resolve_pipeline_relevance_help() -> None:
    """resolve-pipeline-relevance.sh should provide help and exit 0."""
    script_path = Path("scripts/ci/resolve-pipeline-relevance.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_resolve_pipeline_relevance_outputs(tmp_path: Path) -> None:
    """resolve-pipeline-relevance.sh should resolve pipeline per event/JSON.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cases: list[tuple[dict[str, str], str]] = [
        # merge_group and push never path-skip.
        ({"EVENT_NAME": "merge_group"}, "pipeline=true"),
        ({"EVENT_NAME": "push"}, "pipeline=true"),
        # pull_request honors the detect-changes filter value.
        (
            {"EVENT_NAME": "pull_request", "CHANGES_JSON": '{"pipeline":true}'},
            "pipeline=true",
        ),
        (
            {"EVENT_NAME": "pull_request", "CHANGES_JSON": '{"pipeline":false}'},
            "pipeline=false",
        ),
        # Missing or unparsable JSON fails open.
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": ""}, "pipeline=true"),
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": "not json"}, "pipeline=true"),
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": "{}"}, "pipeline=true"),
        # Invariant guard: full-lint relevance forces the pipeline on even
        # if the pipeline filter missed (filter lists drifted).
        (
            {
                "EVENT_NAME": "pull_request",
                "CHANGES_JSON": '{"pipeline":false,"full-lint":true}',
            },
            "pipeline=true",
        ),
    ]
    for index, (env, expected) in enumerate(cases):
        output_file = tmp_path / f"github-output-{index}"
        result = _run_resolve_pipeline_relevance(env, output_file)
        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.read_text().splitlines()).contains(expected)


def test_resolve_pipeline_relevance_lint_scope(tmp_path: Path) -> None:
    """resolve-pipeline-relevance.sh should resolve lint-scope per event/JSON.

    lint-scope narrows to `changed` only when a pull_request diff explicitly
    missed the `full-lint` filter; every other case (non-PR events, filter
    hit, missing filter, unparsable JSON) stays full-repo.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cases: list[tuple[dict[str, str], str]] = [
        # Non-PR events always lint the full repo.
        ({"EVENT_NAME": "merge_group"}, "lint-scope=full"),
        ({"EVENT_NAME": "push"}, "lint-scope=full"),
        ({"EVENT_NAME": "workflow_dispatch"}, "lint-scope=full"),
        # PRs narrow to changed files only on an explicit full-lint=false.
        (
            {
                "EVENT_NAME": "pull_request",
                "CHANGES_JSON": '{"pipeline":true,"full-lint":false}',
            },
            "lint-scope=changed",
        ),
        (
            {
                "EVENT_NAME": "pull_request",
                "CHANGES_JSON": '{"pipeline":true,"full-lint":true}',
            },
            "lint-scope=full",
        ),
        # Missing filter, empty, or unparsable JSON fail safe to full.
        (
            {"EVENT_NAME": "pull_request", "CHANGES_JSON": '{"pipeline":true}'},
            "lint-scope=full",
        ),
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": ""}, "lint-scope=full"),
        (
            {"EVENT_NAME": "pull_request", "CHANGES_JSON": "not json"},
            "lint-scope=full",
        ),
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": "{}"}, "lint-scope=full"),
    ]
    for index, (env, expected) in enumerate(cases):
        output_file = tmp_path / f"github-output-{index}"
        result = _run_resolve_pipeline_relevance(env, output_file)
        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.read_text().splitlines()).contains(expected)


def test_renovate_regex_manager_current_value() -> None:
    """Ensure Renovate custom managers use currentValue to satisfy schema."""
    config_path = Path("renovate.json")
    content = config_path.read_text()
    assert_that(content).contains("customManagers")
    assert_that(content).contains("currentValue")
