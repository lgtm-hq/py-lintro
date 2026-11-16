"""npx cache contents checked against lockfile integrity, bound to the run (#3406).

Entries are real npm layouts built by :mod:`tests.npx_entries`: gzipped
tarballs whose sha512 is computed here, stored in npm's content cache, and
installed beside a lockfile that records it.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.utils.npx_call import npx_shell_safe_command
from lintro.utils.npx_integrity import (
    NpxIntegrityError,
    entry_fingerprint,
    verify_entry,
)
from tests.npx_entries import (
    LockedPackage,
    package_tarball,
    tool_package,
    write_npx_entry,
)

_SPEC = "prettier@3.9.4"

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="npm writes .bin links, not shims, and ctime is change time on POSIX",
)

#: A dependency and a nested copy of another, as npm installs a real tool.
_DEPENDENCIES = (
    LockedPackage(
        key="node_modules/dep",
        files={"package.json": '{"name":"dep"}', "index.js": "module.exports=2;\n"},
    ),
    LockedPackage(
        key="node_modules/dep/node_modules/inner",
        files={"package.json": '{"name":"inner"}', "index.js": "module.exports=3;\n"},
    ),
)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Per-pin npm cache, resolved, with nothing in it yet.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        The cache directory.
    """
    return Path(os.path.realpath(tmp_path)) / "npx" / _SPEC


def _install(cache: Path, name: str = "0123456789abcdef") -> Path:
    """Install the tool and its dependencies as one npm-shaped entry.

    Args:
        cache: Per-pin npm cache.
        name: Entry (hash) directory name.

    Returns:
        The entry.
    """
    return write_npx_entry(cache, [tool_package(), *_DEPENDENCIES], name=name)


def _command(cache: Path) -> list[str]:
    """Return lintro's npx fallback shape for *cache*.

    Args:
        cache: Per-pin npm cache.

    Returns:
        The npx command.
    """
    return ["npx", "--yes", f"--cache={cache}", "--package", _SPEC, "prettier"]


def _replace_file(path: Path, text: str) -> None:
    """Replace a file by a rename, as an attacker swapping it would.

    Args:
        path: File to replace.
        text: New contents.
    """
    swap = path.with_name(f"{path.name}.swap")
    swap.write_text(text, encoding="utf-8")
    os.replace(swap, path)


def _package(entry: Path) -> Path:
    return entry / "node_modules" / "prettier"


def _hard_link_outside(entry: Path, cache: Path) -> None:
    """Make ``lib/index.js`` a hard link to an identical file elsewhere.

    Args:
        entry: The entry.
        cache: Per-pin npm cache.
    """
    target = _package(entry) / "lib" / "index.js"
    outside = cache.parent / "outside.js"
    shutil.copyfile(target, outside)
    target.unlink()
    os.link(outside, target)


def _plant_node(entry: Path, cache: Path) -> None:
    node = cache.parent / "node"
    node.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
    (entry / "node_modules" / ".bin" / "node").symlink_to(node)


def _relink_bin(entry: Path, cache: Path) -> None:
    link = entry / "node_modules" / ".bin" / "prettier"
    link.unlink()
    link.symlink_to("../dep/index.js")


def _replace_cached_tarball(entry: Path, cache: Path) -> None:
    tarballs = list((cache / "_cacache").rglob("*"))
    for tarball in (path for path in tarballs if path.is_file()):
        tarball.write_bytes(package_tarball({"package.json": "{}"}))


def _remove_cached_tarballs(entry: Path, cache: Path) -> None:
    shutil.rmtree(cache / "_cacache")


