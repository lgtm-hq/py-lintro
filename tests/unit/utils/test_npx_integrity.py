"""npx cache contents checked against lockfile integrity, bound to the run (#3406).

Entries are real npm layouts built by :mod:`tests.npx_entries`: gzipped
tarballs whose sha512 is computed here, stored in npm's content cache, and
installed beside a lockfile that records it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.version_parsing import _run_probe_with_retry
from lintro.utils import npx_integrity
from lintro.utils.execution.result_shaping import mark_npx_refusals
from lintro.utils.npx_call import npx_shell_safe_command
from lintro.utils.npx_integrity import (
    NpxIntegrityError,
    entry_fingerprint,
    entry_problem,
    npm_cmd_shims,
    npm_fixed_bin,
    verified_cache,
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
    LockedPackage(
        key="node_modules/@scope/scoped",
        files={"package.json": '{"name":"@scope/scoped"}', "index.js": "1;\n"},
    ),
)

#: npm's own cmd-shim and bin-links output, captured from npm 11.20.0.
_GOLDEN = json.loads(
    (Path(__file__).parent / "npm_bin_links_golden.json").read_text(encoding="utf-8"),
)


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real ``~/.node_modules`` out of the ancestor check.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped ``HOME`` override.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


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


def _link_back_from_outside(path: Path, cache: Path) -> None:
    """Move *path* outside the entry and leave a symlink to it in its place.

    Args:
        path: Directory inside the entry.
        cache: Per-pin npm cache; the copy goes beside it.
    """
    outside = cache.parent / f"outside-{path.name}"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)


def _plant_linked_modules(entry: Path, cache: Path) -> None:
    """Give the tool a ``node_modules`` link to a planted package (Fable's B).

    Args:
        entry: The entry.
        cache: Per-pin npm cache.
    """
    planted = cache.parent / "planted" / "glob"
    planted.mkdir(parents=True)
    (planted / "index.js").write_text("console.log('PLANTED');\n", encoding="utf-8")
    (_package(entry) / "node_modules").symlink_to(planted.parent)


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
    "node-modules-linked-from-outside": (
        lambda entry, cache: _link_back_from_outside(entry / "node_modules", cache),
        "through a symlink",
    ),
    "nested-node-modules-linked-from-outside": (
        lambda entry, cache: _link_back_from_outside(
            entry / "node_modules" / "dep" / "node_modules",
            cache,
        ),
        "reaches node_modules/dep/node_modules/inner through a symlink",
    ),
    "scope-linked-from-outside": (
        lambda entry, cache: _link_back_from_outside(
            entry / "node_modules" / "@scope",
            cache,
        ),
        "reaches node_modules/@scope/scoped through a symlink",
    ),
    "unlocked-node-modules-link": (
        _plant_linked_modules,
        "has node_modules, which is not in the published package",
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
def test_install_script_output_is_refused(cache: Path) -> None:
    """A file an install script could have written is not trusted to run.

    lintro's npx fallback runs with ``--ignore-scripts``, so no install holds
    one; a ``generated.js`` the published entry could require is refused.

    Args:
        cache: Per-pin npm cache.
    """
    built = LockedPackage(
        key="node_modules/native",
        files={"package.json": '{"name":"native"}', "index.js": "require('./g');\n"},
        extra={"hasInstallScript": True},
    )
    entry = write_npx_entry(cache, [tool_package(), built])
    (entry / "node_modules" / "native" / "generated.js").write_text(
        "console.log('[]');\n",
        encoding="utf-8",
    )

    assert_that(verify_entry(entry, cache)).is_instance_of(str).contains(
        "node_modules/native has generated.js, which is not in the published package",
    )


@posix_only
@pytest.mark.parametrize(
    ("planted", "expected"),
    [(False, None), (True, "which node_modules/host does not publish")],
    ids=["as-published", "file-planted"],
)
def test_bundled_package_holds_only_what_its_bundler_publishes(
    cache: Path,
    planted: bool,
    expected: str | None,
) -> None:
    """A bundled package has no tarball of its own; its bundler's is used.

    Args:
        cache: Per-pin npm cache.
        planted: Whether a file is added to the bundled package.
        expected: Reason expected, or None for a pass.
    """
    inner = "module.exports = 'bundled';\n"
    host = LockedPackage(
        key="node_modules/host",
        files={
            "package.json": '{"name":"host"}',
            "node_modules/bundled/index.js": inner,
        },
    )
    bundled = LockedPackage(
        key="node_modules/host/node_modules/bundled",
        files={"index.js": inner},
        extra={"inBundle": True},
    )
    entry = write_npx_entry(cache, [tool_package(), host, bundled])
    if planted:
        (entry / bundled.key / "index.mjs").write_text("1;\n", encoding="utf-8")

    result = verify_entry(entry, cache)

    if expected is None:
        assert_that(result).is_instance_of(tuple)
    else:
        assert_that(result).is_instance_of(str).contains(expected)


@pytest.mark.parametrize("case", ["crlf", "lf", "noshebang"])
def test_fix_bin_port_matches_npm(case: str) -> None:
    """Only a CRLF on the ``#!`` line is rewritten, as bin-links does.

    Args:
        case: Golden captured from npm's own ``fix-bin``.
    """
    golden = _GOLDEN["fix_bin"][case]

    fixed = npm_fixed_bin(golden["published"].encode("utf-8"))

    assert_that(fixed).is_equal_to(golden["installed"].encode("utf-8"))


@pytest.mark.parametrize("generator", ["8", "9"], ids=["npm-10-11", "npm-12"])
@pytest.mark.parametrize(
    "case",
    ["env-node", "env-split-vars", "absolute-sh", "no-shebang"],
)
def test_cmd_shim_port_matches_npm(case: str, generator: str) -> None:
    """The Windows shims lintro expects are byte for byte npm's own.

    Args:
        case: Golden captured from npm's own ``cmd-shim``.
        generator: ``cmd-shim`` major version the golden came from.
    """
    golden = _GOLDEN["cmd_shim"][generator][case]

    shims = npm_cmd_shims("../tool/bin/cli.js", golden["target"], generator=generator)

    assert_that(shims).is_equal_to(
        {suffix: golden[suffix] for suffix in (".cmd", ".ps1", "")},
    )


@posix_only
@pytest.mark.parametrize(
    ("published_as", "expected"),
    [("bin.js", None), ("lib/index.js", "lib/index.js changed")],
    ids=["declared-bin", "other-file"],
)
def test_crlf_shebang_is_expected_only_on_declared_executables(
    cache: Path,
    published_as: str,
    expected: str | None,
) -> None:
    """Npm strips the ``#!`` line's CRLF on install, for executables only.

    Args:
        cache: Per-pin npm cache.
        published_as: Which file is published with a CRLF shebang.
        expected: Reason expected, or None for a pass.
    """
    package = tool_package()
    crlf = "#!/usr/bin/env node\r\nconsole.log(1)\r\n"
    tool = LockedPackage(
        key=package.key,
        files={**package.files, published_as: crlf},
        bins=package.bins,
    )
    entry = write_npx_entry(cache, [tool])
    (_package(entry) / published_as).write_bytes(npm_fixed_bin(crlf.encode()))

    result = verify_entry(entry, cache)

    if expected is None:
        assert_that(result).is_instance_of(tuple)
    else:
        assert_that(result).is_instance_of(str).contains(expected)


def _windows_shims(entry: Path, generator: str = "8") -> Path:
    """Replace the tool's ``.bin`` link with npm's Windows shims.

    Args:
        entry: The entry.
        generator: ``cmd-shim`` major version that writes them.

    Returns:
        The ``.bin`` directory.
    """
    bin_dir = entry / "node_modules" / ".bin"
    (bin_dir / "prettier").unlink()
    text = (_package(entry) / "bin.js").read_text(encoding="utf-8")
    shims = npm_cmd_shims("../prettier/bin.js", text, generator=generator)
    for suffix, shim in shims.items():
        (bin_dir / f"prettier{suffix}").write_bytes(shim.encode("utf-8"))
    return bin_dir


#: Shims in a format no known generator writes are refused as unrecognised,
#: which a reinstall would not fix, so the gate keeps them for the spawn to
#: refuse.
_UNRECOGNISED = "in a shim format lintro does not recognise"

_SHIM_TAMPERING: dict[str, tuple[Callable[[Path], object], str | None]] = {
    "as-npm-writes-them": (lambda _bin_dir: None, None),
    "cmd-replaced": (
        lambda bin_dir: (bin_dir / "prettier.cmd").write_text("@echo []\r\n"),
        f"has .bin/prettier.cmd {_UNRECOGNISED}",
    ),
    "sh-shim-replaced": (
        lambda bin_dir: (bin_dir / "prettier").write_text("#!/bin/sh\necho []\n"),
        f"has .bin/prettier {_UNRECOGNISED}",
    ),
    "undeclared-node-cmd": (
        lambda bin_dir: (bin_dir / "node.cmd").write_text("@echo []\r\n"),
        "has .bin/node.cmd, which no locked package declares",
    ),
}


@pytest.mark.parametrize("generator", ["8", "9"], ids=["npm-10-11", "npm-12"])
@pytest.mark.parametrize("case", list(_SHIM_TAMPERING), ids=list(_SHIM_TAMPERING))
def test_windows_shims_must_be_the_ones_npm_writes(
    cache: Path,
    case: str,
    generator: str,
) -> None:
    """On Windows, ``.bin`` holds shims, which are checked byte for byte.

    Args:
        cache: Per-pin npm cache.
        case: What happens to the shims.
        generator: ``cmd-shim`` major version that wrote them.
    """
    entry = write_npx_entry(cache, [tool_package()])
    tamper, expected = _SHIM_TAMPERING[case]
    tamper(_windows_shims(entry, generator))

    reason = entry_problem(entry, cache, windows=True)

    if expected is None:
        assert_that(reason).is_none()
    else:
        assert_that(reason).contains(expected)
        assert_that(npx_integrity.is_repairable(reason or "")).is_equal_to(
            _UNRECOGNISED not in expected,
        )


#: Where a ``node_modules`` Node would search is planted, relative to the
#: test's directory: inside the per-pin cache, above lintro's cache, and the
#: ``$HOME`` global folder.
_ANCESTORS: dict[str, Callable[[Path, Path], Path]] = {
    "in-the-cache": lambda cache, _tmp: cache / "node_modules",
    "above-the-cache": lambda cache, _tmp: cache.parent.parent / "node_modules",
    "home-global-folder": lambda _cache, tmp: tmp / "home" / ".node_modules",
}


@posix_only
@pytest.mark.parametrize("location", list(_ANCESTORS), ids=list(_ANCESTORS))
def test_node_modules_node_would_search_is_refused_with_its_path(
    cache: Path,
    tmp_path: Path,
    location: str,
) -> None:
    """Every entry is refused, whether or not npm skipped an optional package.

    markdownlint-cli2 skips none, yet Node in its entry resolves an
    undeclared ``require("ghost")`` from ``~/node_modules/ghost``.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
        location: Where the ``node_modules`` is planted.
    """
    write_npx_entry(cache, [tool_package()])
    planted = _ANCESTORS[location](cache, tmp_path)
    (planted / "ghost").mkdir(parents=True)

    with pytest.raises(NpxIntegrityError) as refusal:
        verified_cache(cache)

    assert_that(str(refusal.value)).contains(
        f": {planted}.",
        "Remove or rename that directory",
        "lintro did not run the tool",
    ).does_not_contain("reinstalls")


@posix_only
def test_an_install_in_progress_is_awaited_not_refused(cache: Path) -> None:
    """A concurrent npx still installing holds npm's lock; lintro waits for it.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    lockfile = entry / "package-lock.json"
    pending = entry / "node_modules" / ".pending-lock.json"
    lockfile.rename(pending)
    (entry / "concurrency.lock").mkdir()

    def npm_finishes() -> None:
        time.sleep(0.3)
        (cache / "_logs").mkdir()  # npm writes beside _npx too.
        pending.rename(lockfile)
        (entry / "concurrency.lock").rmdir()

    installer = threading.Thread(target=npm_finishes)
    installer.start()
    try:
        state = verified_cache(cache)
    finally:
        installer.join()

    assert_that(list(state.entries)).is_equal_to([entry.name])


@posix_only
def test_a_held_lock_is_waited_for_once_within_the_tool_timeout(
    cache: Path,
) -> None:
    """A lock still held when the tool's timeout runs out fails the run, once.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    (entry / "concurrency.lock").mkdir()
    warnings: list[str] = []
    sink = logger.add(
        warnings.append,
        level="WARNING",
        format="{message}",
    )
    started = time.monotonic()
    try:
        with pytest.raises(NpxIntegrityError) as refusal:
            verified_cache(cache, timeout=0.5)
    finally:
        logger.remove(sink)
    waited = time.monotonic() - started

    assert_that(waited).is_less_than(1.5)
    assert_that(str(refusal.value)).contains(
        "stayed held",
        "lintro did not run the tool",
        f"delete {entry / 'concurrency.lock'} once none is running",
    )
    assert_that(warnings).is_length(1)
    assert_that(warnings[0]).contains("Waiting up to 0s", "another npm or npx")


@posix_only
def test_a_lock_touched_in_the_future_is_treated_as_stale(cache: Path) -> None:
    """A skewed clock cannot keep a lock fresh for ever.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    lock = entry / "concurrency.lock"
    lock.mkdir()
    future = time.time() + 3600
    os.utime(lock, (future, future))
    started = time.monotonic()

    state = verified_cache(cache, timeout=30)

    assert_that(time.monotonic() - started).is_less_than(5)
    assert_that(list(state.entries)).is_equal_to([entry.name])


@posix_only
@pytest.mark.parametrize("fix", [False, True], ids=["check", "fix"])
def test_a_refusal_the_tool_caught_still_fails_the_run(cache: Path, fix: bool) -> None:
    """A tool's own handler cannot turn a refusal into a plain failure.

    prettier, oxlint and oxfmt run through the batch runner, which catches
    the error and returns an ordinary failed result. The executor marks it
    from the recorded refusal, keeping the findings that really ran; in a
    fix run what remains is unmeasured.

    Args:
        cache: Per-pin npm cache.
        fix: Whether the refused run was a fix.
    """
    entry = _install(cache)
    _replace_file(_package(entry) / "bin.js", "console.log('[]');\n")
    with pytest.raises(NpxIntegrityError):
        verified_cache(cache)
    tool = SimpleNamespace(
        definition=SimpleNamespace(name="prettier"),
        _get_executable_command=lambda _name, cwd: _command(cache),
    )
    issue = BaseIssue(file="a.js", line=1, message="real finding")
    caught = ToolResult(
        name="prettier",
        success=False,
        output="Prettier execution failed",
        issues_count=1,
        issues=[issue],
        cwd=str(cache),
    )

    marked = mark_npx_refusals(tool, caught, fix=fix)  # type: ignore[arg-type]
    again = mark_npx_refusals(tool, caught, fix=fix)  # type: ignore[arg-type]

    assert_that(marked.execution_failed).is_true()
    assert_that(marked.findings_complete).is_false()
    assert_that(marked.issues).is_equal_to([issue])
    assert_that(marked.output).contains("bin.js changed", "did not run it")
    assert_that(again.execution_failed).is_false()
    assert_that(marked.residual_unknown).is_equal_to(fix)


@posix_only
def test_a_refusal_is_never_left_for_a_later_run(cache: Path) -> None:
    """A passing result still takes its cache's refusal, and is failed by it.

    Args:
        cache: Per-pin npm cache.
    """
    entry = _install(cache)
    _replace_file(_package(entry) / "bin.js", "console.log('[]');\n")
    with pytest.raises(NpxIntegrityError):
        verified_cache(cache)
    tool = SimpleNamespace(
        definition=SimpleNamespace(name="prettier"),
        _get_executable_command=lambda _name, cwd: _command(cache),
    )
    passing = ToolResult(name="prettier", success=True, output="", issues_count=0)

    marked = mark_npx_refusals(tool, passing)  # type: ignore[arg-type]
    later = mark_npx_refusals(tool, passing)  # type: ignore[arg-type]

    assert_that(marked.execution_failed).is_true()
    assert_that(marked.success).is_false()
    assert_that(later.execution_failed).is_false()


@posix_only
def test_no_home_directory_skips_only_the_home_folders(
    cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a home directory the check still runs, as a refusal or a pass.

    Args:
        cache: Per-pin npm cache.
        monkeypatch: Makes ``Path.home`` fail as it does with no ``HOME`` and
            no passwd entry.
    """
    _install(cache)
    (cache / "node_modules").mkdir()

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))

    with pytest.raises(NpxIntegrityError, match="Remove or rename"):
        verified_cache(cache)


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
    _install(cache)


def _cold_install_tampered(cache: Path) -> None:
    entry = _install(cache)
    (_package(entry) / "bin.js").write_text("console.log('[]');\n", encoding="utf-8")


def _swap_aside_and_back(directory: Path) -> None:
    """Run from a tampered copy in *directory*'s place, then put it back.

    Fable's rename-aside reproduction: the original keeps its inode and
    contents, so only the moves themselves are left to see.

    Args:
        directory: The entry or ``_npx``.
    """
    aside = directory.with_name(f"{directory.name}-aside")
    directory.rename(aside)
    shutil.copytree(aside, directory, symlinks=True)
    for bin_js in directory.rglob("prettier/bin.js"):
        bin_js.write_text("console.log('SWAPPED');\n", encoding="utf-8")
    shutil.rmtree(directory)
    aside.rename(directory)


#: What happens during the run: whether the pin was installed before the
#: spawn, the change, and the refusal expected (None for none).
_DURING_RUN: dict[str, tuple[bool, Callable[[Path], None], str | None]] = {
    "replaced-and-put-back": (
        True,
        _swap_in_identical_bin,
        "changed while the tool was running",
    ),
    "entry-swapped-aside-and-back": (
        True,
        lambda cache: _swap_aside_and_back(cache / "_npx" / "0123456789abcdef"),
        "(an entry was moved) changed while the tool was running",
    ),
    "npx-dir-swapped-aside-and-back": (
        True,
        lambda cache: _swap_aside_and_back(cache / "_npx"),
        "(an entry was moved) changed while the tool was running",
    ),
    "npm-lock-taken-and-released": (True, _npm_lock_churn, None),
    "cold-install-verified-after": (False, _cold_install, None),
    "cold-install-tampered": (False, _cold_install_tampered, "bin.js changed"),
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
    installed, during, expected = _DURING_RUN[case]
    if installed:
        _install(cache)
    error: NpxIntegrityError | None = None

    try:
        with npx_shell_safe_command(_command(cache)):
            during(cache)
    except NpxIntegrityError as exc:
        error = exc

    if expected is None:
        assert_that(error).is_none()
    else:
        assert_that(str(error)).contains(expected).contains("cannot be trusted")


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


@posix_only
def test_version_probe_never_starts_npx_on_a_tampered_cache(
    cache: Path,
    tmp_path: Path,
) -> None:
    """A version probe of an npx fallback goes through the same check.

    Args:
        cache: Per-pin npm cache.
        tmp_path: Temporary directory provided by pytest.
    """
    entry = _install(cache)
    (_package(entry) / "bin.js").write_text("console.log('9.9.9');\n", encoding="utf-8")
    ran = tmp_path / "ran"
    npx = _fake_npx(tmp_path / "bin", f"touch '{ran}'\necho 3.9.4\n")

    with pytest.raises(NpxIntegrityError, match="bin.js changed"):
        _run_probe_with_retry([str(npx), *_command(cache)[1:], "--version"], 30)

    assert_that(ran.exists()).is_false()
