"""Tests for the hadolint installer path in ``install-tools.sh`` (#3264).

The installer runs as a subprocess on a hermetic ``PATH``: ``curl``,
``uname``, ``brew``, ``apt-get`` and ``sleep`` are stubs, and every other
command it needs is a symlink to the real binary. The ``curl`` stub serves
files from a fake release directory and logs each requested URL, so the
tests see exactly which assets the script asks for. Leaving ``sha256sum`` and
``shasum`` off the ``PATH`` simulates a runner with no hash tool.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, controlled test input
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_VERSION = get_tool_version("hadolint")
_RELEASE_URL = f"https://github.com/hadolint/hadolint/releases/download/v{_VERSION}"
_BINARY = b'#!/bin/sh\necho "Haskell Dockerfile Linter fake"\n'
_HASH_TOOLS = ("sha256sum", "shasum")
_REAL_TOOLS = (
    "awk",
    "basename",
    "cat",
    "chmod",
    "cp",
    "dirname",
    "grep",
    "head",
    "mkdir",
    "mktemp",
    "sed",
    "sort",
    "tr",
)

_CURL_STUB = """#!/bin/sh
url=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "curl $url" >> "$STUB_LOG"
name="${url##*/}"
[ -f "$FAKE_RELEASE/$name" ] || exit 22
cp "$FAKE_RELEASE/$name" "$out"
"""

_BREW_STUB = """#!/bin/sh
echo "brew $*" >> "$STUB_LOG"
if [ "$1" = "--prefix" ]; then
  echo "$FAKE_BREW_PREFIX"
fi
exit 0
"""

_UNAME_STUB = """#!/bin/sh
case "$1" in
  -s) echo "$FAKE_OS" ;;
  -m) echo "$FAKE_ARCH" ;;
  *) echo "$FAKE_OS" ;;
esac
"""

# --docker cleans apt lists after install_system_deps; never touch the host's.
_RM_WRAPPER = """#!/bin/sh
case "$*" in
  *"/var/lib/apt/lists"*) exit 0 ;;