_TAMPERING: dict[str, tuple[Callable[[Path, Path], object], str]] = {
    "replaced-bin-same-manifest": (
        lambda entry, _cache: _replace_file(
            _package(entry) / "bin.js",
            "console.log('[]');\n",
        ),
        "bin.js changed from the published package",
    ),
    "hard-linked-file": (
        _hard_link_outside,
        "lib/index.js installed as something other than its own file",
    ),
    "removed-file": (
        lambda entry, _cache: (_package(entry) / "lib" / "index.js").unlink(),
        "is missing lib/index.js",
    ),
    "file-not-published": (
        lambda entry, _cache: (_package(entry) / "lib" / "index.mjs").write_text(
            "export default 1;\n",
            encoding="utf-8",
        ),
        "has lib/index.mjs, which is not in the published package",
    ),
    "shadowing-nested-package": (
        lambda entry, _cache: (_package(entry) / "node_modules" / "dep").mkdir(
            parents=True,
        ),
        "has node_modules/prettier/node_modules/dep, not in its lockfile",
    ),
    "unlocked-top-level-package": (
        lambda entry, _cache: (entry / "node_modules" / "evil").mkdir(),
        "has node_modules/evil, not in its lockfile",
    ),
    "planted-bin-node": (
        _plant_node,
        "has .bin/node, which no locked package declares",
    ),
    "bin-relinked": (
        _relink_bin,
        "has .bin/prettier pointing outside the package declaring it",
    ),
    "tarball-gone": (
        _remove_cached_tarballs,
        "has no tarball in lintro's npm cache",
    ),
    "tarball-replaced": (
        _replace_cached_tarball,
        "does not match its lockfile integrity",
    ),
    "lockfile-gone": (
        lambda entry, _cache: (entry / "package-lock.json").unlink(),
        "has no readable package-lock.json",
    ),
}


@posix_only
def test_npm_layout_matches_its_lockfile(cache: Path) -> None:
    """A tool, a dependency and a nested dependency, as npm installs them, pass.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)

    assert_that(verify_entry(entry, cache)).is_equal_to(entry_fingerprint(entry))


@posix_only
@pytest.mark.parametrize("case", list(_TAMPERING), ids=list(_TAMPERING))
def test_entry_that_differs_from_the_published_package_is_refused(
    cache: Path,
    case: str,
) -> None:
    """Every way the installed files can stop being the published ones is caught.

    Args:
        cache: Per-pin npm cache.
        case: Which tampering to apply.
    """
    entry = _install(cache)
    tamper, reason = _TAMPERING[case]
    tamper(entry, cache)

    assert_that(verify_entry(entry, cache)).is_instance_of(str).contains(reason)


@posix_only
@pytest.mark.parametrize(
    ("extra", "expected"),
    [({"optional": True}, None), ({}, "is missing node_modules/@x/linux")],
    ids=["optional-skipped", "required-missing"],
)
def test_only_an_optional_package_may_be_absent(
    cache: Path,
    extra: dict[str, object],
    expected: str | None,
) -> None:
    """Npm skips platform-specific optional packages; nothing else may be missing.

    Args:
        cache: Per-pin npm cache.
        extra: Lockfile fields of the absent package.
        expected: Reason expected, or None for a pass.
    """
    absent = LockedPackage(
        key="node_modules/@x/linux",
        files={"package.json": "{}"},
        extra=extra,
        installed=False,
    )
    entry = write_npx_entry(cache, [tool_package(), absent])

    result = verify_entry(entry, cache)

    if expected is None:
        assert_that(result).is_instance_of(tuple)
    else:
        assert_that(result).is_instance_of(str).contains(expected)


@posix_only
@pytest.mark.parametrize(
    ("packed", "installed"),
    [
        ({".gitignore": "dist\n"}, {".npmignore": "dist\n"}),
        ({".npmignore": "a\n", ".gitignore": "b\n"}, {".npmignore": "a\n"}),
    ],
    ids=["gitignore-renamed", "npmignore-wins"],
)
def test_packed_gitignore_is_expected_where_npm_installs_it(
    cache: Path,
    packed: dict[str, str],
    installed: dict[str, str],
) -> None:
    """Pacote installs ``.gitignore`` as ``.npmignore`` unless one came first.

    Seen with ``node-sarif-builder`` under spectral's pin in CI.

    Args:
        cache: Per-pin npm cache.
        packed: Ignore files in the tarball, in tarball order.
        installed: What npm leaves in the package directory.
    """
    package = LockedPackage(
        key="node_modules/ignored",
        files={"package.json": '{"name":"ignored"}', **packed},
    )
    entry = write_npx_entry(cache, [tool_package(), package])
    directory = entry / "node_modules" / "ignored"
    for name in packed:
        (directory / name).unlink()
    for name, text in installed.items():
        (directory / name).write_text(text, encoding="utf-8")

    assert_that(verify_entry(entry, cache)).is_instance_of(tuple)


@posix_only
def test_install_script_output_is_allowed_only_where_declared(cache: Path) -> None:
    """A package npm ran an install script for may hold files it generated.

    Args:
        cache: Per-pin npm cache.
    """
    built = LockedPackage(
        key="node_modules/native",
        files={"package.json": '{"name":"native"}'},
        extra={"hasInstallScript": True},
    )
    entry = write_npx_entry(cache, [tool_package(), built])
    (entry / "node_modules" / "native" / "build.node").write_bytes(b"\0")

    assert_that(verify_entry(entry, cache)).is_instance_of(tuple)


@posix_only
def test_lockfile_keys_through_a_symlinked_cache_path_are_understood(
    tmp_path: Path,
) -> None:
    """Npm keys packages from the real path when its cache path is a symlink.

    Reproduced with npx 11.20.0 and ``HOME`` under macOS's ``/tmp``: every key
    reads ``../../…/private/tmp/…/_npx/<hash>/node_modules/<name>``.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    real = Path(os.path.realpath(tmp_path)) / "real" / _SPEC
    link = tmp_path / "link"
    link.symlink_to(real.parent, target_is_directory=True)
    linked_cache = link / _SPEC
    name = "0123456789abcdef"
    climb = os.path.relpath(real / "_npx" / name, linked_cache / "_npx" / name)
    entry = write_npx_entry(
        real,
        [tool_package(), *_DEPENDENCIES],
        name=name,
        lock_prefix=f"{Path(climb).as_posix()}/",
    )

    assert_that(verify_entry(entry, real, npm_cache=linked_cache)).is_instance_of(
        tuple,
    )


