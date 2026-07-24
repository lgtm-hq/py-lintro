"""Tests for scripts/ci/cosign-sign-images.sh retry + error classification.

These exercise the signing wrapper against a stubbed ``cosign`` on PATH so no
real Sigstore signing happens. They pin the two behaviours #1646 depends on:

* the transient ambient-OIDC token-fetch flake is retried with bounded backoff;
* a genuine signing rejection (or any non-token-fetch error) stays fatal and is
  never retried away.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives the script under test; shell=False
import textwrap
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = (_REPO_ROOT / "scripts/ci/cosign-sign-images.sh").resolve()
_DIGEST = "ghcr.io/lgtm-hq/py-lintro@sha256:" + "a" * 64
_DIGEST_TWO = "ghcr.io/lgtm-hq/py-lintro-base@sha256:" + "b" * 64

_OIDC_MESSAGE = (
    "Error: signing [%s]: signing digest: getting keypair and token: "
    "retrieving ID token: reading ID token: fetching ambient OIDC "
    "credentials: invalid character 'u' looking for beginning of value"
)
_REJECT_MESSAGE = "Error: signing [%s]: signing digest: signature invalid"


def _write_fake_cosign(bin_dir: Path, mode: str, *, fail_until: int = 0) -> Path:
    """Write a stub ``cosign`` that records call count and simulates a mode.

    Args:
        bin_dir: Directory placed at the front of PATH.
        mode: One of ``success``, ``reject``, ``oidc_always``,
            ``oidc_then_success``.
        fail_until: For ``oidc_then_success``, the number of leading calls that
            fail with the OIDC flake before one succeeds.

    Returns:
        Path: Path to the counter file the stub increments on each call.
    """
    counter = bin_dir / "cosign.calls"
    args_log = bin_dir / "cosign.args"
    oidc = _OIDC_MESSAGE % "REF"
    reject = _REJECT_MESSAGE % "REF"
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        counter="{counter}"
        args_log="{args_log}"
        n=0
        if [[ -f "$counter" ]]; then n=$(cat "$counter"); fi
        n=$((n + 1))
        echo "$n" >"$counter"
        # Record every ref cosign was asked to sign so tests can assert
        # coverage, not just invocation count.
        echo "$*" >>"$args_log"
        case "{mode}" in
          success) exit 0 ;;
          reject) echo "{reject}" >&2; exit 1 ;;
          oidc_always) echo "{oidc}" >&2; exit 1 ;;
          oidc_then_success)
            if (( n <= {fail_until} )); then
              echo "{oidc}" >&2
              exit 1
            fi
            exit 0
            ;;
        esac
        """,
    )
    cosign = bin_dir / "cosign"
    cosign.write_text(script)
    cosign.chmod(0o755)
    return counter


