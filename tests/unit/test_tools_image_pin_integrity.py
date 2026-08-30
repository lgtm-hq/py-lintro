"""Guards that the tools image keeps rustc and PyPI pins from floating.

The app image copies binaries from a digest-pinned ``lintro-tools`` layer.
Main Docker verify has no ``--allow-version-lag``, so a published tools
image that silently installs ``stable`` rustc or latest ``ruff>=`` fails
after a versions-only merge (#2139, #2220).
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed bash probe exercises repository script
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


def _shell_function(text: str, name: str) -> str:
    """Extract one top-level shell function for an isolated behavior test."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


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
    text = _executable_lines(_INSTALL_TOOLS.read_text(encoding="utf-8"))

    assert_that(text).contains("--profile minimal --component")
    assert_that(text).contains(
        'rustup toolchain install "$RUST_TOOLCHAIN_VERSION" --profile minimal',
    )


def test_install_python_package_uses_uv_tool_for_local_shims() -> None:
    """Local uv installs write the pinned executable directly into ``BIN_DIR``.

    Looking up the executable with ``command -v`` after installation can return
    a stale ``BIN_DIR`` entry because that directory is first on ``PATH``.
    """
    text = _executable_lines(_INSTALL_TOOLS.read_text(encoding="utf-8"))

    assert_that(text).does_not_contain("uv run which")
    assert_that(text).does_not_contain('command -v "$package"')
    assert_that(text).contains('full_package="$package==$version"')
    assert_that(text).contains('UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force')
    assert_that(text).contains(
        'pip install --ignore-installed --prefix "$install_prefix"',
    )


def test_local_uv_install_replaces_stale_bin_dir_executable(tmp_path: Path) -> None:
    """A successful local uv install must replace an existing stale shim.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stale = bin_dir / "ruff"
    stale.write_text("#!/bin/sh\necho stale\n", encoding="utf-8")
    stale.chmod(0o755)

    function = _shell_function(
        _INSTALL_TOOLS.read_text(encoding="utf-8"),
        "install_python_package",
    )
    probe = f"""
set -euo pipefail
BIN_DIR="$TEST_BIN_DIR"
INSTALL_MODE=local
TOOL_FILTER=""
should_install() {{ return 0; }}
log_verbose() {{ :; }}
uv() {{
    test "$1 $2 $3" = "tool install --force"
    test "$4" = "ruff==0.15.9"
    printf '#!/bin/sh\\necho 0.15.9\\n' >"$UV_TOOL_BIN_DIR/ruff"
    chmod +x "$UV_TOOL_BIN_DIR/ruff"
}}
{function}
install_python_package ruff 0.15.9
"$BIN_DIR/ruff"
"""
    env = os.environ.copy()
    env["TEST_BIN_DIR"] = str(bin_dir)
    result = subprocess.run(  # nosec B603 - fixed bash with controlled test data
        ["/bin/bash", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("0.15.9")
    assert_that(result.stderr).is_empty()


def test_docker_uv_install_uses_pip_without_tool_bin_dir(tmp_path: Path) -> None:
    """A Docker install must use uv pip without local uv tool configuration.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    function = _shell_function(
        _INSTALL_TOOLS.read_text(encoding="utf-8"),
        "install_python_package",
    )
    probe = f"""
set -euo pipefail
BIN_DIR="$TEST_BIN_DIR"
INSTALL_MODE=--docker
TOOL_FILTER=""
should_install() {{ return 0; }}
log_verbose() {{ :; }}
uv() {{
    test "${{UV_TOOL_BIN_DIR+x}}" != x
    test "$#" -eq 3
    test "$1 $2" = "pip install"
    test "$3" = "ruff==0.15.9"
}}
{function}
install_python_package ruff 0.15.9
"""
    env = os.environ.copy()
    env["TEST_BIN_DIR"] = str(tmp_path / "bin")
    result = subprocess.run(  # nosec B603 - fixed bash with controlled test data
        ["/bin/bash", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).is_empty()
    assert_that(result.stderr).is_empty()


def test_pip_fallback_fails_closed_if_executable_stays_stale(
    tmp_path: Path,
) -> None:
    """A zero-exit pip fallback must not accept the stale ``BIN_DIR`` entry.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bin_dir = tmp_path / "prefix" / "bin"
    bin_dir.mkdir(parents=True)
    stale = bin_dir / "ruff"
    stale.write_text("#!/bin/sh\necho stale\n", encoding="utf-8")
    stale.chmod(0o755)

    function = _shell_function(
        _INSTALL_TOOLS.read_text(encoding="utf-8"),
        "install_python_package",
    )
    probe = f"""
set -euo pipefail
BIN_DIR="$TEST_BIN_DIR"
INSTALL_MODE=local
TOOL_FILTER=""
RED=""
NC=""
should_install() {{ return 0; }}
log_verbose() {{ :; }}
uv() {{ return 1; }}
pip() {{
    test "$#" -eq 5
    test "$1 $2 $3" = "install --ignore-installed --prefix"
    test "$4" = "$TEST_PREFIX"
    test "$5" = "ruff==0.15.9"
    test ! -e "$BIN_DIR/ruff"
    return 0
}}
brew() {{ return 98; }}
{function}
if install_python_package ruff 0.15.9; then
    "$BIN_DIR/ruff"
    exit 99
fi
test ! -e "$BIN_DIR/ruff"
"""
    env = os.environ.copy()
    env["TEST_BIN_DIR"] = str(bin_dir)
    env["TEST_PREFIX"] = str(bin_dir.parent)
    result = subprocess.run(  # nosec B603 - fixed bash with controlled test data
        ["/bin/bash", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).is_empty()
    assert_that(result.stderr).contains(
        f"pip installed ruff==0.15.9 but did not create {stale}",
    )