@posix_only
def test_lockfile_key_outside_the_entry_is_refused(cache: Path) -> None:
    """A key cannot point npm's tree, and lintro's check, outside the entry.

    Args:
        cache: Per-pin npm cache.
    """
    entry = write_npx_entry(
        cache,
        [tool_package()],
        lock_prefix="node_modules/../../",
    )

    assert_that(verify_entry(entry, cache)).is_instance_of(str).contains(
        "which is not an installed registry package",
    )


@posix_only
def test_a_verified_entry_is_rehashed_only_after_it_changes(cache: Path) -> None:
    """Warm runs reuse a verification, but any write forces a new one.

    The tarballs are removed after the first check: the unchanged entry still
    passes without them, and rewriting one file with its own bytes (a new
    ctime) makes lintro look for them again.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    verify_entry(entry, cache)
    shutil.rmtree(cache / "_cacache")

    warm = verify_entry(entry, cache)
    bin_js = _package(entry) / "bin.js"
    _replace_file(bin_js, bin_js.read_text(encoding="utf-8"))
    rewritten = verify_entry(entry, cache)

    assert_that(warm).is_instance_of(tuple)
    assert_that(rewritten).is_instance_of(str).contains("has no tarball")


# ---------------------------------------------------------------------------
# Bound to the spawn: npx_shell_safe_command
# ---------------------------------------------------------------------------


@posix_only
def test_tampered_entry_is_refused_before_anything_spawns(cache: Path) -> None:
    """An entry changed after the gate never reaches the spawn.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    _replace_file(_package(entry) / "bin.js", "console.log('[]');\n")
    spawned: list[list[str]] = []

    with (
        pytest.raises(NpxIntegrityError, match="bin.js changed"),
        npx_shell_safe_command(_command(cache)) as argv,
    ):
        spawned.append(argv)

    assert_that(spawned).is_empty()


