"""Installer, version-source and image wiring tests for checkov."""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, controlled test input
from pathlib import Path

import pytest
from assertpy import assert_that
from packaging.version import Version

from lintro._tool_versions import get_min_version, get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.tools.core.install_hints import CHECKOV_ISOLATED_INSTALL_HINT
from lintro.tools.core.version_checking import get_install_hints
from tests.integration._tools import DEFAULT_TIMEOUT_SECONDS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_TOOL_VERSIONS = _REPO_ROOT / "lintro" / "_tool_versions.py"
_VERSION_CHECKING = _REPO_ROOT / "lintro" / "tools" / "core" / "version_checking.py"


def _production_version_timeout() -> float:
    """Read lintro's default version-probe budget from its source.

    ``VERSION_CHECK_TIMEOUT`` is resolved from ``LINTRO_VERSION_TIMEOUT`` at
    import time, so importing it would make this test track whatever CI
    exports. The default literal inside ``_get_version_timeout`` is the value
    the "never stricter than production" invariant is about, and reading it
    from source keeps the floor from going stale when it changes.

    Returns:
        The default timeout in seconds, falling back to 30.0 when the literal
        cannot be located.
    """
    source = _VERSION_CHECKING.read_text(encoding="utf-8")
    match = re.search(r"default_timeout = ([0-9.]+)", source)
    return float(match.group(1)) if match else 30.0


#: lintro's own default version-probe budget, from ``_get_version_timeout``.
_PRODUCTION_VERSION_TIMEOUT: float = _production_version_timeout()


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