esac
exec {rm} "$@"
"""


def _modern_bash() -> str | None:
    """Locate a bash new enough to run the installer.

    ``install-tools.sh`` uses associative arrays, so bash 3.2 (the system bash
    shipped by macOS) cannot run it.

    Returns:
        Path to a bash >= 4 interpreter, or None when only an older one exists.
    """
    bash = shutil.which("bash")
    if bash is None:
        return None
    probe = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    major = probe.stdout.strip()
    return bash if major.isdigit() and int(major) >= 4 else None


_BASH = _modern_bash()

pytestmark = pytest.mark.skipif(
    _BASH is None,
    reason="install-tools.sh requires bash >= 4 (associative arrays)",
)


@dataclass(frozen=True)
class _Run:
    """Outcome of one installer run.

    Attributes:
        returncode: Installer exit status.
        output: Combined stdout and stderr.
        calls: Lines logged by the ``curl`` and ``brew`` stubs.
        installed: Where ``--local`` installs hadolint.
    """

    returncode: int
    output: str
    calls: list[str]
    installed: Path


def _write_exe(path: Path, content: str | bytes) -> None:
    """Write an executable file.

    Args:
        path: Destination path.
        content: Script text or raw bytes.
    """
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _stub_bin(tmp_path: Path, *, hash_tools: bool) -> Path:
    """Build the hermetic ``PATH`` directory.

    Args:
        tmp_path: Per-test temporary directory.
        hash_tools: Whether to expose the host's ``sha256sum``/``shasum``.

    Returns:
        The directory to use as the installer's whole ``PATH``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    linked = _REAL_TOOLS + (_HASH_TOOLS if hash_tools else ())
    for name in linked:
        real = shutil.which(name)
        if real is not None:
            (bin_dir / name).symlink_to(real)
    rm = shutil.which("rm") or "/bin/rm"
    _write_exe(bin_dir / "rm", _RM_WRAPPER.format(rm=rm))
    _write_exe(bin_dir / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    _write_exe(bin_dir / "curl", _CURL_STUB)
    _write_exe(bin_dir / "brew", _BREW_STUB)
    _write_exe(bin_dir / "uname", _UNAME_STUB)
    _write_exe(bin_dir / "apt-get", "#!/bin/sh\nexit 0\n")
    _write_exe(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    return bin_dir


def _sums_line(asset: str, payload: bytes = _BINARY) -> str:
    """Render one ``checksums.sha256`` line in hadolint's binary-mode format.

    Args:
        asset: Release asset name.
        payload: Bytes the line should hash.

    Returns:
        A ``<sha256> *<asset>`` line.
    """
    return f"{hashlib.sha256(payload).hexdigest()} *{asset}\n"


def _run_installer(
    tmp_path: Path,
    *,
    platform: tuple[str, str] = ("Linux", "x86_64"),
    release: dict[str, bytes | str] | None = None,
    hash_tools: bool = True,
    mode: str = "--local",
) -> _Run:
    """Run ``install-tools.sh --tools hadolint`` against stubbed tools.

    Args:
        tmp_path: Per-test temporary directory.
        platform: ``uname -s`` and ``uname -m`` values to report.
        release: Files the fake GitHub release serves, by asset name.
        hash_tools: Whether ``sha256sum``/``shasum`` are on ``PATH``.
        mode: ``--local`` or ``--docker``.

    Returns:
        The installer outcome.
    """
    assert _BASH is not None  # nosec B101 - guarded by pytestmark
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    for name, content in (release or {}).items():
        target = release_dir / name
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    brew_prefix = tmp_path / "brew"
    (brew_prefix / "bin").mkdir(parents=True)
    _write_exe(brew_prefix / "bin" / "hadolint", _BINARY)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    result = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), mode, "--tools", "hadolint"],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        env={
            "HOME": str(home),
            "PATH": str(_stub_bin(tmp_path, hash_tools=hash_tools)),
            "FAKE_OS": platform[0],
            "FAKE_ARCH": platform[1],
            "FAKE_RELEASE": str(release_dir),
            "FAKE_BREW_PREFIX": str(brew_prefix),
            "STUB_LOG": str(log),
        },
    )
    return _Run(
        returncode=result.returncode,
        output=result.stdout + result.stderr,
        calls=log.read_text(encoding="utf-8").splitlines(),
        installed=home / ".local" / "bin" / "hadolint",
    )


@pytest.mark.parametrize(
    ("platform", "asset"),
    [
        (("Linux", "x86_64"), "hadolint-linux-x86_64"),
        (("Linux", "aarch64"), "hadolint-linux-arm64"),
        (("Darwin", "arm64"), "hadolint-macos-arm64"),
        (("Darwin", "x86_64"), "hadolint-macos-x86_64"),
    ],
)
def test_installs_the_upstream_asset_verified_against_the_release_sums(
    tmp_path: Path,
    platform: tuple[str, str],
    asset: str,
) -> None:
    """Each platform fetches its exact upstream asset plus ``checksums.sha256``.

    Args:
        tmp_path: Per-test temporary directory.
        platform: ``uname`` output to simulate.
        asset: Asset name hadolint publishes for that platform.
    """
    run = _run_installer(
        tmp_path,
        platform=platform,
        release={
            asset: _BINARY,
            "checksums.sha256": _sums_line("hadolint-windows-x86_64.exe", b"x")
            + _sums_line(asset),
        },
    )

    assert_that(run.returncode).described_as(run.output).is_equal_to(0)
    assert_that(run.calls).is_equal_to(
        [f"curl {_RELEASE_URL}/{asset}", f"curl {_RELEASE_URL}/checksums.sha256"],
    )
    assert_that(run.output).contains(f"Checksum verified for {asset}")
    assert_that(run.installed.read_bytes()).is_equal_to(_BINARY)