def _swap_in_identical_bin(cache: Path) -> None:
    entry = cache / "_npx" / "0123456789abcdef"
    bin_js = _package(entry) / "bin.js"
    _replace_file(bin_js, bin_js.read_text(encoding="utf-8"))


def _npm_lock_churn(cache: Path) -> None:
    entry = cache / "_npx" / "0123456789abcdef"
    (entry / "concurrency.lock").mkdir()
    (entry / "concurrency.lock").rename(entry / ".lintro-stale-concurrency.lock-1")


def _cold_install(cache: Path) -> None:
    _install(cache, name="fedcba9876543210")


def _cold_install_tampered(cache: Path) -> None:
    entry = _install(cache, name="fedcba9876543210")
    (_package(entry) / "bin.js").write_text("console.log('[]');\n", encoding="utf-8")


_DURING_RUN: dict[str, tuple[Callable[[Path], None], str | None]] = {
    "replaced-and-put-back": (
        _swap_in_identical_bin,
        "changed while the tool was running",
    ),
    "npm-lock-taken-and-released": (_npm_lock_churn, None),
    "cold-install-verified-after": (_cold_install, None),
    "cold-install-tampered": (_cold_install_tampered, "bin.js changed"),
}


@posix_only
@pytest.mark.parametrize("case", list(_DURING_RUN), ids=list(_DURING_RUN))
def test_cache_must_hold_what_was_verified_when_the_run_ends(
    cache: Path,
    case: str,
) -> None:
    """A change during the run fails it; npm's lock and a clean install do not.

    Args:
        cache: Per-pin npm cache.
        case: What happens to the cache while the tool runs.
    """
    _install(cache)
    during, expected = _DURING_RUN[case]
    error: NpxIntegrityError | None = None

    try:
        with npx_shell_safe_command(_command(cache)):
            during(cache)
    except NpxIntegrityError as exc:
        error = exc

    if expected is None:
        assert_that(error).is_none()
    else:
        assert_that(str(error)).contains(expected).contains("next lintro run")


def _fake_npx(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "npx"
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


@posix_only
@pytest.mark.parametrize("executor", [run_subprocess, run_subprocess_streaming])
def test_executors_never_start_npx_on_a_tampered_cache(
    cache: Path,
    tmp_path: Path,
    executor: Callable[..., SubprocessResult],
) -> None:
    """The real executors refuse before npx starts, so nothing runs at all.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: lintro subprocess entry point.
    """
    entry = _install(cache)
    (_package(entry) / "bin.js").write_text("console.log('[]');\n", encoding="utf-8")
    ran = tmp_path / "ran"
    npx = _fake_npx(tmp_path / "bin", f"touch '{ran}'\necho '[]'\n")

    with pytest.raises(NpxIntegrityError):
        executor([str(npx), *_command(cache)[1:]], timeout=30)

    assert_that(ran.exists()).is_false()


@posix_only
@pytest.mark.parametrize("executor", [run_subprocess, run_subprocess_streaming])
def test_executors_fail_a_run_that_replaced_the_tool(
    cache: Path,
    tmp_path: Path,
    executor: Callable[..., SubprocessResult],
) -> None:
    """A clean-looking report from a tool swapped mid-run is not returned.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        executor: lintro subprocess entry point.
    """
    entry = _install(cache)
    bin_js = _package(entry) / "bin.js"
    npx = _fake_npx(
        tmp_path / "bin",
        f"printf 'swapped' > '{bin_js}.swap' && mv '{bin_js}.swap' '{bin_js}'\n"
        "echo '[]'\n",
    )

    with pytest.raises(NpxIntegrityError, match="changed while the tool was running"):
        executor([str(npx), *_command(cache)[1:]], timeout=30)
