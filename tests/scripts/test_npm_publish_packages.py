"""Tests for scripts/ci/npm/publish_packages.sh.

These exercise the retry + idempotency behaviour of the npm publish helper
using stub ``npm`` and ``node`` binaries on PATH. Nothing is published and no
network call is made: the stubs record every invocation and emit canned output
so we can assert on retries, skips, and hard failures.

The live publish path itself only runs on tag-push, so it is never exercised by
PR CI; these script-level tests are the correctness signal for the retry and
existence-check logic.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives the script under test with shell=False
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts/ci/npm/publish_packages.sh"

# The five package subdirectories the script publishes, in order.
_PACKAGES = ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64", "lintro")


def _publish_log(result: subprocess.CompletedProcess[str]) -> str:
    """Return the publish-log section the npm stub recorded.

    Args:
        result: The completed run produced by ``_run``.

    Returns:
        str: The text between the ``---LOG---`` and ``---SLEEP---`` markers.
    """
    return result.stdout.split("---LOG---", 1)[1].split("---SLEEP---", 1)[0]


def _sleep_log(result: subprocess.CompletedProcess[str]) -> str:
    """Return the recorded backoff-sleep durations, one per line.

    Args:
        result: The completed run produced by ``_run``.

    Returns:
        str: The text after the ``---SLEEP---`` marker.
    """
    return result.stdout.split("---SLEEP---", 1)[1]


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable stub binary into a PATH shim directory.

    Args:
        bin_dir: Directory prepended to PATH for the script under test.
        name: Binary name to shadow (e.g. ``npm``).
        body: Bash script body executed when the stub is invoked.
    """
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    stub.chmod(0o755)


def _fake_npm_dir(root: Path) -> Path:
    """Create a fake ``npm`` package tree the script can read manifests from.

    Args:
        root: Directory that will act as the repository root override.

    Returns:
        Path: The created ``npm`` directory containing per-package manifests.
    """
    npm_dir = root / "npm"
    for pkg in _PACKAGES:
        pkg_dir = npm_dir / pkg
        pkg_dir.mkdir(parents=True)
        name = "@lgtm-hq/lintro" if pkg == "lintro" else f"@lgtm-hq/lintro-{pkg}"
        (pkg_dir / "package.json").write_text(
            f'{{"name": "{name}", "version": "9.9.9"}}\n',
        )
    return npm_dir


def _node_stub_body() -> str:
    """Return a bash body that emulates ``node -p "require(...).<field>"``.

    The real script calls ``node -p`` to read the package name and version from
    each manifest; the stub parses the manifest path and requested field out of
    the expression and echoes the matching value.

    Returns:
        str: Bash source for the ``node`` stub.
    """
    return r"""
expr="${2:-}"
path="$(sed -n "s/.*require('\([^']*\)').*/\1/p" <<<"$expr")"
field="$(sed -n "s/.*)\.\([a-z]*\).*/\1/p" <<<"$expr")"
grep -o "\"$field\": *\"[^\"]*\"" "$path" | sed "s/.*: *\"\([^\"]*\)\"/\1/"
"""