def test_checksum_mismatch_fails_and_installs_nothing(tmp_path: Path) -> None:
    """A binary that does not match its sums line is rejected.

    Args:
        tmp_path: Per-test temporary directory.
    """
    asset = "hadolint-linux-x86_64"
    run = _run_installer(
        tmp_path,
        release={
            asset: _BINARY,
            "checksums.sha256": _sums_line(asset, b"tampered"),
        },
    )

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains(f"Checksum mismatch for {asset}")
    assert_that(run.installed.exists()).is_false()


def test_missing_sums_file_fails_instead_of_skipping(tmp_path: Path) -> None:
    """A release without ``checksums.sha256`` no longer installs unverified.

    Args:
        tmp_path: Per-test temporary directory.
    """
    run = _run_installer(tmp_path, release={"hadolint-linux-x86_64": _BINARY})

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains("refusing to install an unverified hadolint")
    assert_that(run.output).does_not_contain("installed successfully")
    assert_that(run.installed.exists()).is_false()


def test_sums_file_without_the_asset_line_fails(tmp_path: Path) -> None:
    """A sums file that does not list the asset cannot vouch for it.

    Args:
        tmp_path: Per-test temporary directory.
    """
    run = _run_installer(
        tmp_path,
        release={
            "hadolint-linux-x86_64": _BINARY,
            "checksums.sha256": _sums_line("hadolint-linux-arm64"),
        },
    )

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains(
        "No checksum entry for hadolint-linux-x86_64 in checksums.sha256",
    )
    assert_that(run.installed.exists()).is_false()


def test_missing_hash_tool_fails_instead_of_skipping(tmp_path: Path) -> None:
    """Without ``sha256sum`` or ``shasum`` the install fails closed.

    Args:
        tmp_path: Per-test temporary directory.
    """
    asset = "hadolint-linux-x86_64"
    run = _run_installer(
        tmp_path,
        release={asset: _BINARY, "checksums.sha256": _sums_line(asset)},
        hash_tools=False,
    )

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains("no hash tool found")
    assert_that(run.output).does_not_contain("skipping verification")
    assert_that(run.installed.exists()).is_false()


def test_failed_download_on_a_supported_platform_does_not_use_brew(
    tmp_path: Path,
) -> None:
    """A published asset that fails to download is an error, not a brew cue.

    Args:
        tmp_path: Per-test temporary directory.
    """
    run = _run_installer(tmp_path, platform=("Darwin", "arm64"), release={})

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains(
        f"Failed to download {_RELEASE_URL}/hadolint-macos-arm64",
    )
    assert_that([call for call in run.calls if call.startswith("brew")]).is_empty()


def test_local_mode_uses_brew_only_when_no_asset_exists(tmp_path: Path) -> None:
    """``--local`` falls back to Homebrew for an unpublished platform and says why.

    Args:
        tmp_path: Per-test temporary directory.
    """
    run = _run_installer(tmp_path, platform=("Linux", "riscv64"))

    assert_that(run.returncode).described_as(run.output).is_equal_to(0)
    assert_that(run.output).contains(
        "hadolint publishes no release asset for Linux-riscv64; "
        "falling back to Homebrew",
    )
    assert_that(run.calls).contains("brew install hadolint")
    assert_that([call for call in run.calls if call.startswith("curl")]).is_empty()
    assert_that(run.installed.read_bytes()).is_equal_to(_BINARY)


@pytest.mark.skipif(
    Path("/usr/local/bin/brew").exists(),
    reason="--docker prepends /usr/local/bin, which would shadow the brew stub",
)
def test_docker_mode_never_falls_back_to_brew(tmp_path: Path) -> None:
    """The tools image fails rather than installing an unpinned brew hadolint.

    Args:
        tmp_path: Per-test temporary directory.
    """
    run = _run_installer(tmp_path, platform=("Linux", "riscv64"), mode="--docker")

    assert_that(run.returncode).is_not_equal_to(0)
    assert_that(run.output).contains("--docker does not fall back to Homebrew")
    assert_that(run.calls).is_empty()
