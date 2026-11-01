"""Tests for scripts/ci/cosign-sign-images.sh retry and verified recovery.

These exercise the signing wrapper against a stubbed ``cosign`` on PATH so no
real Sigstore signing happens. They pin the behaviours #1646 and #3114 need:

* the transient ambient-OIDC token-fetch flake is retried with bounded backoff;
* a genuine signing rejection (or any non-token-fetch error) stays fatal and is
  never retried away.
* equivalent-entry conflicts require exact digest, identity and issuer
  verification; duplicate immutable refs are signed once per repository.
"""

from __future__ import annotations

import os
import shlex
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
_REKOR_CONFLICT = (
    "[POST /api/v1/log/entries][409] createLogEntryConflict "
    '{"code":409,"message":"an equivalent entry already exists in the '
    "transparency log with UUID 108e9186e8c5677a42ab5396acea2a5a2f3e82c2bfc2ba024"
    '7614a172096bc2eb41131500e82ea31"}'
)
_IDENTITY_REGEXP = (
    r"^https://github\.com/lgtm-hq/(py-lintro/\.github/workflows/"
    r"(docker-ci|publish-pypi-on-tag)\.yml|lgtm-ci/\.github/workflows/"
    r"reusable-docker-(build|multiplatform)\.yml)@"
)
_ISSUER = "https://token.actions.githubusercontent.com"
_WORKFLOW_REF = (
    "lgtm-hq/py-lintro/.github/workflows/publish-pypi-on-tag.yml@refs/tags/v0.175.4"
)
_WORKFLOW_IDENTITY = f"https://github.com/{_WORKFLOW_REF}"
_BUILD_IDENTITY = (
    "https://github.com/lgtm-hq/lgtm-ci/.github/workflows/"
    "reusable-docker-multiplatform.yml@refs/tags/v0.78.1"
)
_DOCKER_WORKFLOW_REF = (
    "lgtm-hq/py-lintro/.github/workflows/docker-ci.yml@refs/heads/main"
)


def _write_fake_cosign(bin_dir: Path, mode: str, *, fail_until: int = 0) -> Path:
    """Write a stub ``cosign`` that records call count and simulates a mode.

    Args:
        bin_dir: Directory placed at the front of PATH.
        mode: One of ``success``, ``reject``, ``oidc_always``,
            ``oidc_then_success``, ``duplicate_conflict``.
        fail_until: For ``oidc_then_success``, the number of leading calls that
            fail with the OIDC flake before one succeeds.

    Returns:
        Path: Path to the counter file the stub increments on each call.
    """
    counter = bin_dir / "cosign.calls"
    args_log = bin_dir / "cosign.args"
    oidc = _OIDC_MESSAGE % "REF"
    reject = _REJECT_MESSAGE % "REF"
    conflict = shlex.quote(_REKOR_CONFLICT)
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
          duplicate_conflict)
            if [[ $(grep -cFx -- "$*" "$args_log") -gt 1 ]]; then
              printf '%s\\n' {conflict} >&2
              exit 17
            fi
            ;;
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


def _write_rekor_cosign(
    bin_dir: Path,
    *,
    error: str = _REKOR_CONFLICT,
    verification: str = "success",
    signature_identity: str = _WORKFLOW_IDENTITY,
    oidc_first: bool = False,
) -> None:
    """Fake a signing conflict and require the full verification policy.

    Args:
        bin_dir: Directory placed at the front of PATH.
        error: Signing failure text, returned with exit status 17.
        verification: Verification result to simulate after checking arguments.
        signature_identity: Identity on the existing signature.
        oidc_first: Fail the first signing attempt with an OIDC flake.
    """
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        printf '%s\\n' "$*" >>{shlex.quote(str(bin_dir / 'cosign.args'))}
        case "$1" in
          sign)
            if [[ "${{@: -1}}" != {shlex.quote(_DIGEST)} ]]; then exit 0; fi
            if [[ {int(oidc_first)} -eq 1 &&
                  $(grep -c '^sign ' {shlex.quote(str(bin_dir / 'cosign.args'))}) -eq 1 ]]; then
              printf '%s\\n' {shlex.quote(_OIDC_MESSAGE % 'REF')} >&2
              exit 1
            fi
            printf '%s\\n' {shlex.quote(error)} >&2
            exit 17
            ;;
          verify)
            if [[ $# -ne 7 || "$2" != --new-bundle-format ||
                  "$3" != {shlex.quote(_DIGEST)} ||
                  "$6" != --certificate-oidc-issuer ||
                  "$7" != {shlex.quote(_ISSUER)} ]]; then
              echo 'verification policy mismatch' >&2
              exit 99
            fi
            identity={shlex.quote(signature_identity)}
            case "$4" in
              --certificate-identity-regexp)
                if [[ "$5" != {shlex.quote(_IDENTITY_REGEXP)} ]]; then exit 99; fi
                if ! [[ "$identity" =~ $5 ]]; then exit 23; fi
                ;;
              --certificate-identity)
                if [[ "$5" != "$identity" ]]; then
                  echo 'certificate identity does not match current workflow' >&2
                  exit 23
                fi
                ;;
              *) exit 99 ;;
            esac
            if [[ {shlex.quote(verification)} != success ]]; then
              printf '%s\\n' {shlex.quote(verification)} >&2
              exit 23
            fi
            echo 'Verified trusted signature for exact digest'
            ;;
          *) exit 99 ;;
        esac
        """,
    )
    cosign = bin_dir / "cosign"
    cosign.write_text(script)
    cosign.chmod(0o755)


def _run(
    bin_dir: Path,
    *,
    images: str,
    max_attempts: str = "4",
    max_delay: str = "30",
    workflow_ref: str | None = _WORKFLOW_REF,
) -> subprocess.CompletedProcess[str]:
    """Run the signing script with the stub PATH and no backoff wait.

    Args:
        bin_dir: Directory holding the stub ``cosign`` to prepend to PATH.
        images: Value for the IMAGES environment variable.
        max_attempts: Value for COSIGN_SIGN_MAX_ATTEMPTS.
        max_delay: Value for COSIGN_SIGN_MAX_DELAY.
        workflow_ref: Current workflow identity, or None to omit it.

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
    env.pop("GITHUB_WORKFLOW_REF", None)
    if workflow_ref is not None:
        env["GITHUB_WORKFLOW_REF"] = workflow_ref
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
    """Return the signing and verification arguments, in order.

    Args:
        bin_dir: Directory holding the stub's ``cosign.args`` log.

    Returns:
        list[str]: One entry per cosign invocation.
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
    assert_that(result.stdout).contains("Bash 4 or newer", "GITHUB_WORKFLOW_REF")


def test_missing_images_is_fatal(tmp_path: Path) -> None:
    """A missing IMAGES value exits 2 before any cosign call."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images="")
    assert_that(result.returncode).is_equal_to(2)
    assert_that(_calls(counter)).is_equal_to(0)


