"""Tests for `scripts/ci/tools-image-detect-changes.sh`."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import yaml
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "tools-image-detect-changes.sh"
TOOLS_IMAGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tools-image.yml"

# Canonical tool-file trigger set. All three locations below must match exactly:
#   1. scripts/ci/tools-image-detect-changes.sh (TOOL_PATTERNS + scripts/ci glob)
#   2. .github/workflows/tools-image.yml push.paths
#   3. .github/workflows/tools-image.yml check-changes paths-filter tools
EXPECTED_TOOL_PATHS: frozenset[str] = frozenset(
    {
        "Dockerfile.tools",
        "scripts/utils/install-tools.sh",
        "scripts/ci/tools-image-*.sh",
        "package.json",
        "lintro/_tool_versions.py",
        "lintro/tools/manifest.json",
        ".github/workflows/tools-image.yml",
    },
)


def _write_fake_git(bin_dir: Path) -> None:
    """Write a fake `git` executable that returns mock changed files.

    Args:
        bin_dir: Directory that will contain the fake `git` binary.
    """
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "diff" && "${2:-}" == "--name-only" ]]; then\n'
        "  printf '%s\\n' \"${MOCK_CHANGED_FILES:-}\"\n"
        "  exit 0\n"
        "fi\n"
        'echo "unexpected git invocation: $*" >&2\n'
        "exit 1\n",
    )
    fake_git.chmod(fake_git.stat().st_mode | stat.S_IXUSR)


def _run_script(
    *,
    changed_files: str,
    event: str = "pull_request",
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the detect-changes script with a fake `git diff` response.

    Args:
        changed_files: Newline-delimited file list returned by fake `git diff`.
        event: GitHub event name passed to the script.

    Returns:
        Tuple of subprocess result and output file contents.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        bin_dir = temp_dir / "bin"
        bin_dir.mkdir()
        _write_fake_git(bin_dir=bin_dir)

        output_file = temp_dir / "github-output.txt"
        env = os.environ.copy()
        env.update(
            {
                "GITHUB_EVENT_NAME": event,
                "GITHUB_OUTPUT": str(output_file),
                "MOCK_CHANGED_FILES": changed_files,
                "PATH": f"{bin_dir}:{env['PATH']}",
            },
        )

        if event in {"pull_request", "merge_group"}:
            env.update(
                {
                    "PR_BASE_SHA": "base-sha",
                    "PR_HEAD_SHA": "head-sha",
                },
            )
        elif event == "push":
            env["GITHUB_EVENT_BEFORE"] = "before-sha"

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )

        output_text = output_file.read_text() if output_file.exists() else ""
        return result, output_text


def test_detect_changes_matches_tools_image_script_glob() -> None:
    """Script changes under `scripts/ci/tools-image-*.sh` trigger fresh image usage."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains("scripts/ci/tools-image-*.sh")


def test_detect_changes_matches_tools_image_on_push() -> None:
    """Push events use the same tools-image glob detection as pull requests."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
        event="push",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains("scripts/ci/tools-image-*.sh")


def test_detect_changes_matches_tools_image_on_merge_group() -> None:
    """Merge queue events request a fresh tools-image build for tool changes."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
        event="merge_group",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains(
        "fresh tools image will be built via workflow_call",
    )


def test_detect_changes_on_push_uses_production_tools_image_notice() -> None:
    """Main pushes leave production image validation to Build - Tools Image."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
        event="push",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains(
        "production tools image will be built by Build - Tools Image",
    )


def _shell_tool_paths() -> frozenset[str]:
    """Return the tool path set declared in the detect-changes script.

    Returns:
        Union of TOOL_PATTERNS entries and the inline scripts/ci glob.

    Raises:
        AssertionError: If TOOL_PATTERNS or the scripts/ci glob are missing.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    array_match = re.search(r"TOOL_PATTERNS=\(\s*(.*?)\s*\)", text, re.DOTALL)
    if array_match is None:
        raise AssertionError("TOOL_PATTERNS array not found in script")
    entries = re.findall(r'"([^"]+)"', array_match.group(1))
    glob_match = re.search(
        r'changed_file"\s*==\s*(scripts/ci/tools-image-\*\.sh)',
        text,
    )
    if glob_match is None:
        # Fallback: look for the literal glob used in matches_tool_pattern.
        glob_match = re.search(r"scripts/ci/tools-image-\*\.sh", text)
    if glob_match is None:
        raise AssertionError("scripts/ci glob not found in script")
    return frozenset(entries) | {"scripts/ci/tools-image-*.sh"}


def _workflow_push_paths() -> frozenset[str]:
    """Return push.paths declared in tools-image.yml."""
    data = yaml.safe_load(TOOLS_IMAGE_WORKFLOW.read_text(encoding="utf-8"))
    return frozenset(data["on"]["push"]["paths"])


def _workflow_paths_filter_tools() -> frozenset[str]:
    """Return paths-filter `tools` entries from the check-changes job.

    Returns:
        Set of paths under the `tools` filter.

    Raises:
        AssertionError: If the filters block cannot be located in the workflow.
    """
    text = TOOLS_IMAGE_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"filters:\s*\|\s*\n((?:[ \t]+.*\n)+)",
        text,
    )
    if match is None:
        raise AssertionError("paths-filter filters block not found")
    filters = yaml.safe_load(match.group(1))
    return frozenset(filters["tools"])


def test_tool_patterns_parity_across_shell_and_workflow() -> None:
    """Tool-change triggers must stay aligned in all three declaration sites."""
    assert_that(_shell_tool_paths()).is_equal_to(EXPECTED_TOOL_PATHS)
    assert_that(_workflow_push_paths()).is_equal_to(EXPECTED_TOOL_PATHS)
    assert_that(_workflow_paths_filter_tools()).is_equal_to(EXPECTED_TOOL_PATHS)


def test_detect_changes_ignores_unrelated_files() -> None:
    """Unrelated file changes keep the stable image path."""
    result, output_text = _run_script(
        changed_files="README.md\ndocs/usage.md",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=false")
    assert_that(result.stdout).contains("No tool file changes detected")
