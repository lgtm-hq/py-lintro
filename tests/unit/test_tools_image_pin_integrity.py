"""Guards that the tools image keeps rustc and PyPI pins from floating.

The app image copies binaries from a digest-pinned ``lintro-tools`` layer.
Main Docker verify has no ``--allow-version-lag``, so a published tools
image that silently installs ``stable`` rustc or latest ``ruff>=`` fails
after a versions-only merge (#2139, #2220).
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"


def _executable_lines(text: str) -> str:
    """Return script text with comments and blanks removed.

    Args:
        text: Full file contents.

    Returns:
        Newline-joined executable lines.
    """
    return "\n".join(
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_tools_dockerfile_does_not_default_rustup_to_stable() -> None:
    """``rustup default stable`` overwrites the rustc pin with today's stable.

    A no-cache tools publish then ships rustc 1.98 while
    ``_tool_versions.py`` still says 1.97.1, which fails verify on main.
    """
    text = _executable_lines(_TOOLS_DOCKERFILE.read_text(encoding="utf-8"))

    assert_that(text).does_not_contain("rustup default stable")
    assert_that(text).does_not_contain("toolchain install stable")
    assert_that(text).contains("install-tools.sh --docker")


def test_install_tools_uses_minimal_profile_for_pinned_rustc() -> None:
    """The rustc pin must install with ``--profile minimal``, not default.

    The default profile pulls rust-docs (#1703). The tools image used to
    paper over that by installing ``stable`` with ``--profile minimal``
    and making it the default — which floated rustc.
    """
    text = _INSTALL_TOOLS.read_text(encoding="utf-8")

    assert_that(text).contains("--profile minimal --component")
    assert_that(text).contains(
        'rustup toolchain install "$RUST_TOOLCHAIN_VERSION" --profile minimal',
    )


def test_install_python_package_does_not_use_uv_run_which() -> None:
    """``uv run which`` syncs pyproject.toml ranges and floats the pin.

    ``uv pip install ruff==0.15.9`` is exact, but ``uv run which ruff``
    from the repo root resolves ``ruff>=0.15.9`` to latest and copies
    that binary into ``BIN_DIR``.
    """
    text = _INSTALL_TOOLS.read_text(encoding="utf-8")

    assert_that(text).does_not_contain("uv run which")
    assert_that(text).contains('full_package="$package==$version"')
    assert_that(text).contains('installed_path=$(command -v "$package"')