@pytest.mark.parametrize(
    "ref",
    [
        "ghcr.io/lgtm-hq/py-lintro:latest",
        "ghcr.io/lgtm-hq/py-lintro@sha256:",
        "ghcr.io/lgtm-hq/py-lintro@sha256:abc123",
        _DIGEST + "junk",
        _DIGEST + "\t" + _DIGEST_TWO,
    ],
)
def test_non_digest_ref_is_fatal_and_not_retried(tmp_path: Path, ref: str) -> None:
    """Only complete, unambiguous digest refs may reach signing or recovery."""
    counter = _write_fake_cosign(tmp_path, "success")
    result = _run(tmp_path, images=f"{_DIGEST}\n{ref}")
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


def test_duplicate_digest_refs_are_signed_once(tmp_path: Path) -> None:
    """Repeated immutable refs cannot trigger a second signing conflict."""
    _write_fake_cosign(tmp_path, "duplicate_conflict")
    result = _run(tmp_path, images=f"{_DIGEST}\n{_DIGEST}\n{_DIGEST_TWO}\n{_DIGEST}")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(_signed_refs(tmp_path)).is_equal_to(
        [f"sign --yes {_DIGEST}", f"sign --yes {_DIGEST_TWO}"],
    )
    assert_that(result.stdout).contains("Signed 2 image(s)")


def test_same_digest_in_different_repositories_is_signed_in_each(
    tmp_path: Path,
) -> None:
    """Signature storage is repository scoped even when digest bytes match."""
    _write_fake_cosign(tmp_path, "success")
    other_repo = _DIGEST.replace("/py-lintro@", "/py-lintro-base@")
    result = _run(tmp_path, images=f"{_DIGEST}\n{other_repo}")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(_signed_refs(tmp_path)).is_equal_to(
        [f"sign --yes {_DIGEST}", f"sign --yes {other_repo}"],
    )


