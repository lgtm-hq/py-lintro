"""Tests for ``scripts/ci/restore-codex-session.sh``.

The openai dogfood lane (#2472) authenticates ``codex`` through the
ChatGPT-plan session file, delivered to CI as the base64 CODEX_AUTH_JSON
secret. These tests cover the script's local behavior — unset secret,
decode, permissions, malformed payloads — without touching the network.
"""

from __future__ import annotations

import base64
import os
import subprocess  # nosec B404 - subprocess is used to drive the script under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "restore-codex-session.sh"

VALID_SESSION = '{"OPENAI_API_KEY": null, "tokens": {"access_token": "a"}}'


def _run(
    *,
    env_overrides: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the restore script with a controlled environment.

    Args:
        env_overrides: Environment variables layered onto a minimal base.
        args: Optional positional arguments passed to the script.

    Returns:
        The completed subprocess result.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary; shell=False
        [str(SCRIPT), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
    )


def _encoded(payload: str) -> str:
    """Return the base64 encoding CI would store in the CODEX_AUTH_JSON secret.

    Args:
        payload: Raw session JSON.

    Returns:
        Base64-encoded payload.
    """
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def test_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = _run(env_overrides={}, args=["--help"])

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("CODEX_AUTH_JSON")


def test_unset_secret_warns_and_exits_zero_without_writing(tmp_path: Path) -> None:
    """An unset secret warns and no-ops: the wrapper reports it via classifier.

    Substituting a placeholder or failing hard here would break the
    no-silent-skip contract — the credential gate owns the visible failure.
    """
    result = _run(env_overrides={"HOME": str(tmp_path)})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("::warning")
    assert_that(tmp_path.joinpath(".codex", "auth.json").exists()).is_false()


def test_empty_secret_is_treated_as_unset(tmp_path: Path) -> None:
    """An empty CODEX_AUTH_JSON follows the unset contract exactly.

    Empty and unset are the same delivery failure (the org secret exists but
    resolved to nothing), so both warn and exit 0 for the credential gate to
    report — neither may fail the step itself.
    """
    result = _run(env_overrides={"HOME": str(tmp_path), "CODEX_AUTH_JSON": ""})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("::warning")
    assert_that(tmp_path.joinpath(".codex", "auth.json").exists()).is_false()


def test_valid_secret_is_decoded_with_owner_only_permissions(tmp_path: Path) -> None:
    """A valid session lands at $HOME/.codex/auth.json, mode 600.

    The file contains live tokens, so it must not be group/world readable,
    and its content must round-trip the base64 exactly.
    """
    result = _run(
        env_overrides={
            "HOME": str(tmp_path),
            "CODEX_AUTH_JSON": _encoded(VALID_SESSION),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    auth_file = tmp_path.joinpath(".codex", "auth.json")
    assert_that(auth_file.read_text(encoding="utf-8")).is_equal_to(VALID_SESSION)
    assert_that(oct(auth_file.stat().st_mode & 0o777)).is_equal_to("0o600")


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not base64 at all!",
        "[]",
        # Both decode cleanly and start with "{", but are not valid JSON —
        # exactly what a truncated secret looks like at a 4-char boundary.
        "{not-json",
        '{"OPENAI_API_KEY": "trunca',
    ],
)
def test_malformed_secret_fails_and_cleans_up(
    tmp_path: Path,
    bad_payload: str,
) -> None:
    """A secret that does not decode to a JSON object fails with a clear error.

    A mistyped secret should fail here, loudly, rather than surface later as
    a confusing CLI auth error — and the partial file must not linger.
    (The empty payload is not in this list: empty follows the unset
    contract — warn + exit 0 — covered by its own test above.)
    """
    encoded = _encoded(bad_payload)

    result = _run(env_overrides={"HOME": str(tmp_path), "CODEX_AUTH_JSON": encoded})

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("did not decode to a JSON session file")
    assert_that(tmp_path.joinpath(".codex", "auth.json").exists()).is_false()


def test_undecodable_secret_fails_and_cleans_up(tmp_path: Path) -> None:
    """A value that is not base64 at all fails at the decode step.

    The parametrized cases above travel through ``_encoded`` and so arrive
    as decodable base64, exercising the JSON guard; this one supplies raw
    garbage so the ``base64 --decode`` failure branch itself is covered —
    the "raw JSON pasted unencoded" mistake from the script's comment.
    """
    result = _run(
        env_overrides={"HOME": str(tmp_path), "CODEX_AUTH_JSON": "!!!not-base64!!!"},
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("did not decode to a JSON session file")
    assert_that(tmp_path.joinpath(".codex", "auth.json").exists()).is_false()
