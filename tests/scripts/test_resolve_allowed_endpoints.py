"""Tests for ``scripts/ci/resolve-allowed-endpoints.sh``.

The script is the single source of truth feeding the harden-runner allowlist of
the docker image jobs, and those jobs run only on releases — so every guarantee
it makes has to be pinned here rather than discovered during a publish (#1821).
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess drives the shell script under test
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "resolve-allowed-endpoints.sh"
_ALLOWLIST = _REPO_ROOT / ".github" / "allowed-endpoints" / "docker-build-publish.txt"


def _run(
    *,
    endpoints_file: str | None,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the resolver script with a controlled environment.

    Args:
        endpoints_file: Value for ``ENDPOINTS_FILE``; omitted when None.
        github_output: Path exported as ``GITHUB_OUTPUT``; omitted when None.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if endpoints_file is not None:
        env["ENDPOINTS_FILE"] = endpoints_file
    if github_output is not None:
        env["GITHUB_OUTPUT"] = str(github_output)
    return subprocess.run(  # nosec B603 - fixed argv pointing at a repo script
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env=env,
    )


def test_flattens_the_repository_allowlist_into_one_line() -> None:
    """The checked-in allowlist resolves to a single whitespace-joined list."""
    result = _run(endpoints_file=str(_ALLOWLIST))

    assert_that(result.returncode).is_equal_to(0)
    endpoints = result.stdout.strip()
    assert_that(endpoints).does_not_contain("\n")
    assert_that(endpoints).does_not_contain("#")
    assert_that(endpoints.split()).contains("ghcr.io:443", "fulcio.sigstore.dev:443")


def test_writes_the_endpoints_output_for_the_workflow(tmp_path: Path) -> None:
    """The flattened list is exported as the ``endpoints`` step output."""
    output_file = tmp_path / "github_output"
    output_file.touch()

    result = _run(endpoints_file=str(_ALLOWLIST), github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    written = output_file.read_text(encoding="utf-8").strip()
    assert_that(written).starts_with("endpoints=")
    assert_that(written.removeprefix("endpoints=")).is_equal_to(result.stdout.strip())


def test_strips_comments_and_blank_lines(tmp_path: Path) -> None:
    """Comments, inline comments and blank lines never reach harden-runner."""
    allowlist = tmp_path / "endpoints.txt"
    allowlist.write_text(
        "# leading comment\n\n  github.com:443  \nghcr.io:443 # inline\n\n",
        encoding="utf-8",
    )

    result = _run(endpoints_file=str(allowlist))

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("github.com:443 ghcr.io:443")


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty-file"),
        pytest.param("# only a comment\n\n", id="comments-only"),
    ],
)
def test_fails_instead_of_emitting_an_empty_allowlist(
    tmp_path: Path,
    content: str,
) -> None:
    """An empty list under replace semantics would block all egress.

    Failing the resolver job is the safe outcome: the image jobs gate on its
    success, so nothing builds with a blanked allowlist.
    """
    allowlist = tmp_path / "endpoints.txt"
    allowlist.write_text(content, encoding="utf-8")

    result = _run(endpoints_file=str(allowlist))

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("no endpoints found")


def test_fails_when_the_allowlist_file_is_missing(tmp_path: Path) -> None:
    """A renamed or deleted allowlist must fail loudly, not silently empty."""
    result = _run(endpoints_file=str(tmp_path / "absent.txt"))

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("allowlist file not found")


def test_fails_when_the_endpoints_file_variable_is_unset() -> None:
    """Forgetting ``ENDPOINTS_FILE`` in the workflow must not pass silently."""
    result = _run(endpoints_file=None)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("ENDPOINTS_FILE is required")


def test_help_flag_documents_usage() -> None:
    """``--help`` documents the contract for local reruns."""
    result = subprocess.run(  # nosec B603 - fixed argv pointing at a repo script
        [str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("ENDPOINTS_FILE")
    assert_that(result.stdout).contains("GITHUB_OUTPUT")
