"""Tests for the builtin tool index generator (``#2006``)."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - subprocess drives the generator script under test; invocations use shell=False
import sys
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro_build import builtin_index

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "generate-builtin-tool-index.py"

# Bound each child process so a hang surfaces as TimeoutExpired, not a stuck run.
SUBPROCESS_TIMEOUT_SECONDS = 120


def test_collect_module_names_skips_private_modules(tmp_path: Path) -> None:
    """Private and dunder modules stay out of the index.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    for name in ("ruff.py", "black.py", "_shared.py", "__init__.py", "notes.txt"):
        (tmp_path / name).write_text("")

    names = builtin_index.collect_module_names(tmp_path)

    assert_that(names).is_equal_to(["black", "ruff"])


def _registering_module() -> str:
    """Build the source of a definition module that registers a tool.

    Returns:
        Module source carrying the ``@register_tool`` decorator.
    """
    return "@register_tool\nclass Plugin:\n    pass\n"


def test_collect_registering_module_names_skips_helper_modules(
    tmp_path: Path,
) -> None:
    """Only modules applying ``@register_tool`` join the registering subset.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    (tmp_path / "ruff.py").write_text(_registering_module())
    (tmp_path / "black.py").write_text(_registering_module())
    (tmp_path / "oxlint_doctor.py").write_text("HELPER = True\n")

    assert_that(builtin_index.collect_module_names(tmp_path)).is_equal_to(
        ["black", "oxlint_doctor", "ruff"],
    )
    assert_that(builtin_index.collect_registering_module_names(tmp_path)).is_equal_to(
        ["black", "ruff"],
    )


def test_collect_registering_module_names_counts_reexport_shims(
    tmp_path: Path,
) -> None:
    """A shim for a per-tool package still contributes a registry entry.

    #2311 moves a tool's plugin to ``lintro/tools/<tool>/definition.py`` and
    leaves a re-export shim behind in the definitions package. The shim has no
    ``@register_tool`` of its own, but importing it imports the module that
    does, so the binary smoke test must still expect the tool.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    (tmp_path / "ruff.py").write_text(
        "from lintro.tools.ruff.definition import RuffPlugin\n\n"
        '__all__ = ["RuffPlugin"]\n',
    )
    (tmp_path / "black.py").write_text(_registering_module())
    (tmp_path / "helper.py").write_text(
        "from lintro.tools.core.cargo import find_cargo_root\n",
    )

    assert_that(builtin_index.collect_registering_module_names(tmp_path)).is_equal_to(
        ["black", "ruff"],
    )


def test_collect_registering_module_names_ignores_comments_and_docstrings(
    tmp_path: Path,
) -> None:
    """A commented or docstring ``@register_tool`` is not a registration.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    (tmp_path / "commented.py").write_text(
        "# @register_tool\nclass Plugin:\n    pass\n",
    )
    (tmp_path / "docstring.py").write_text(
        '"""This helper mentions @register_tool in the module docstring."""\n'
        "HELPER = True\n",
    )
    (tmp_path / "literal.py").write_text('DECORATOR = "@register_tool"\n')
    (tmp_path / "real.py").write_text(_registering_module())
    (tmp_path / "attr.py").write_text(
        "@registry.register_tool\nclass Plugin:\n    pass\n",
    )
    (tmp_path / "called.py").write_text(
        "@register_tool()\nclass Plugin:\n    pass\n",
    )

    assert_that(builtin_index.collect_registering_module_names(tmp_path)).is_equal_to(
        ["attr", "called", "real"],
    )


def test_collect_registering_module_names_fails_closed_on_syntax_error(
    tmp_path: Path,
) -> None:
    """An unparseable definition file is an input error, not a silent skip.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    (tmp_path / "broken.py").write_text("def (\n")

    with pytest.raises(ValueError, match="could not parse"):
        builtin_index.collect_registering_module_names(tmp_path)


