"""Contract tests for the Docker image's volume-permission handling.

These tests guard the mechanism that lets lintro auto-install Node.js
dependencies into a volume-mounted project without consumers passing
``--user``. The container must start as root so ``entrypoint.sh`` can detect
the UID/GID that owns ``/code`` and drop privileges to it via ``gosu``.

Regression guard for issue #592: if ``USER lintro`` is reintroduced into the
runtime stages, the container starts non-root, the ``id -u = 0`` branch in the
entrypoint never runs, and the volume-owner detection becomes dead code.
"""

from __future__ import annotations

import re
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_ENTRYPOINT = _REPO_ROOT / "scripts" / "docker" / "entrypoint.sh"


def _dockerfile_text() -> str:
    """Read the repository Dockerfile.

    Returns:
        The full text of the Dockerfile.
    """
    return _DOCKERFILE.read_text(encoding="utf-8")


def _entrypoint_text() -> str:
    """Read the Docker entrypoint script.

    Returns:
        The full text of scripts/docker/entrypoint.sh.
    """
    return _ENTRYPOINT.read_text(encoding="utf-8")


def test_dockerfile_has_no_user_lintro_directive() -> None:
    """Runtime stages must not pin ``USER lintro``.

    A ``USER lintro`` directive makes the container start non-root, which
    disables the entrypoint's root-only volume-owner detection. Comments that
    merely mention the string are fine; an actual instruction is not.
    """
    directive = re.compile(r"^\s*USER\s+lintro\b", re.MULTILINE)
    assert_that(directive.search(_dockerfile_text())).is_none()


def test_dockerfile_has_no_root_user_directive() -> None:
    """No stage should end pinned to ``USER root`` either.

    Starting as root is intentional (entrypoint drops privileges via gosu), but
    it is achieved by omitting USER entirely, not by an explicit ``USER root``
    (which trips hadolint DL3002).
    """
    directive = re.compile(r"^\s*USER\s+(root|0)\b", re.MULTILINE)
    assert_that(directive.search(_dockerfile_text())).is_none()


def test_dockerfile_installs_gosu() -> None:
    """Gosu must be installed so the entrypoint can drop privileges."""
    assert_that(_dockerfile_text()).contains("gosu")


def test_entrypoint_detects_volume_owner_and_reexecs_via_gosu() -> None:
    """Entrypoint must re-exec as the /code owner when running as root."""
    text = _entrypoint_text()
    assert_that(text).contains("if [ \"$(id -u)\" = '0' ]")
    assert_that(text).contains("stat -c '%u' /code")
    assert_that(text).contains("stat -c '%g' /code")
    assert_that(text).contains('exec gosu "$CODE_UID:$CODE_GID"')


def test_entrypoint_does_not_chown_code_tree() -> None:
    """Entrypoint must never chown the mounted source tree.

    ``chown -R /code`` is vulnerable to symlink attacks on untrusted repos and
    mutates host file ownership. UID matching replaces it.
    """
    assert_that(_entrypoint_text()).does_not_match(r"chown\s+.*\b/code\b")


def test_entrypoint_reexec_avoids_infinite_loop() -> None:
    """The gosu re-exec must be guarded so it does not loop forever.

    When /code is already owned by the current UID/GID (e.g. root-owned mount),
    re-execing again would recurse indefinitely.
    """
    text = _entrypoint_text()
    assert_that(text).contains("CUR_UID=$(id -u)")
    assert_that(text).contains('"$CODE_UID" != "$CUR_UID"')
