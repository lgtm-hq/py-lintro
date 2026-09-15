"""Tests for scripts/ci/npm/write_package_checksums.py (#2632).

The manifest must list exactly the files npm would pack, with digests over
the bytes on disk, so lgtm-ci's package-set reusable can verify the staged
set against the stage job's attestation before ``npm pack``. npm itself is
stubbed with a tiny executable that reports a fixed file list per package.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess  # nosec B404 - drives the script under test with shell=False
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "npm" / "write_package_checksums.py"


def _load_module() -> ModuleType:
    """Import the script as a module.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("write_package_checksums", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file.

    Args:
        path: File to hash.

    Returns:
        Lower-case hex digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_fake_npm(bin_dir: Path, *, mode: str = "ok") -> Path:
    """Write a fake ``npm`` that answers ``pack --dry-run --json``.

    The file list comes from a ``PACKED_FILES`` file in the package directory
    (one path per line) so each test controls what npm "would pack".

    Args:
        bin_dir: Directory to place the stub in.
        mode: ``ok`` reports the list; ``fail`` exits non-zero; ``garbage``
            prints invalid JSON; ``empty`` reports no files.

    Returns:
        Path to the stub.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "npm"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'MODE="{mode}"\n'
        'if [[ "$*" != "pack --dry-run --json --ignore-scripts" ]]; then\n'
        '  echo "unexpected npm argv: $*" >&2; exit 99\n'
        "fi\n"
        'case "$MODE" in\n'
        "  fail) echo 'npm error boom' >&2; exit 1 ;;\n"
        "  garbage) echo 'not json'; exit 0 ;;\n"
        "  empty) echo '[{\"files\": []}]'; exit 0 ;;\n"
        "esac\n"
        "printf '[{\"files\": ['\n"
        "first=1\n"
        "while IFS= read -r p; do\n"
        '  [[ -n "$p" ]] || continue\n'
        '  if [[ $first -eq 0 ]]; then printf ","; fi\n'
        "  first=0\n"
        '  printf \'{"path": "%s"}\' "$p"\n'
        "done < PACKED_FILES\n"
        "printf ']}]\\n'\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _make_package(root: Path, name: str, files: dict[str, bytes]) -> Path:
    """Create a package directory with the given files and a PACKED_FILES list.

    Args:
        root: Packages directory.
        name: Package subdirectory name.
        files: Package-relative path -> content.

    Returns:
        The package directory.
    """
    package = root / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": f"@lgtm-hq/lintro-{name}", "version": "1.0.0"}),
        encoding="utf-8",
    )
    for rel, content in files.items():
        target = package / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (package / "PACKED_FILES").write_text(
        "\n".join(["package.json", *files]) + "\n",
        encoding="utf-8",
    )
    return package


@pytest.fixture
def packages(tmp_path: Path) -> Path:
    """Two packages with a binary and a launcher each.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The packages directory.
    """
    root = tmp_path / "npm"
    root.mkdir()
    _make_package(root, "linux-x64", {"bin/lintro": b"\x7fELF", "index.js": b"x"})
    _make_package(
        root,
        "lintro",
        {"bin/lintro": b"#!/usr/bin/env node", "lib/resolve.js": b"y"},
    )
    # A stray non-package directory must be ignored.
    (root / "not-a-package").mkdir()
    return root


def _run(
    packages_dir: Path,
    output: Path,
    *,
    npm_bin: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the script with the stub npm on NPM_CMD.

    Args:
        packages_dir: ``--packages-dir`` value.
        output: ``--output`` value.
        npm_bin: The stub npm executable.

    Returns:
        The completed process.
    """
    env = {**os.environ, "NPM_CMD": str(npm_bin)}
    return subprocess.run(  # nosec B603 - fixed in-repo script
        [
            sys.executable,
            str(_SCRIPT),
            "--packages-dir",
            str(packages_dir),
            "--output",
            str(output),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_exits_zero() -> None:
    """``--help`` works (scripts/README coverage relies on it)."""
    result = subprocess.run(  # nosec B603 - fixed in-repo script
        [sys.executable, str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--packages-dir")


def test_manifest_lists_every_packed_file_with_its_digest(
    packages: Path,
    tmp_path: Path,
) -> None:
    """One sorted ``<sha256>  <package>/<file>`` line per packed file."""
    npm_bin = _install_fake_npm(tmp_path / "bin")
    output = packages / "SHA256SUMS"
    result = _run(packages, output, npm_bin=npm_bin)
    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)

    lines = output.read_text(encoding="utf-8").splitlines()
    expected = sorted(
        f"{_sha256(packages / rel)}  {rel}"
        for rel in (
            "lintro/package.json",
            "lintro/bin/lintro",
            "lintro/lib/resolve.js",
            "linux-x64/package.json",
            "linux-x64/bin/lintro",
            "linux-x64/index.js",
        )
    )
    assert_that(lines).is_equal_to(
        sorted(expected, key=lambda ln: ln.split("  ", 1)[1]),
    )
    # sha256sum format: two spaces, no leading "./", relative to packages-dir.
    for line in lines:
        assert_that(line).matches(r"^[0-9a-f]{64}  [a-z0-9-]+/")
    assert_that(result.stdout).contains("Wrote 6 checksums")


def test_manifest_covers_the_real_npm_tree_shape(tmp_path: Path) -> None:
    """Every in-repo package is enumerated (the stray dir is not)."""
    module = _load_module()
    root = tmp_path / "npm"
    root.mkdir()
    _make_package(root, "a", {"index.js": b"a"})
    _make_package(root, "b", {"index.js": b"b"})
    (root / "zz-no-manifest").mkdir()
    assert_that([p.name for p in module.package_dirs(root)]).is_equal_to(["a", "b"])


def test_file_npm_would_pack_but_is_missing_fails(
    packages: Path,
    tmp_path: Path,
) -> None:
    """A listed-but-absent file cannot be hashed: fail closed."""
    npm_bin = _install_fake_npm(tmp_path / "bin")
    (packages / "linux-x64" / "bin" / "lintro").unlink()
    result = _run(packages, packages / "SHA256SUMS", npm_bin=npm_bin)
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("linux-x64/bin/lintro but it does not exist")
    assert_that((packages / "SHA256SUMS").exists()).is_false()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail", "npm pack --dry-run failed"),
        ("garbage", "could not parse the npm pack file list"),
        ("empty", "reported no files"),
    ],
)
def test_npm_failures_fail_closed(
    packages: Path,
    tmp_path: Path,
    mode: str,
    message: str,
) -> None:
    """An unknown file set cannot be verified, so npm trouble is fatal.

    Args:
        packages: The staged packages directory.
        tmp_path: pytest temporary directory.
        mode: Stub npm behaviour.
        message: Expected stderr fragment.
    """
    npm_bin = _install_fake_npm(tmp_path / "bin", mode=mode)
    result = _run(packages, packages / "SHA256SUMS", npm_bin=npm_bin)
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(message)


def test_no_packages_is_an_error(tmp_path: Path) -> None:
    """An empty packages directory would produce an empty manifest: refuse."""
    npm_bin = _install_fake_npm(tmp_path / "bin")
    empty = tmp_path / "npm"
    empty.mkdir()
    result = _run(empty, tmp_path / "SHA256SUMS", npm_bin=npm_bin)
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("no package directories")


def test_real_npm_pack_agrees_with_the_manifest_fields() -> None:
    """With the real npm, the in-repo packages pack only what ``files`` lists.

    The reusable fails on any packed file the manifest does not list, so the
    file set this script asks npm for must be the set ``npm publish`` ships:
    the ``files`` allowlist in each package.json, plus package.json itself.
    """
    module = _load_module()
    npm = "npm"
    try:
        subprocess.run(  # nosec B603 B607 - probing for npm on PATH
            [npm, "--version"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("npm is not available")
    npm_dir = _REPO_ROOT / "npm"
    for package in module.package_dirs(npm_dir):
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        # Placeholder binaries are .gitkeep'd; npm packs only files that exist.
        expected = {
            "package.json",
            *(f for f in manifest["files"] if (package / f).is_file()),
        }
        packed = set(module.packed_files(package, npm=npm))
        assert_that(packed).described_as(package.name).is_equal_to(expected)
