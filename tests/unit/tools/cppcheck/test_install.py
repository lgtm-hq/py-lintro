"""Installer and image wiring tests for cppcheck."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"


def test_installer_knows_cppcheck() -> None:
    """``install-tools.sh`` has a ``cppcheck`` installer and accepts the name."""
    text = _INSTALL_TOOLS.read_text(encoding="utf-8")

    assert_that(text).contains('should_install "cppcheck"')
    assert_that(text).contains('"cppcheck"')


def test_tools_image_installs_and_verifies_the_binary() -> None:
    """``docker/tools.Dockerfile`` proves ``cppcheck`` is on the image PATH."""
    text = _TOOLS_DOCKERFILE.read_text(encoding="utf-8")

    assert_that(text).contains("cppcheck --version")


def test_app_image_bridges_cppcheck_until_the_next_tools_digest() -> None:
    """The app image FROMs a digest-pinned tools base that predates this tool.

    Until that digest is republished with ``cppcheck`` on PATH, the app image
    must install it itself, or the manifest-vs-image gate
    (``scripts/ci/verify-image-manifest-tools.sh``) fails with exit code 127
    for ``cppcheck``. This bridge is a no-op once the pinned digest already
    carries the binary.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    bridge_at = text.find("install-tools.sh --docker --tools ")

    assert_that(bridge_at).is_not_equal_to(-1)
    bridge_line = text[bridge_at : text.index("\n", bridge_at)]
    assert_that(bridge_line).contains("cppcheck")