def _checkov_install_block() -> str:
    """Return the installer's checkov block, failing if it cannot be located.

    ``str.find`` returns -1 for a missing marker, and an unguarded slice on
    that silently widens to the rest of the ~2000-line script — every
    ``contains`` below would then match text belonging to other tools' blocks
    and the ``exit 1`` count would clear its threshold trivially. Locating the
    block once, with both markers asserted, is what keeps these assertions
    honest.

    Returns:
        The text between ``if should_install "checkov"`` and ``fi # checkov``.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    start = script.find('if should_install "checkov"; then')
    assert_that(start).described_as("checkov install block").is_not_equal_to(-1)
    end = script.find("fi # checkov", start)
    assert_that(end).described_as("'fi # checkov' end marker").is_not_equal_to(-1)
    return script[start:end]


requires_modern_bash = pytest.mark.skipif(
    _BASH is None,
    reason="install-tools.sh requires bash >= 4 (associative arrays)",
)

#: The checkov block probes for ``uv`` before the dry-run branch and, because
#: ``--tools checkov`` names the tool explicitly, exits 1 when it is missing —
#: by design (see ``test_missing_uv_does_not_abort_a_full_local_install``). Without uv
#: the dry-run test would therefore fail rather than skip.
requires_uv = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="`install-tools.sh --tools checkov` exits 1 when uv is absent",
)


def test_pin_lives_outside_pyproject_and_any_requirements_file() -> None:
    """Checkov is never declared as a resolvable Python dependency.

    Two independent reasons, both load-bearing: checkov requires
    ``packaging<24`` while lintro requires ``packaging>=25``, so a pyproject
    entry makes ``uv lock`` unsatisfiable; and a requirements-file pin would
    publish checkov's transitive tree into this repository's own osv-scanner
    dogfood, turning the security gate red over dependencies lintro never
    installs. The pin therefore lives in ``TOOL_VERSIONS`` and checkov is
    installed into its own venv.
    """
    assert_that(_PYPROJECT.read_text(encoding="utf-8")).does_not_contain("checkov>")
    assert_that(_PYPROJECT.read_text(encoding="utf-8")).does_not_contain("checkov==")
    requirements = sorted(_REPO_ROOT.glob("requirements*.txt"))
    for path in requirements:
        assert_that(path.read_text(encoding="utf-8")).described_as(
            str(path),
        ).does_not_contain("checkov")


def test_pin_is_a_single_exact_literal_the_runtime_resolves() -> None:
    """One ``==`` pin exists and ``get_tool_version`` resolves to it.

    Deliberately narrow: this locks the shape of the source line the Renovate
    manager rewrites and the installer interpolates. The rendered manifest is
    generated from this same constant, so asserting it here would restate the
    generator rather than test it — ``test_manifest_declares_checkov_as_an_...``
    covers the source entry instead, and the manifest-vs-image gate (#1511)
    covers the rendered value against the real binary.
    """
    text = _TOOL_VERSIONS.read_text(encoding="utf-8")
    pins = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("ToolName.CHECKOV:")
    ]

    assert_that(pins).is_length(1)
    pinned = pins[0].split('"')[1]
    assert_that(get_tool_version("checkov")).is_equal_to(pinned)


def test_supported_floor_is_below_the_ci_pin() -> None:
    """``min_version`` is a real floor, not a copy of the CI pin.

    The image installs the exact ``TOOL_VERSIONS`` pin, but a developer machine
    may carry Homebrew's checkov, which lags upstream. Setting the floor to the
    pin would make every such install skip the tool instead of running it.
    """
    raw_floor = get_min_version(ToolName.CHECKOV)
    raw_pin = get_tool_version("checkov")
    assert_that(raw_floor).is_not_none()
    assert_that(raw_pin).is_not_none()
    floor = Version(str(raw_floor))
    pin = Version(str(raw_pin))

    assert_that(floor < pin).described_as(f"{floor} < {pin}").is_true()


def test_install_hint_never_targets_the_project_environment() -> None:
    """The install hint points at an isolated venv, not ``pip install``.

    A hint suggesting a plain pip install into lintro's environment would
    downgrade ``packaging`` and break lintro itself.
    """
    hint = get_install_hints()["checkov"]

    assert_that(hint).is_equal_to(CHECKOV_ISOLATED_INSTALL_HINT)
    assert_that(hint).contains("uv tool install checkov")
    assert_that(hint).does_not_contain("uv add")


def test_manifest_declares_checkov_as_an_external_binary_tool() -> None:
    """The manifest source describes the install path the docs promise.

    ``binary`` is the manifest's label for "an executable that lands on PATH
    via ``install-tools.sh``", not for the upstream packaging format — cppcheck
    carries it while arriving from apt. Declaring ``pip`` instead would route
    quick-fix at a ``uv pip install checkov`` into lintro's own environment,
    which downgrades ``packaging`` and breaks lintro.
    """
    manifest = json.loads(
        (_REPO_ROOT / "lintro" / "tools" / "manifest.src.json").read_text(
            encoding="utf-8",
        ),
    )
    entry = next(t for t in manifest["tools"] if t["name"] == "checkov")

    assert_that(entry["install"]).is_equal_to({"type": "binary"})
    assert_that(entry["category"]).is_equal_to("external")
    assert_that(entry).does_not_contain_key("version")
    assert_that(manifest["language_map"]["terraform"]).contains("checkov")
    assert_that(manifest["language_map"]["security"]).contains("checkov")


def test_renovate_tracks_the_pin_against_pypi() -> None:
    """A custom manager keeps the ``TOOL_VERSIONS`` pin current.

    checkov lives in ``TOOL_VERSIONS`` rather than a pypi seed, so nothing
    updates it automatically; without this manager the pin would silently go
    stale the way cppcheck's deliberately does.
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    managers = [
        manager
        for manager in config.get("customManagers") or []
        if manager.get("packageNameTemplate") == "checkov"
    ]

    assert_that(managers).is_length(1)
    assert_that(managers[0]["datasourceTemplate"]).is_equal_to("pypi")
    # One entry, asserted rather than assumed: Renovate treats multiple
    # matchStrings as alternatives, so joining them would compile to a pattern
    # that matches nothing and report a working manager as broken.
    match_strings = managers[0]["matchStrings"]
    assert_that(match_strings).is_length(1)
    pattern = match_strings[0]
    assert_that(pattern).contains(r"ToolName\.CHECKOV")
    # The regex must actually match the line it is aimed at; a manager whose
    # pattern silently matches nothing is indistinguishable from no manager.
    # Renovate uses JavaScript's named-group spelling; rewrite only the capture
    # form, never a lookbehind, which would compile to invalid Python.
    python_pattern = pattern.replace("(?<currentValue>", "(?P<currentValue>")
    assert_that(
        re.search(python_pattern, _TOOL_VERSIONS.read_text(encoding="utf-8")),
    ).is_not_none()


def test_renovate_never_automerges_the_checkov_pin() -> None:
    """The pin bump must ride a tools-image rebuild, like other binary pins.

    The manifest-vs-image gate compares the *installed* version to the manifest
    version, so a versions-only automerge would land a pin the pinned tools
    image cannot supply and redden main until the image is republished. A
    generic ``pypi`` patch-automerge rule would otherwise catch checkov.
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    blocking = [
        rule
        for rule in config.get("packageRules") or []
        if "checkov" in (rule.get("matchPackageNames") or [])
        and rule.get("automerge") is False
    ]

    assert_that(blocking).is_not_empty()


def test_supported_tools_lists_checkov() -> None:
    """``--tools checkov`` is accepted by the installer's validator.

    Scoped to the ``SUPPORTED_TOOLS`` array: a bare search of the whole script
    would match the install block and prove nothing about the validator.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    supported_at = script.find("SUPPORTED_TOOLS=(")
    assert_that(supported_at).is_not_equal_to(-1)
    supported = script[supported_at : script.index(")", supported_at)]

    assert_that(supported).contains('"checkov"')


def test_install_block_uses_an_isolated_venv_and_fails_loudly() -> None:
    """The install block installs the pin in isolation and never half-succeeds.

    ``uv pip install checkov`` into the active environment would downgrade
    ``packaging`` and break lintro, so the block must use ``uv tool install``.
    """
    block = _checkov_install_block()

    assert_that(block).contains('get_tool_version "checkov"')
    assert_that(block).contains("uv tool install")
    assert_that(block).does_not_contain("uv pip install")
    assert_that(block).contains("exit 1")


def test_docker_install_places_the_venv_where_non_root_can_read_it() -> None:
    """The ``uv tool`` venv must not land in root's home in Docker.

    ``uv tool install`` puts the shim in ``UV_TOOL_BIN_DIR`` but the interpreter
    in ``UV_TOOL_DIR``, which defaults to ``$HOME/.local/share/uv/tools``. The
    image builds as root and runs as ``lintro``, so the default would leave
    ``checkov`` on PATH pointing at an unreadable venv — a working build and a
    broken container.
    """
    block = _checkov_install_block()

    assert_that(block).contains("UV_TOOL_DIR=")
    assert_that(block).contains("/opt/uv-tools")
    assert_that(block).contains("chmod -R a+rX")

    for dockerfile in (_DOCKERFILE, _TOOLS_DOCKERFILE):
        text = dockerfile.read_text(encoding="utf-8")
        assert_that(text).described_as(str(dockerfile)).contains("/opt/uv-tools")


def test_app_image_smoke_probes_checkov_as_the_runtime_user() -> None:
    """The image proves checkov runs as ``lintro``, not only as root.

    checkov is the one tool installed into a ``uv tool`` venv, so its shim on
    PATH and the interpreter that shim points at carry separate permissions.
    The manifest-vs-image gate runs as root and would happily verify a venv the
    runtime user cannot read, shipping a tool that is on PATH and unusable for
    everyone who actually runs the image.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")

    assert_that(text).contains("gosu lintro checkov --version")


def test_integration_probe_allows_for_checkov_startup() -> None:
    """The integration gate's probe budget exceeds the 10s default.

    Every xdist worker runs the version probe at collection time, so four
    checkov interpreters start at once on a cold container filesystem. At the
    default budget three of four workers timed out while the fourth collected
    the module, which surfaces as an xdist "different tests were collected"
    error rather than an honest skip. The budget must also not be *stricter*
    than the 30s lintro itself allows a version check in production — compared
    against that documented default rather than ``VERSION_CHECK_TIMEOUT``,
    which is bound at import time from ``LINTRO_VERSION_TIMEOUT`` and is
    already overridden to 120 in docker-ci.
    """
    module = (
        _REPO_ROOT / "tests" / "integration" / "tools" / "checkov" / "test_check.py"
    ).read_text(encoding="utf-8")

    assert_that(module).contains(
        'require_tool("checkov", timeout=CHECKOV_PROBE_TIMEOUT)',
    )
    # Read the literal rather than importing the module: importing it would
    # run the very probe under test inside the unit suite.
    match = re.search(r"CHECKOV_PROBE_TIMEOUT: float = ([0-9.]+)", module)
    assert_that(match).is_not_none()
    budget = float(match.group(1))  # type: ignore[union-attr]

    assert_that(budget).is_greater_than(DEFAULT_TIMEOUT_SECONDS)
    assert_that(budget).is_greater_than_or_equal_to(_PRODUCTION_VERSION_TIMEOUT)


def test_every_failing_install_path_exits_non_zero() -> None:
    """Each failure branch in the checkov block exits, not just the first.

    A single ``exit 1`` somewhere in the block is satisfied by the version
    lookup alone, so the count is asserted: the docker missing-uv branch, the
    permission fixup and the failed install must each carry their own.
    """
    block = _checkov_install_block()

    assert_that(block.count("exit 1")).is_greater_than_or_equal_to(4)


def test_missing_uv_does_not_abort_a_full_local_install() -> None:
    """A uv-less local machine skips checkov instead of cancelling the run.

    The checkov block sits in the middle of ``main()``. Every other Python tool
    degrades to pip or brew when uv is absent, so escalating to ``exit 1`` here
    would silently cancel the dozen install blocks that follow. In Docker uv is
    always present, so its absence there is an image bug and must stop the
    build.
    """
    block = _checkov_install_block()
    missing_uv_at = block.find("command -v uv")
    assert_that(missing_uv_at).is_greater_than(-1)
    branch_end = block.find("elif", missing_uv_at + 1)
    assert_that(branch_end).described_as("branch end").is_greater_than(missing_uv_at)
    branch = block[missing_uv_at:branch_end]

    assert_that(branch).contains("Skipping checkov")
    assert_that(branch).contains('INSTALL_MODE" = "--docker')
    # A caller who named checkov asked for that tool specifically, so exiting 0
    # after skipping it would report work that never happened.
    assert_that(branch).contains('if [ -n "$TOOL_FILTER" ]')


def test_permission_fixup_is_scoped_to_the_checkov_venv() -> None:
    """The chmod touches checkov's venv only, and only in Docker.

    ``CHECKOV_TOOL_DIR`` is the shared uv tool root; locally that is the user's
    own, holding venvs this installer never created. Widening permissions
    across all of them would be a side effect nobody asked for, and the
    root-builds/non-root-runs mismatch the fixup exists for only happens in
    Docker.
    """
    block = _checkov_install_block()

    assert_that(block).contains('chmod -R a+rX "$CHECKOV_TOOL_DIR/checkov"')
    assert_that(block).does_not_contain('chmod -R a+rX "$CHECKOV_TOOL_DIR"')
    # A silently-failing chmod ships an image whose runtime user cannot run the
    # scanner, so the failure must not be swallowed.
    assert_that(block).does_not_contain(
        'chmod -R a+rX "$CHECKOV_TOOL_DIR/checkov" 2>/dev/null',
    )


def test_verification_loop_includes_checkov() -> None:
    """``tools_to_verify`` names checkov, so ``--tools checkov`` verifies it.

    A missing array entry would silently drop verification while every other
    assertion here still passed.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    verify_at = script.find("tools_to_verify=(")
    assert_that(verify_at).is_not_equal_to(-1)
    verify_array = script[verify_at : script.index(")", verify_at)]

    assert_that(verify_array).contains('"checkov"')


def test_tools_image_verifies_the_binary() -> None:
    """``docker/tools.Dockerfile`` proves checkov is on PATH after the install."""
    text = _TOOLS_DOCKERFILE.read_text(encoding="utf-8")

    assert_that(text).contains("checkov --version")


def test_app_image_bridges_checkov_until_the_next_tools_digest() -> None:
    """The app image FROMs a digest-pinned tools base that predates this tool.

    Until that digest is republished with ``checkov`` on PATH, the app image
    must install it itself, or the manifest-vs-image gate
    (``scripts/ci/verify-image-manifest-tools.sh``) fails with exit code 127
    for ``checkov``. This bridge is a no-op once the pinned digest already
    carries the binary.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    bridge_at = text.find("install-tools.sh --docker --tools ")

    assert_that(bridge_at).is_not_equal_to(-1)
    bridge_line = text[bridge_at : text.index("\n", bridge_at)]
    assert_that(bridge_line).contains("checkov")


@requires_modern_bash
@requires_uv
def test_dry_run_selects_checkov_and_installs_the_exact_pin() -> None:
    """``--tools checkov`` reaches the block and names the pinned version.

    The dry run proves the filter name the Dockerfile bridge passes actually
    selects a block, rather than being silently ignored, and that the version
    installed is the exact pin the manifest gate compares against.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    pinned = get_tool_version("checkov")

    result = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--dry-run", "--tools", "checkov"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(f"uv tool install checkov=={pinned}")
