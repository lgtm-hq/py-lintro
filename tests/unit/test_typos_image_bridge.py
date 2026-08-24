"""The CI app image must ship typos even before the next tools-image digest."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"


def test_ci_dockerfile_bridges_typos_until_tools_digest_includes_it() -> None:
    """Dogfood uses the app image FROM a digest-pinned tools base.

    ``typos`` is added to ``install-tools.sh`` and ``docker/tools.Dockerfile``,
    but that binary is absent from the published digest until Renovate bumps
    it. Without a bridge install, the no-silent-skip gate fails with
    ``binary_missing``.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert_that(text).contains("COPY scripts/utils/install-tools.sh")
    assert_that(text).contains("COPY scripts/utils/utils.sh")
    install_at = text.find("install-tools.sh --docker --tools typos")
    smoke_at = text.find("typos --version")
    assert_that(install_at).is_not_equal_to(-1)
    assert_that(smoke_at).is_not_equal_to(-1)
    assert_that(install_at).is_less_than(smoke_at)


def test_install_tools_script_installs_typos() -> None:
    """The shared installer must have a typos block the Dockerfile can invoke."""
    text = _INSTALL_TOOLS.read_text(encoding="utf-8")
    assert_that(text).contains('should_install "typos"')
    assert_that(text).contains("typos-cli")
    tools_df = _TOOLS_DOCKERFILE.read_text(encoding="utf-8")
    assert_that(tools_df).contains("typos --version")