@pytest.mark.parametrize(
    "workflow_ref",
    [
        _WORKFLOW_REF,
        _WORKFLOW_REF.replace("refs/tags/v0.175.4", "refs/heads/main"),
        _DOCKER_WORKFLOW_REF,
    ],
)
def test_equivalent_rekor_entry_requires_trusted_digest_verification(
    tmp_path: Path,
    workflow_ref: str,
) -> None:
    """Both callers recover only with their own exact workflow signature."""
    identity = f"https://github.com/{workflow_ref}"
    _write_rekor_cosign(tmp_path, signature_identity=identity)
    result = _run(
        tmp_path,
        images=f"{_DIGEST}\n{_DIGEST_TWO}",
        workflow_ref=workflow_ref,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Verified trusted signature for exact digest")
    assert_that(_signed_refs(tmp_path)).is_equal_to(
        [
            f"sign --yes {_DIGEST}",
            f"verify --new-bundle-format {_DIGEST} --certificate-identity "
            f"{identity} --certificate-oidc-issuer {_ISSUER}",
            f"sign --yes {_DIGEST_TWO}",
        ],
    )
    # Recovery must keep using the published trust policy.
    assert_that(_SCRIPT.read_text()).contains(f"'{_IDENTITY_REGEXP}'")
    for doc in ("docker.md", "release-validation.md"):
        assert_that((_REPO_ROOT / "docs" / doc).read_text()).contains(
            f"--certificate-identity-regexp '{_IDENTITY_REGEXP}'",
            f"--certificate-oidc-issuer {_ISSUER}",
        )


@pytest.mark.parametrize(
    "signature_identity",
    [
        _BUILD_IDENTITY,
        f"https://github.com/{_DOCKER_WORKFLOW_REF}",
        _WORKFLOW_IDENTITY.replace("v0.175.4", "v0.175.3"),
    ],
)
def test_rekor_conflict_cannot_recover_with_another_trusted_workflow(
    tmp_path: Path,
    signature_identity: str,
) -> None:
    """Other workflows or refs cannot prove this caller stored its signature."""
    _write_rekor_cosign(tmp_path, signature_identity=signature_identity)
    result = _run(tmp_path, images=_DIGEST)
    assert_that(result.returncode).is_equal_to(17)
    assert_that(result.stdout).does_not_contain("Signed 1 image(s)")
    assert_that(result.stderr).contains("certificate identity does not match")


def test_oidc_flake_then_rekor_conflict_verifies_current_workflow(
    tmp_path: Path,
) -> None:
    """A later 409 verifies the caller after the permitted OIDC retry."""
    _write_rekor_cosign(tmp_path, oidc_first=True)
    result = _run(tmp_path, images=_DIGEST)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).contains("retrying in 0s")
    assert_that(result.stdout).contains("Signed 1 image(s)")
    assert_that(_signed_refs(tmp_path)).is_equal_to(
        [
            f"sign --yes {_DIGEST}",
            f"sign --yes {_DIGEST}",
            f"verify --new-bundle-format {_DIGEST} --certificate-identity "
            f"{_WORKFLOW_IDENTITY} --certificate-oidc-issuer {_ISSUER}",
        ],
    )


@pytest.mark.parametrize(
    "workflow_ref",
    [
        None,
        "",
        _WORKFLOW_REF.replace("lgtm-hq/", "attacker/"),
        _WORKFLOW_REF.replace("publish-pypi-on-tag.yml", "untrusted.yml"),
        _WORKFLOW_REF.replace(".yml@", ".yml.evil@"),
        _WORKFLOW_REF.replace("refs/tags/v0.175.4", ""),
        _WORKFLOW_REF.replace("refs/tags/v0.175.4", "refs/heads/invalid..ref"),
        _WORKFLOW_REF + "\nextra",
    ],
)
def test_rekor_recovery_requires_a_valid_trusted_workflow_ref(
    tmp_path: Path,
    workflow_ref: str | None,
) -> None:
    """Missing, malformed or untrusted caller identities cannot recover."""
    _write_rekor_cosign(tmp_path)
    result = _run(tmp_path, images=_DIGEST, workflow_ref=workflow_ref)
    assert_that(result.returncode).is_equal_to(17)
    assert_that(result.stderr).contains("GITHUB_WORKFLOW_REF")
    assert_that(_signed_refs(tmp_path)).is_equal_to([f"sign --yes {_DIGEST}"])


@pytest.mark.parametrize(
    "verification",
    [
        "no signatures found",
        "certificate identity does not match",
        "certificate OIDC issuer does not match",
        "signature digest does not match",
        "invalid signature",
        "verification registry unavailable",
    ],
)
def test_rekor_conflict_with_failed_verification_stays_fatal(
    tmp_path: Path,
    verification: str,
) -> None:
    """A conflict is never sufficient when the required verification fails."""
    _write_rekor_cosign(tmp_path, verification=verification)
    result = _run(tmp_path, images=f"{_DIGEST}\n{_DIGEST_TWO}")
    assert_that(result.returncode).is_equal_to(17)
    assert_that(result.stderr).contains(verification)
    assert_that(_signed_refs(tmp_path)).is_length(2)
    assert_that(result.stdout).does_not_contain("Signed 2 image(s)")
    assert_that(result.stderr).does_not_contain("retrying")


@pytest.mark.parametrize(
    "error",
    [
        "registry returned HTTP 409 Conflict",
        '[POST /api/v1/log/entries][409] createLogEntryConflict {"code":409}',
        "an equivalent entry already exists in the transparency log",
        _REKOR_CONFLICT.replace("[409]", "[500]"),
        "Rekor returned HTTP 200",
        "registry authorization denied",
        "signature invalid",
    ],
)
def test_other_signing_errors_never_try_signature_recovery(
    tmp_path: Path,
    error: str,
) -> None:
    """Unknown errors fail even when the stub could verify a signature."""
    _write_rekor_cosign(tmp_path, error=error)
    result = _run(tmp_path, images=_DIGEST)
    assert_that(result.returncode).is_equal_to(17)
    assert_that(_signed_refs(tmp_path)).is_equal_to([f"sign --yes {_DIGEST}"])
    assert_that(result.stderr).contains("not a transient OIDC token-fetch flake")


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
