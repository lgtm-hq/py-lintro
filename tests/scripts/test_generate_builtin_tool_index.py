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


def _make_package(*, root: Path, name: str, modules: dict[str, str]) -> Path:
    """Create a per-tool package directory under ``root``.

    Args:
        root: Directory standing in for ``lintro/tools``.
        name: Package name.
        modules: Mapping of module file name to source text. ``__init__.py`` is
            created automatically when absent.

    Returns:
        The created package directory.
    """
    package = root / name
    package.mkdir(parents=True)
    if "__init__.py" not in modules:
        (package / "__init__.py").write_text("")
    for file_name, source in modules.items():
        (package / file_name).write_text(source)
    return package


def _registering_module() -> str:
    """Build the source of a definition module that registers a tool.

    Returns:
        Module source carrying the ``@register_tool`` decorator.
    """
    return "@register_tool\nclass Plugin:\n    pass\n"


def test_collect_module_names_lists_one_entry_per_package(tmp_path: Path) -> None:
    """A package is entered through its ``definition`` module alone.

    Importing ``definition`` runs the package ``__init__``, so listing the
    package's other modules would only defeat their deliberate laziness.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(
        root=tmp_path,
        name="ruff",
        modules={"definition.py": _registering_module(), "commands.py": "X = 1\n"},
    )
    _make_package(
        root=tmp_path,
        name="black",
        modules={"definition.py": _registering_module()},
    )

    names = builtin_index.collect_module_names(tmp_path)

    assert_that(names).is_equal_to(["black.definition", "ruff.definition"])


def test_collect_module_names_lists_every_module_of_a_shared_package(
    tmp_path: Path,
) -> None:
    """A package with no ``definition`` module contributes all its modules.

    That is the ``ts_checker`` family: shared scaffolding behind ``tsc`` and
    ``vue-tsc`` with no plugin of its own, and so no single entry point.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(
        root=tmp_path,
        name="ts_checker",
        modules={"base.py": "X = 1\n", "command.py": "Y = 2\n", "_private.py": ""},
    )

    names = builtin_index.collect_module_names(tmp_path)

    assert_that(names).is_equal_to(["ts_checker.base", "ts_checker.command"])


def test_collect_module_names_skips_private_and_non_tool_packages(
    tmp_path: Path,
) -> None:
    """Private packages, ``core`` and loose files stay out of the index.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(
        root=tmp_path,
        name="ruff",
        modules={"definition.py": _registering_module()},
    )
    _make_package(root=tmp_path, name="core", modules={"runner.py": "X = 1\n"})
    _make_package(root=tmp_path, name="_scratch", modules={"thing.py": "X = 1\n"})
    (tmp_path / "not_a_package").mkdir()
    (tmp_path / "not_a_package" / "loose.py").write_text("X = 1\n")
    (tmp_path / "__init__.py").write_text("")

    names = builtin_index.collect_module_names(tmp_path)

    assert_that(names).is_equal_to(["ruff.definition"])


def test_collect_registering_package_names_skips_shared_packages(
    tmp_path: Path,
) -> None:
    """Only packages applying ``@register_tool`` join the registering set.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(
        root=tmp_path,
        name="ruff",
        modules={"definition.py": _registering_module()},
    )
    _make_package(
        root=tmp_path,
        name="black",
        modules={"definition.py": _registering_module()},
    )
    _make_package(root=tmp_path, name="ts_checker", modules={"base.py": "X = 1\n"})

    assert_that(builtin_index.collect_registering_package_names(tmp_path)).is_equal_to(
        ["black", "ruff"],
    )


def test_collect_registering_package_names_scans_every_module(
    tmp_path: Path,
) -> None:
    """A tool registered outside ``definition`` still counts.

    The registering set drives the released binary's registry assertion, so it
    must follow the decorator rather than the file name.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(
        root=tmp_path,
        name="oxlint",
        modules={"definition.py": "X = 1\n", "doctor.py": _registering_module()},
    )

    assert_that(builtin_index.collect_registering_package_names(tmp_path)).is_equal_to(
        ["oxlint"],
    )


def test_collect_registering_package_names_ignores_comments_and_docstrings(
    tmp_path: Path,
) -> None:
    """A commented or docstring ``@register_tool`` is not a registration.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    sources = {
        "commented": "# @register_tool\nclass Plugin:\n    pass\n",
        "docstring": (
            '"""This helper mentions @register_tool in the module docstring."""\n'
            "HELPER = True\n"
        ),
        "literal": 'DECORATOR = "@register_tool"\n',
        "real": _registering_module(),
        "attr": "@registry.register_tool\nclass Plugin:\n    pass\n",
        "called": "@register_tool()\nclass Plugin:\n    pass\n",
    }
    for name, source in sources.items():
        _make_package(root=tmp_path, name=name, modules={"definition.py": source})

    assert_that(builtin_index.collect_registering_package_names(tmp_path)).is_equal_to(
        ["attr", "called", "real"],
    )


