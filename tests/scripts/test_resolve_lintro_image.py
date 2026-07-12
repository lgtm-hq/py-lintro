"""Tests for scripts/ci/testing/resolve-lintro-image.sh."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess drives shell scripts under test; shell=False
import tempfile
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts/ci/testing/resolve-lintro-image.sh"


def _write_executable(path: Path, content: str) -> None:
    """Write a mock executable script.

    Args:
        path: Destination path for the mock binary.
        content: Shell script body including shebang.
    """
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_resolve(
    *,
    env: dict[str, str],
    requested_sha: str = "abc123def456789012345678901234567890abcd",
) -> subprocess.CompletedProcess[str]:
    """Run resolve-lintro-image.sh with mocked tooling.

    Args:
        env: Environment variables for the subprocess.
        requested_sha: Commit SHA passed via LINTRO_SHA.

    Returns:
        CompletedProcess from the script invocation.
    """
    return subprocess.run(  # nosec B603 - fixed argv, controlled test env, shell=False
        [str(_SCRIPT.resolve())],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env={
            **os.environ.copy(),
            "LINTRO_SHA": requested_sha,
            **env,
        },
    )


def test_resolve_lintro_image_help() -> None:
    """resolve-lintro-image.sh should expose usage via --help."""
    result = (
        subprocess.run(  # nosec B603 - fixed argv run against repo script; shell=False
            [str(_SCRIPT.resolve()), "--help"],
            capture_output=True,
            text=True,
            check=False,
            cwd=_REPO_ROOT,
        )
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_resolve_lintro_image_uses_primary_when_manifest_exists() -> None:
    """When the requested sha manifest exists, no fallback metadata is emitted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_bin = Path(tmpdir) / "bin"
        mock_bin.mkdir()
        primary = (
            "ghcr.io/lgtm-hq/py-lintro:sha-abc123def456789012345678901234567890abcd"
        )
        _write_executable(
            mock_bin / "docker",
            f"""#!/usr/bin/env bash
if [[ "$1" == "manifest" && "$2" == "inspect" && "$3" == "{primary}" ]]; then
  exit 0
fi
exit 1
""",
        )
        _write_executable(
            mock_bin / "gh",
            """#!/usr/bin/env bash
echo "gh should not be called when primary manifest exists" >&2
exit 99
""",
        )
        output_path = Path(tmpdir) / "github_output"
        output_path.touch()
        result = _run_resolve(
            env={
                "PATH": f"{mock_bin}:{os.environ.get('PATH', '')}",
                "GITHUB_OUTPUT": str(output_path),
            },
        )

        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stdout).contains("Using published image")
        output = output_path.read_text(encoding="utf-8")
        assert_that(output).contains(f"lintro_image={primary}")
        assert_that(output).contains("lintro_fallback=false")


def test_resolve_lintro_image_falls_back_to_newest_sha_tag() -> None:
    """When the requested manifest is missing, resolve to newest published sha-* tag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_bin = Path(tmpdir) / "bin"
        mock_bin.mkdir()
        requested_sha = "abc123def456789012345678901234567890abcd"
        fallback_sha = "fedcba9876543210fedcba9876543210fedcba98"
        primary = f"ghcr.io/lgtm-hq/py-lintro:sha-{requested_sha}"
        fallback = f"ghcr.io/lgtm-hq/py-lintro:sha-{fallback_sha}"
        _write_executable(
            mock_bin / "docker",
            f"""#!/usr/bin/env bash
if [[ "$1" == "manifest" && "$2" == "inspect" ]]; then
  if [[ "$3" == "{fallback}" ]]; then
    exit 0
  fi
  exit 1
fi
exit 1
""",
        )
        _write_executable(
            mock_bin / "gh",
            f"""#!/usr/bin/env bash
if [[ "$1" == "api" ]]; then
  cat <<'JSON'
[
  {{
    "updated_at": "2026-07-01T00:00:00Z",
    "metadata": {{
      "container": {{
        "tags": ["sha-{fallback_sha}"]
      }}
    }}
  }}
]
JSON
  exit 0
fi
if [[ "$1" == "run" && "$2" == "list" ]]; then
  echo "https://github.com/lgtm-hq/py-lintro/actions/runs/1 (conclusion: failure)"
  exit 0
fi
echo "unexpected gh invocation: $*" >&2
exit 99
""",
        )
        output_path = Path(tmpdir) / "github_output"
        summary_path = Path(tmpdir) / "step_summary"
        summary_path.touch()
        result = _run_resolve(
            env={
                "PATH": f"{mock_bin}:{os.environ.get('PATH', '')}",
                "GITHUB_OUTPUT": str(output_path),
                "GITHUB_STEP_SUMMARY": str(summary_path),
            },
            requested_sha=requested_sha,
        )

        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stderr).contains("Falling back to")
        assert_that(result.stderr).contains(primary)
        output = output_path.read_text(encoding="utf-8")
        assert_that(output).contains(f"lintro_image={fallback}")
        assert_that(output).contains(f"lintro_requested_sha={requested_sha}")
        assert_that(output).contains(f"lintro_resolved_sha={fallback_sha}")
        assert_that(output).contains("lintro_fallback=true")
        summary = summary_path.read_text(encoding="utf-8")
        assert_that(summary).contains("Docker image preflight")
        assert_that(summary).contains(fallback)
        assert_that(summary).contains("actions/runs/1")


def test_resolve_lintro_image_fails_when_no_sha_tags() -> None:
    """When no sha-* tags exist in GHCR, the script exits with an error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_bin = Path(tmpdir) / "bin"
        mock_bin.mkdir()
        _write_executable(
            mock_bin / "docker",
            """#!/usr/bin/env bash
exit 1
""",
        )
        _write_executable(
            mock_bin / "gh",
            """#!/usr/bin/env bash
if [[ "$1" == "api" ]]; then
  echo '[]'
  exit 0
fi
exit 99
""",
        )
        result = _run_resolve(
            env={"PATH": f"{mock_bin}:{os.environ.get('PATH', '')}"},
        )

        assert_that(result.returncode).is_equal_to(1)
        assert_that(result.stderr).contains("No published sha-* tags")