def _run(
    bin_dir: Path,
    *,
    images: str,
    max_attempts: str = "4",
    max_delay: str = "30",
) -> subprocess.CompletedProcess[str]:
    """Run the signing script with the stub PATH and no backoff wait.

    Args:
        bin_dir: Directory holding the stub ``cosign`` to prepend to PATH.
        images: Value for the IMAGES environment variable.
        max_attempts: Value for COSIGN_SIGN_MAX_ATTEMPTS.
        max_delay: Value for COSIGN_SIGN_MAX_DELAY.

    Returns:
        subprocess.CompletedProcess: The completed run.
    """
    env = {
        **os.environ.copy(),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "IMAGES": images,
        "COSIGN_SIGN_MAX_ATTEMPTS": max_attempts,
        "COSIGN_SIGN_BASE_DELAY": "0",
        "COSIGN_SIGN_MAX_DELAY": max_delay,
    }
    return subprocess.run(  # nosec B603 - fixed argv, controlled env, shell=False
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _calls(counter: Path) -> int:
    """Return how many times the stub cosign was invoked.

    Args:
        counter: Counter file written by the stub.

    Returns:
        int: The recorded invocation count (0 if never called).
    """
    if not counter.exists():
        return 0
    return int(counter.read_text().strip())


def _signed_refs(bin_dir: Path) -> list[str]:
    """Return the refs the stub cosign was asked to sign, in order.

    Args:
        bin_dir: Directory holding the stub's ``cosign.args`` log.

    Returns:
        list[str]: One entry per cosign invocation (the ``--yes <ref>`` argv).
    """
    args_log = bin_dir / "cosign.args"
    if not args_log.exists():
        return []
    return [line for line in args_log.read_text().splitlines() if line]


def test_help_flag_exits_zero() -> None:
    """--help prints usage and exits 0."""
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_missing_images_is_fatal(tmp_path: Path) -> None:
    """A missing IMAGES value exits 2 before any cosign call."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images="")
    assert_that(result.returncode).is_equal_to(2)
    assert_that(_calls(counter)).is_equal_to(0)


def test_non_digest_ref_is_fatal_and_not_retried(tmp_path: Path) -> None:
    """A floating-tag ref is rejected outright and cosign is never called."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images="ghcr.io/lgtm-hq/py-lintro:latest")
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("Refusing to sign non-digest ref")
    assert_that(_calls(counter)).is_equal_to(0)


def test_success_signs_once(tmp_path: Path) -> None:
    """A clean sign calls cosign exactly once and succeeds."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images=_DIGEST)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(_calls(counter)).is_equal_to(1)
    assert_that(result.stdout).contains("Signed 1 image(s)")


def test_multiple_digests_each_signed(tmp_path: Path) -> None:
    """Every digest ref is signed."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images=f"{_DIGEST}\n{_DIGEST_TWO}")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(_calls(counter)).is_equal_to(2)
    assert_that(result.stdout).contains("Signed 2 image(s)")
    # Both digests must actually be handed to cosign, not just counted.
    signed = _signed_refs(tmp_path)
    assert_that(signed).is_length(2)
    assert_that(any(_DIGEST in ref for ref in signed)).is_true()
    assert_that(any(_DIGEST_TWO in ref for ref in signed)).is_true()


def test_transient_oidc_flake_is_retried_then_succeeds(tmp_path: Path) -> None:
    """An OIDC token-fetch flake is retried and the eventual success passes."""
    counter = _write_fake_cosign(tmp_path, "oidc_then_success", fail_until=2)
    result = _run(tmp_path, images=_DIGEST)
    assert_that(result.returncode).is_equal_to(0)
    # 2 flaked attempts + 1 successful = 3 calls.
    assert_that(_calls(counter)).is_equal_to(3)
    assert_that(result.stderr).contains("retrying")


def test_persistent_oidc_flake_exhausts_attempts_and_fails(tmp_path: Path) -> None:
    """A persistent OIDC flake fails after exactly max_attempts tries."""
    counter = _write_fake_cosign(tmp_path, "oidc_always")
    result = _run(tmp_path, images=_DIGEST, max_attempts="3")
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(_calls(counter)).is_equal_to(3)
    assert_that(result.stderr).contains("after 3 attempt(s)")


def test_genuine_signing_rejection_stays_fatal_and_is_not_retried(
    tmp_path: Path,
) -> None:
    """A real signing rejection fails immediately and is never retried."""
    counter = _write_fake_cosign(tmp_path, "reject")
    result = _run(tmp_path, images=_DIGEST, max_attempts="4")
    assert_that(result.returncode).is_not_equal_to(0)
    # Fatal on the first failure — no retry despite attempts remaining.
    assert_that(_calls(counter)).is_equal_to(1)
    assert_that(result.stderr).contains("not a transient OIDC token-fetch flake")


@pytest.mark.parametrize(
    "bad_value",
    ["0", "-1", "abc", "1.5"],
)
def test_invalid_max_attempts_is_fatal(tmp_path: Path, bad_value: str) -> None:
    """A non-positive or non-integer max-attempts value exits 2."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images=_DIGEST, max_attempts=bad_value)
    assert_that(result.returncode).is_equal_to(2)
    assert_that(_calls(counter)).is_equal_to(0)


@pytest.mark.parametrize(
    "bad_value",
    ["-1", "abc", "1.5"],
)
def test_invalid_max_delay_is_fatal(tmp_path: Path, bad_value: str) -> None:
    """A negative or non-integer max-delay value exits 2 before signing."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images=_DIGEST, max_delay=bad_value)
    assert_that(result.returncode).is_equal_to(2)
    assert_that(_calls(counter)).is_equal_to(0)