def test_collect_registering_package_names_fails_closed_on_syntax_error(
    tmp_path: Path,
) -> None:
    """An unparseable tool module is an input error, not a silent skip.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    _make_package(root=tmp_path, name="broken", modules={"definition.py": "def (\n"})

    with pytest.raises(ValueError, match="could not parse"):
        builtin_index.collect_registering_package_names(tmp_path)


def test_collect_module_names_rejects_missing_directory(tmp_path: Path) -> None:
    """A missing tools directory is an input error, not an empty index.

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
        builtin_index.render_index(
            ["black.definition", "ruff.definition"],
            ["ruff"],
        ),
    )

    spec = importlib.util.spec_from_file_location("_rendered_index", rendered_path)
    if spec is None or spec.loader is None:
        pytest.fail("rendered index could not be loaded as a module")
    rendered_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rendered_module)

    assert_that(rendered_module.BUILTIN_TOOL_MODULES).is_equal_to(
        ("black.definition", "ruff.definition"),
    )
    assert_that(rendered_module.REGISTERING_TOOL_PACKAGES).is_equal_to(("ruff",))


def test_resolve_paths_follows_repo_layout(tmp_path: Path) -> None:
    """Path resolution derives both locations from the repo root.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    tools_dir, index_path = builtin_index.resolve_paths(tmp_path)

    assert_that(str(tools_dir)).is_equal_to(
        str(tmp_path / "lintro" / "tools"),
    )
    assert_that(str(index_path)).is_equal_to(
        str(tmp_path / "lintro" / "plugins" / "_builtin_index.py"),
    )


def test_check_passes_against_real_repo() -> None:
    """The committed index matches the per-tool packages on disk.

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
    """Point the generator at a throwaway tools tree.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Tuple of (tools directory, index path).
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    index_path = tmp_path / "_builtin_index.py"
    return tools, index_path


def test_check_reports_drift(
    fake_repo: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check mode exits 1 and reports drift without rewriting the index.

    Args:
        fake_repo: Tools directory and index path fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    tools, index_path = fake_repo
    _make_package(
        root=tools,
        name="ruff",
        modules={"definition.py": _registering_module()},
    )
    index_path.write_text(
        builtin_index.render_index(["black.definition"], ["black"]),
    )

    exit_code = builtin_index.main(
        ["--check"],
        tools_dir=tools,
        index_path=index_path,
    )

    assert_that(exit_code).is_equal_to(1)
    assert_that(capsys.readouterr().out).contains("out of date")
    assert_that(index_path.read_text()).contains("black")


def test_write_mode_refreshes_the_index(fake_repo: tuple[Path, Path]) -> None:
    """Write mode replaces a stale index with the current module list.

    Args:
        fake_repo: Tools directory and index path fixture.
    """
    tools, index_path = fake_repo
    _make_package(
        root=tools,
        name="ruff",
        modules={"definition.py": _registering_module()},
    )
    index_path.write_text(
        builtin_index.render_index(["black.definition"], ["black"]),
    )

    exit_code = builtin_index.main(
        [],
        tools_dir=tools,
        index_path=index_path,
    )

    assert_that(exit_code).is_equal_to(0)
    assert_that(index_path.read_text()).is_equal_to(
        builtin_index.render_index(["ruff.definition"], ["ruff"]),
    )


def test_empty_tools_directory_is_an_input_error(
    fake_repo: tuple[Path, Path],
) -> None:
    """An empty tools tree fails loudly instead of writing an empty index.

    Args:
        fake_repo: Tools directory and index path fixture.
    """
    tools, index_path = fake_repo

    exit_code = builtin_index.main(
        [],
        tools_dir=tools,
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
