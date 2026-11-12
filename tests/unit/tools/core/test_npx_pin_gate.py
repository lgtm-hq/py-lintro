"""Fail-closed npx fallback: trust the pin only when npx cannot reuse anything.

Node tools resolve a concrete local binary first, then a ``PATH`` binary,
then ``bunx <pkg>@<pin>``, then ``npx``. An npx-only pin is trusted only when
no ``node_modules`` exists in the execution directory or above it and npm bin
links are not disabled; otherwise the tool is unavailable (#2043). Real-runner
coverage is in ``tests/integration/test_registry_runner_hoisted_bin.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.tools.core import snapshots
from lintro.tools.core.node_fallback import npx_pin_refusal
from lintro.tools.prettier.definition import PrettierPlugin

_PIN = get_tool_version("prettier") or ""


def _has_node_modules_above(directory: Path) -> bool:
    """Report whether a ``node_modules`` exists in *directory* or an ancestor.

    Args:
        directory: Directory to start from.

    Returns:
        True when a clean-project precondition cannot hold on this machine.
    """
    origin = directory.resolve()
    return any((d / "node_modules").exists() for d in (origin, *origin.parents))


@pytest.fixture
def clean_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project directory with no ``node_modules`` anywhere above it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped removal of inherited bin-link settings.

    Returns:
        The project directory.
    """
    if _has_node_modules_above(tmp_path):
        pytest.skip("a node_modules directory exists above the temporary directory")
    for key in ("NPM_CONFIG_BIN_LINKS", "npm_config_bin_links"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"name":"proj"}\n', encoding="utf-8")
    return project


def _only_runner(monkeypatch: pytest.MonkeyPatch, runner: str, tmp_path: Path) -> None:
    """Leave *runner* as the only Node command on ``PATH``.

    Args:
        monkeypatch: Scoped override of ``shutil.which``.
        runner: ``"npx"`` or ``"bunx"``.
        tmp_path: Directory for the stand-in runner executable.
    """
    stand_in = tmp_path / runner
    stand_in.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    stand_in.chmod(0o755)

    def which(command: str, *args: object, **kwargs: object) -> str | None:
        return str(stand_in) if command == runner else None

    monkeypatch.setattr(shutil, "which", which)


# ---------------------------------------------------------------------------
# npx_pin_refusal
# ---------------------------------------------------------------------------


def test_clean_directory_with_links_unset_trusts_the_pin(clean_dir: Path) -> None:
    """Nothing to reuse and default links: npx can only run the pin.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
    """
    assert_that(
        npx_pin_refusal(cwd=clean_dir, package="prettier", pin=_PIN, environ={}),
    ).is_none()


@pytest.mark.parametrize("where", ["cwd", "parent", "grandparent"])
def test_node_modules_in_cwd_or_any_ancestor_refuses(
    clean_dir: Path,
    where: str,
) -> None:
    """Any reachable ``node_modules`` makes the pin untrustworthy.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        where: Which directory gets the ``node_modules``.
    """
    nested = clean_dir / "a" / "b"
    nested.mkdir(parents=True)
    owner = {"cwd": nested, "parent": nested.parent, "grandparent": clean_dir}[where]
    (owner / "node_modules").mkdir()

    reason = npx_pin_refusal(cwd=nested, package="prettier", pin=_PIN, environ={})

    assert_that(reason).contains("only npx is available and a node_modules tree")
    assert_that(reason).contains((owner.resolve() / "node_modules").as_posix())
    assert_that(reason).contains(f"Install prettier@{_PIN} locally or install bun.")


@pytest.mark.parametrize(
    ("key", "value", "refused"),
    [
        ("NPM_CONFIG_BIN_LINKS", "false", True),
        ("npm_config_bin_links", "false", True),
        ("Npm_Config_Bin_Links", "FALSE", True),
        ("npm_config_bin_links", "0", True),
        ("npm_config_bin_links", "", True),
        ("npm_config_bin_links", "true", False),
        ("NPM_CONFIG_BIN_LINKS", " TRUE ", False),
    ],
    ids=[
        "upper-false",
        "lower-false",
        "mixed-case-key",
        "zero",
        "empty",
        "true",
        "padded-true",
    ],
)
def test_bin_links_must_be_unset_or_true(
    clean_dir: Path,
    key: str,
    value: str,
    refused: bool,
) -> None:
    """Disabled or ambiguous bin links refuse; unset or ``true`` is trusted.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        key: Environment variable spelling.
        value: Its value.
        refused: Whether the pin must be refused.
    """
    reason = npx_pin_refusal(
        cwd=clean_dir,
        package="prettier",
        pin=_PIN,
        environ={key: value},
    )

    if refused:
        assert_that(reason).contains("npm bin links are disabled")
        assert_that(reason).contains(f"{key}={value}")
    else:
        assert_that(reason).is_none()


# ---------------------------------------------------------------------------
# The gate, end to end through probe_tool
# ---------------------------------------------------------------------------


def test_npx_only_in_a_clean_project_reports_the_pin(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one trusted npx case keeps the pinned command as version evidence.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(clean_dir))

    assert_that(snapshot.command[0]).is_equal_to("npx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(verdict).is_none()


def test_npx_only_beside_a_node_modules_tree_is_unavailable(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hoisted copy npx might run makes the tool unavailable, not passing.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    member = clean_dir / "packages" / "app"
    member.mkdir(parents=True)
    (member / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    hoisted = clean_dir / "node_modules" / "prettier"
    hoisted.mkdir(parents=True)
    (hoisted / "package.json").write_text(
        json.dumps({"name": "prettier", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=member)
    verdict = verify_tool_version(PrettierPlugin().definition, cwd=str(member))

    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains("npx may run an unpinned copy")
    assert verdict is not None
    assert_that(verdict.unavailable_reason).contains("Install prettier@")


def test_concrete_local_binary_wins_over_the_npx_rule(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project-local binary is probed and run, though ``node_modules`` exists.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    binary = clean_dir / "node_modules" / ".bin" / "prettier"
    binary.parent.mkdir(parents=True)
    binary.write_text(f'#!/bin/sh\nprintf "{_PIN}\\n"\n', encoding="utf-8")
    binary.chmod(0o755)
    _only_runner(monkeypatch, "npx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)

    assert_that(snapshot.command).is_equal_to((binary.resolve().as_posix(),))
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)


def test_bunx_keeps_its_pin_beside_a_node_modules_tree(
    clean_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With bunx, the pin stands even where npx would be refused.

    Args:
        clean_dir: Project without any ``node_modules`` above it.
        tmp_path: Directory for the stand-in runner.
        monkeypatch: Scoped PATH override.
    """
    (clean_dir / "node_modules").mkdir()
    _only_runner(monkeypatch, "bunx", tmp_path)

    snapshot = snapshots.probe_tool("prettier", search_root=clean_dir)

    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