def _run(
    tmp_path: Path,
    npm_body: str,
    *,
    extra_env: dict[str, str] | None = None,
    view_body: str | None = None,
    log_name: str = "npm.log",
) -> subprocess.CompletedProcess[str]:
    """Run publish_packages.sh with stub ``npm``/``node`` on PATH.

    Args:
        tmp_path: Pytest temporary directory.
        npm_body: Bash body for ``npm publish`` handling (receives ``$@``).
        extra_env: Extra environment variables for the script.
        view_body: Optional bash body for ``npm view`` handling. When omitted
            ``npm view`` reports E404 (version not yet published).
        log_name: File the npm stub appends its argv to for assertions.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    _fake_npm_dir(root)
    log = tmp_path / log_name

    view = view_body or (
        'echo "npm error code E404" >&2\n'
        'echo "npm error 404 Not Found - GET registry" >&2\n'
        "exit 1"
    )
    npm = (
        f'if [[ "$1" == "view" ]]; then\n{view}\nfi\n'
        f'if [[ "$1" == "publish" ]]; then\n'
        f'echo "publish $(basename "$PWD") $*" >> "{log}"\n{npm_body}\nfi\n'
    )
    _write_stub(bin_dir, "npm", npm)
    _write_stub(bin_dir, "node", _node_stub_body())
    # Record backoff durations without actually sleeping so retry tests stay
    # fast even with a non-zero NPM_PUBLISH_RETRY_DELAY.
    sleep_log = tmp_path / "sleep.log"
    _write_stub(bin_dir, "sleep", f'echo "$1" >> "{sleep_log}"')

    env = {
        **os.environ.copy(),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "LIVE": "1",
        "NPM_PROVENANCE": "1",
        "NPM_PUBLISH_RETRY_DELAY": "0",
        # Point the script's REPO_ROOT-derived npm dir at our fake tree by
        # copying the script into the fake root so its ../../.. resolves there.
        **(extra_env or {}),
    }
    # Copy the script into the fake repo so REPO_ROOT resolves to our tree.
    script_dst = root / "scripts" / "ci" / "npm" / "publish_packages.sh"
    script_dst.parent.mkdir(parents=True)
    script_dst.write_text(_SCRIPT.read_text())
    script_dst.chmod(0o755)

    result = subprocess.run(  # nosec B603 - fixed argv, stubbed PATH, no network
        [str(script_dst)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
    result_log = log.read_text() if log.exists() else ""
    sleep_record = sleep_log.read_text() if sleep_log.exists() else ""
    # Fold stderr (warnings/errors) into stdout, then append the publish and
    # sleep logs so a single ``result.stdout`` carries everything the
    # assertions inspect.
    result.stdout = (
        f"{result.stdout}\n{result.stderr}\n"
        f"---LOG---\n{result_log}\n---SLEEP---\n{sleep_record}"
    )
    return result


def test_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = subprocess.run(  # nosec B603 - fixed argv against the real script
        [str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("NPM_PUBLISH_MAX_ATTEMPTS")


def test_all_packages_publish_when_absent(tmp_path: Path) -> None:
    """When no version exists, all five packages publish once, in order."""
    result = _run(tmp_path, npm_body="exit 0")
    assert_that(result.returncode).is_equal_to(0)
    for pkg in _PACKAGES:
        assert_that(result.stdout).contains(f"Publishing {pkg}")
    # Meta package is published last.
    log = _publish_log(result)
    assert_that(log.strip().splitlines()).is_length(len(_PACKAGES))
    assert_that(log.strip().splitlines()[-1]).contains("lintro")


def test_provenance_flag_is_passed(tmp_path: Path) -> None:
    """Each publish carries --provenance so signing is never dropped."""
    result = _run(tmp_path, npm_body="exit 0")
    log = _publish_log(result)
    for line in log.strip().splitlines():
        assert_that(line).contains("--provenance")


def test_transient_tlog_409_is_retried_then_succeeds(tmp_path: Path) -> None:
    """A TLOG_CREATE_ENTRY_ERROR on the first attempt is retried and succeeds."""
    # Fail the first publish of linux-arm64 with the tlog 409, then succeed.
    npm_body = (
        'if [[ "$(basename "$PWD")" == "linux-arm64" && ! -f "$STATE" ]]; then\n'
        '  touch "$STATE"\n'
        '  echo "npm error code TLOG_CREATE_ENTRY_ERROR" >&2\n'
        '  echo "npm error error creating tlog entry - (409) an equivalent '
        'entry already exists" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    state = tmp_path / "state"
    result = _run(
        tmp_path,
        npm_body=npm_body,
        extra_env={"STATE": str(state), "NPM_PUBLISH_MAX_ATTEMPTS": "3"},
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("transient publish error for linux-arm64")
    assert_that(result.stdout).contains("attempt 1/3")
    assert_that(result.stdout).contains("attempt 2/3")


def test_backoff_delay_doubles_between_retries(tmp_path: Path) -> None:
    """Retry backoff is exponential: the recorded sleeps double each attempt."""
    # linux-arm64 fails transiently on the first two attempts, then succeeds,
    # so publish_one sleeps twice before the third attempt lands.
    npm_body = (
        'if [[ "$(basename "$PWD")" == "linux-arm64" ]]; then\n'
        '  n="$(cat "$STATE" 2>/dev/null || echo 0)"\n'
        '  if [[ "$n" -lt 2 ]]; then\n'
        '    echo "$((n + 1))" > "$STATE"\n'
        '    echo "npm error code TLOG_CREATE_ENTRY_ERROR" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "fi\n"
        "exit 0"
    )
    state = tmp_path / "state"
    result = _run(
        tmp_path,
        npm_body=npm_body,
        # Base delay 5s; the stubbed sleep records without waiting.
        extra_env={
            "STATE": str(state),
            "NPM_PUBLISH_RETRY_DELAY": "5",
            "NPM_PUBLISH_MAX_ATTEMPTS": "3",
        },
    )
    assert_that(result.returncode).is_equal_to(0)
    sleeps = _sleep_log(result).strip().splitlines()
    # 5 then 10 — exponential doubling of the base delay.
    assert_that(sleeps).is_equal_to(["5", "10"])


def test_rate_limit_429_is_retried_then_succeeds(tmp_path: Path) -> None:
    """A registry 429 (rate limit) is transient and retried, not a hard fail."""
    npm_body = (
        'if [[ "$(basename "$PWD")" == "linux-arm64" && ! -f "$STATE" ]]; then\n'
        '  touch "$STATE"\n'
        '  echo "npm error code E429" >&2\n'
        '  echo "npm error 429 Too Many Requests - PUT registry" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    state = tmp_path / "state"
    result = _run(tmp_path, npm_body=npm_body, extra_env={"STATE": str(state)})
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("transient publish error for linux-arm64")


def test_transient_error_exhausts_attempts_and_fails(tmp_path: Path) -> None:
    """A persistently transient error fails after the bounded attempt budget."""
    npm_body = (
        'if [[ "$(basename "$PWD")" == "linux-arm64" ]]; then\n'
        '  echo "npm error code TLOG_CREATE_ENTRY_ERROR" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    result = _run(
        tmp_path,
        npm_body=npm_body,
        extra_env={"NPM_PUBLISH_MAX_ATTEMPTS": "3"},
    )
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout).contains("failed after 3 attempts")
    # linux-x64 and the meta package must NOT publish after the hard failure.
    log = _publish_log(result)
    assert_that(log).does_not_contain("linux-x64")
    assert_that(log).does_not_contain("lintro")


def test_auth_failure_is_not_retried(tmp_path: Path) -> None:
    """A non-transient auth failure fails immediately without retrying."""
    npm_body = (
        'if [[ "$(basename "$PWD")" == "darwin-arm64" ]]; then\n'
        '  echo "npm error code E401" >&2\n'
        '  echo "npm error 401 Unauthorized - PUT registry" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    result = _run(tmp_path, npm_body=npm_body)
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout).contains("non-retryable auth/validation error")
    # Only one publish attempt for darwin-arm64 (no retry).
    log = _publish_log(result)
    darwin_attempts = [
        line for line in log.strip().splitlines() if "darwin-arm64" in line
    ]
    assert_that(darwin_attempts).is_length(1)


def test_auth_error_mentioning_sigstore_is_not_retried(tmp_path: Path) -> None:
    """An auth failure is a hard fail even if it names a Sigstore component.

    The transient regex matches ``sigstore``; the non-retryable auth check must
    win so ``sigstore authentication failed (E401)`` is never retried.
    """
    npm_body = (
        'if [[ "$(basename "$PWD")" == "darwin-arm64" ]]; then\n'
        '  echo "npm error sigstore authentication failed (E401)" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    result = _run(tmp_path, npm_body=npm_body)
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout).contains("non-retryable auth/validation error")
    log = _publish_log(result)
    darwin_attempts = [
        line for line in log.strip().splitlines() if "darwin-arm64" in line
    ]
    assert_that(darwin_attempts).is_length(1)


def test_invalid_max_attempts_is_rejected(tmp_path: Path) -> None:
    """A non-integer NPM_PUBLISH_MAX_ATTEMPTS aborts before any publish."""
    result = _run(
        tmp_path,
        npm_body="exit 0",
        extra_env={"NPM_PUBLISH_MAX_ATTEMPTS": "0"},
    )
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout).contains("NPM_PUBLISH_MAX_ATTEMPTS must be")
    assert_that(_publish_log(result).strip()).is_equal_to("")


def test_already_published_versions_are_skipped(tmp_path: Path) -> None:
    """Versions already on the registry are skipped loudly, not re-published."""
    # npm view reports the two darwin packages as present, others E404.
    view_body = (
        'if grep -qE "darwin-(arm64|x64)" <<<"$*"; then\n'
        '  echo "9.9.9"\n'
        "  exit 0\n"
        "fi\n"
        'echo "npm error code E404" >&2\n'
        'echo "npm error 404 Not Found" >&2\n'
        "exit 1"
    )
    result = _run(tmp_path, npm_body="exit 0", view_body=view_body)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Skipping @lgtm-hq/lintro-darwin-arm64")
    assert_that(result.stdout).contains("Skipping @lgtm-hq/lintro-darwin-x64")
    # Only the three remaining packages actually publish.
    log = _publish_log(result)
    published = log.strip().splitlines()
    assert_that(published).is_length(3)
    assert_that(log).does_not_contain("darwin")


def test_publish_conflict_is_treated_as_idempotent_success(tmp_path: Path) -> None:
    """A publish conflict (version already present) counts as success."""
    # view reports absent, but publish returns EPUBLISHCONFLICT — the version
    # landed via a prior attempt/run. The loop must continue, not abort.
    npm_body = (
        'if [[ "$(basename "$PWD")" == "linux-arm64" ]]; then\n'
        '  echo "npm error code EPUBLISHCONFLICT" >&2\n'
        '  echo "npm error You cannot publish over the previously published '
        'versions: 9.9.9." >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0"
    )
    result = _run(tmp_path, npm_body=npm_body)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("idempotent success")
    # All five packages are attempted; the meta package still publishes.
    assert_that(result.stdout).contains("Publishing lintro")


def test_ambiguous_view_failure_falls_through_to_publish(tmp_path: Path) -> None:
    """A non-404 view failure does not abort; it proceeds to a conflict-safe publish."""
    # npm view fails with a 5xx for linux-arm64 (cannot prove absence).
    view_body = (
        'if grep -q "linux-arm64" <<<"$*"; then\n'
        '  echo "npm error code E500" >&2\n'
        '  echo "npm error 500 Internal Server Error" >&2\n'
        "  exit 1\n"
        "fi\n"
        'echo "npm error code E404" >&2\n'
        "exit 1"
    )
    result = _run(tmp_path, npm_body="exit 0", view_body=view_body)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("could not verify @lgtm-hq/lintro-linux-arm64")
    assert_that(result.stdout).contains("proceeding to publish")
    # linux-arm64 is still published despite the ambiguous check.
    log = _publish_log(result)
    assert_that(log).contains("linux-arm64")


def test_dry_run_publishes_without_existence_check(tmp_path: Path) -> None:
    """In dry-run mode every package runs and the registry is never queried."""
    # If npm view were called it would fail loudly; assert it is not.
    view_body = 'echo "VIEW SHOULD NOT RUN" >&2\nexit 1'
    result = _run(
        tmp_path,
        npm_body="exit 0",
        view_body=view_body,
        extra_env={"LIVE": "0", "NPM_PROVENANCE": "0"},
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).does_not_contain("VIEW SHOULD NOT RUN")
    log = _publish_log(result)
    assert_that(log.strip().splitlines()).is_length(len(_PACKAGES))
