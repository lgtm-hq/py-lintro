"""Ruff import-section verdicts must not depend on the invocation directory (#2349).

Ruff classifies an import as first-party by resolving it against its ``src``
search path. With no ``[tool.ruff]`` table to anchor it (or under
``--isolated``), that path defaults to ruff's working directory, so the same
file gets opposite ``I001`` verdicts depending on where ruff runs. Lintro
anchors every ruff subprocess -- check and fix alike -- to the files' project
root, so ``lintro chk`` and ``lintro fmt`` agree with each other and with
themselves whichever directory they are started from.

The fixture mirrors the incident that found this: a CI helper module in
``scripts/ci/`` imported by a sibling script, in a repository that does not set
``[tool.ruff] src``. Seen from the repository root that helper is not a
top-level module (third-party); seen from ``scripts/ci/`` it is a sibling
(first-party).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import format_command
from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("ruff")

#: Import block sorted as ruff sees it from the repository root, where the
#: ``scripts/ci`` helper is not importable and so sorts with third-party code.
ROOT_ANCHORED_IMPORTS = (
    "import os\n\nimport pypi_utils\nimport yaml\n\nprint(os, pypi_utils, yaml)\n"
)

#: Import block sorted as ruff sees it from ``scripts/ci/``, where the helper is
#: a sibling module and so sorts into the first-party block.
SUBDIR_ANCHORED_IMPORTS = (
    "import os\n\nimport yaml\n\nimport pypi_utils\n\nprint(os, pypi_utils, yaml)\n"
)

START_DIRS = pytest.mark.parametrize(
    "start_dir",
    [".", "scripts/ci"],
    ids=["from_repo_root", "from_subdirectory"],
)


def _make_repo(root: Path, *, script: str, ruff_table: str = "") -> Path:
    """Create a repository with a ``scripts/ci`` helper and an importing sibling.

    Args:
        root: Directory to create the repository in.
        script: Source of ``scripts/ci/publish.py``, which imports the helper.
        ruff_table: Extra ``pyproject.toml`` content, e.g. a ``[tool.ruff]`` table.

    Returns:
        Path to ``scripts/ci/publish.py``.
    """
    ci_dir = root / "scripts" / "ci"
    ci_dir.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n' + ruff_table,
    )
    (ci_dir / "pypi_utils.py").write_text("X = 1\n")
    script_path = ci_dir / "publish.py"
    script_path.write_text(script)
    return script_path


def _import_sort_codes(ruff: BaseToolPlugin, target: Path) -> list[str]:
    """Run ruff check on ``target`` and return the reported rule codes.

    Args:
        ruff: Ruff plugin instance.
        target: File or directory to check.

    Returns:
        Rule codes of the issues ruff reported.
    """
    result = ruff.check([str(target)], {})
    return [str(getattr(issue, "code", "")) for issue in result.issues or []]


@START_DIRS
def test_check_verdict_ignores_invocation_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_dir: str,
) -> None:
    """Ruff check gives the root-anchored I001 verdict from any start directory.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        monkeypatch: Pytest fixture used to change the process cwd.
        start_dir: Directory, relative to the repository, lintro runs from.
    """
    script = _make_repo(tmp_path, script=SUBDIR_ANCHORED_IMPORTS)
    monkeypatch.chdir(tmp_path / start_dir)
    ruff = get_plugin("ruff")
    ruff.set_options(select=["I"])

    assert_that(_import_sort_codes(ruff, tmp_path)).contains("I001")
    assert_that(_import_sort_codes(ruff, script)).contains("I001")


@START_DIRS
def test_fix_sorts_imports_the_same_from_any_start_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_dir: str,
) -> None:
    """Ruff fix rewrites the import block to the root-anchored order everywhere.

    Before #2349 the fix path ran ruff in the process cwd, so ``lintro fmt``
    started in ``scripts/ci/`` accepted the first-party layout that ``lintro chk``
    rejects, and the two commands never converged.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        monkeypatch: Pytest fixture used to change the process cwd.
        start_dir: Directory, relative to the repository, lintro runs from.
    """
    script = _make_repo(tmp_path, script=SUBDIR_ANCHORED_IMPORTS)
    monkeypatch.chdir(tmp_path / start_dir)
    ruff = get_plugin("ruff")
    ruff.set_options(select=["I"])

    ruff.fix([str(tmp_path)], {})

    assert_that(script.read_text()).is_equal_to(ROOT_ANCHORED_IMPORTS)
    assert_that(_import_sort_codes(ruff, tmp_path)).does_not_contain("I001")


@START_DIRS
def test_explicit_ruff_src_keeps_its_classification(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_dir: str,
) -> None:
    """A repository's own ``[tool.ruff] src`` wins over the project-root anchor.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
        monkeypatch: Pytest fixture used to change the process cwd.
        start_dir: Directory, relative to the repository, lintro runs from.
    """
    script = _make_repo(
        tmp_path,
        script=SUBDIR_ANCHORED_IMPORTS,
        ruff_table='[tool.ruff]\nsrc = ["scripts/ci"]\n[tool.ruff.lint]\nselect = ["I"]\n',
    )
    monkeypatch.chdir(tmp_path / start_dir)
    ruff = get_plugin("ruff")
    # Test mode adds ``--isolated``, which would hide the repository's config.
    monkeypatch.delenv("LINTRO_TEST_MODE")

    assert_that(_import_sort_codes(ruff, tmp_path)).does_not_contain("I001")
    ruff.fix([str(tmp_path)], {})
    assert_that(script.read_text()).is_equal_to(SUBDIR_ANCHORED_IMPORTS)


def test_format_reports_paths_and_fixes_against_the_directory_ruff_ran_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ruff format --check`` paths resolve against the root ruff ran in.

    Three sibling projects are formatted from inside one of them, so the
    execution root (the outer repository) differs from the process cwd. Before
    #2349 the format check ran in the process cwd while its ``Would reformat:``
    paths were resolved against the execution root: ``pkg_b`` files were shown
    as nonexistent ``../fmt.py`` / ``../m.py`` and, because the verify pass
    could not find them, two of the seven fixes were reported as remaining.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
        monkeypatch: Pytest fixture used to change the process cwd.
    """
    (tmp_path / ".git").mkdir()
    for package in ("pkg_a", "pkg_b", "pkg_c"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "pyproject.toml").write_text(
            f'[project]\nname = "{package}"\nversion = "0.1.0"\n',
        )
    (tmp_path / "pkg_a" / "a.py").write_text("x=1\n")
    (tmp_path / "pkg_b" / "fmt.py").write_text("y=2\n")
    (tmp_path / "pkg_b" / "m.py").write_text("import sys\nimport os\nz=3\n")
    (tmp_path / "pkg_c" / "c.py").write_text("w=4\n")
    monkeypatch.chdir(tmp_path / "pkg_b")

    result = CliRunner().invoke(
        format_command,
        [
            "../pkg_a",
            "../pkg_b",
            "../pkg_c",
            "--tools",
            "ruff",
            "--tool-options",
            "ruff:format=True",
            "--yes",
            "--no-art",
        ],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("| ./fmt.py ", "| ./m.py ")
    assert_that(result.output).does_not_contain("../fmt.py", "../m.py")
    net_resolved = re.search(r"Net Resolved \(Native\)\s*\|\s*(\d+)", result.output)
    assert_that(net_resolved).is_not_none()
    assert_that(int(net_resolved.group(1)) if net_resolved else -1).is_equal_to(7)
    assert_that((tmp_path / "pkg_b" / "fmt.py").read_text()).is_equal_to("y = 2\n")