def test_collect_module_names_rejects_missing_directory(tmp_path: Path) -> None:
    """A missing definitions directory is an input error, not an empty index.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    with pytest.raises(FileNotFoundError):
        builtin_index.collect_module_names(tmp_path / "nope")


def test_render_index_emits_importable_tuple(tmp_path: Path) -> None:
    """The rendered text imports as a module exposing the module-name tuple.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    rendered_path = tmp_path / "_rendered_index.py"
    rendered_path.write_text(
        builtin_index.render_index(["black", "ruff"], ["ruff"]),
    )

    spec = importlib.util.spec_from_file_location("_rendered_index", rendered_path)
    if spec is None or spec.loader is None:
        pytest.fail("rendered index could not be loaded as a module")
    rendered_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rendered_module)

    assert_that(rendered_module.BUILTIN_TOOL_MODULES).is_equal_to(("black", "ruff"))
    assert_that(rendered_module.REGISTERING_TOOL_MODULES).is_equal_to(("ruff",))


def test_resolve_paths_follows_repo_layout(tmp_path: Path) -> None:
    """Path resolution derives both locations from the repo root.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    definitions_dir, index_path = builtin_index.resolve_paths(tmp_path)

    assert_that(str(definitions_dir)).is_equal_to(
        str(tmp_path / "lintro" / "tools" / "definitions"),
    )
    assert_that(str(index_path)).is_equal_to(
        str(tmp_path / "lintro" / "plugins" / "_builtin_index.py"),
    )


def test_check_passes_against_real_repo() -> None:
    """The committed index matches the definitions directory.

    Runs only ``--check`` so the test cannot repair drift before asserting.
    """
    result = subprocess.run(  # nosec B603 - fixed argv run with shell=False in a controlled test
        [sys.executable, str(SCRIPT_PATH), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)
    assert_that(result.stdout).contains("is up to date")


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Point the generator at a throwaway definitions tree.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Tuple of (definitions directory, index path).
    """
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    index_path = tmp_path / "_builtin_index.py"
    return definitions, index_path


def test_check_reports_drift(
    fake_repo: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check mode exits 1 and reports drift without rewriting the index.

    Args:
        fake_repo: Definitions directory and index path fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    definitions, index_path = fake_repo
    (definitions / "ruff.py").write_text(_registering_module())
    index_path.write_text(builtin_index.render_index(["black"], ["black"]))

    exit_code = builtin_index.main(
        ["--check"],
        definitions_dir=definitions,
        index_path=index_path,
    )

    assert_that(exit_code).is_equal_to(1)
    assert_that(capsys.readouterr().out).contains("out of date")
    assert_that(index_path.read_text()).contains("black")


def test_write_mode_refreshes_the_index(fake_repo: tuple[Path, Path]) -> None:
    """Write mode replaces a stale index with the current module list.

    Args:
        fake_repo: Definitions directory and index path fixture.
    """
    definitions, index_path = fake_repo
    (definitions / "ruff.py").write_text(_registering_module())
    index_path.write_text(builtin_index.render_index(["black"], ["black"]))

    exit_code = builtin_index.main(
        [],
        definitions_dir=definitions,
        index_path=index_path,
    )

    assert_that(exit_code).is_equal_to(0)
    assert_that(index_path.read_text()).is_equal_to(
        builtin_index.render_index(["ruff"], ["ruff"]),
    )


def test_empty_definitions_directory_is_an_input_error(
    fake_repo: tuple[Path, Path],
) -> None:
    """An empty definitions tree fails loudly instead of writing an empty index.

    Args:
        fake_repo: Definitions directory and index path fixture.
    """
    definitions, index_path = fake_repo

    exit_code = builtin_index.main(
        [],
        definitions_dir=definitions,
        index_path=index_path,
    )

    assert_that(exit_code).is_equal_to(2)
    assert_that(index_path.exists()).is_false()


def test_generated_index_passes_black() -> None:
    """The committed index is byte-equivalent to black's output.

    Keeps the formatter and the drift gate from fighting on every PR.
    """
    index_path = REPO_ROOT / "lintro" / "plugins" / "_builtin_index.py"
    result = subprocess.run(  # nosec B603 - fixed argv run with shell=False in a controlled test
        [sys.executable, "-m", "black", "--check", "--quiet", str(index_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)


def test_generated_index_passes_ruff() -> None:
    """The committed index passes ruff without modification."""
    index_path = REPO_ROOT / "lintro" / "plugins" / "_builtin_index.py"
    result = subprocess.run(  # nosec B603 - fixed argv run with shell=False in a controlled test
        [sys.executable, "-m", "ruff", "check", str(index_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)
