"""Tests for shell scripts in the scripts/ directory.

This module tests the shell scripts to ensure they follow best practices,
have correct syntax, and provide appropriate help/usage information.
"""

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

from assertpy import assert_that


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


def test_resolve_pipeline_relevance_outputs() -> None:
    """resolve-pipeline-relevance.sh should resolve pipeline per event/JSON."""
    script_path = Path("scripts/ci/resolve-pipeline-relevance.sh").resolve()
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
    ]
    for env, expected in cases:
        result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env},
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stdout).contains(expected)


def test_renovate_regex_manager_current_value() -> None:
    """Ensure Renovate custom managers use currentValue to satisfy schema."""
    config_path = Path("renovate.json")
    content = config_path.read_text()
    assert_that(content).contains("customManagers")
    assert_that(content).contains("currentValue")
