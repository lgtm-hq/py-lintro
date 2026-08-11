"""Tests for the builtin tool index generator (``#2006``)."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - subprocess drives the generator script under test; invocations use shell=False
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "generate-builtin-tool-index.py"


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    """Import the hyphen-named generator script as a module.

    Returns:
        The imported generator module.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_builtin_tool_index",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load generator script at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_builtin_tool_index"] = module
    spec.loader.exec_module(module)
    return module


def test_collect_module_names_skips_private_modules(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Private and dunder modules stay out of the index.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest-provided temporary directory.
    """
    for name in ("ruff.py", "black.py", "_shared.py", "__init__.py", "notes.txt"):
        (tmp_path / name).write_text("")

    names = gen.collect_module_names(tmp_path)

    assert_that(names).is_equal_to(["black", "ruff"])


def test_collect_module_names_rejects_missing_directory(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """A missing definitions directory is an input error, not an empty index.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest-provided temporary directory.
    """
    with pytest.raises(FileNotFoundError):
        gen.collect_module_names(tmp_path / "nope")


def test_render_index_emits_importable_tuple(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """The rendered text imports as a module exposing the module-name tuple.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest-provided temporary directory.
    """
    rendered_path = tmp_path / "_rendered_index.py"
    rendered_path.write_text(gen.render_index(["black", "ruff"]))

    spec = importlib.util.spec_from_file_location("_rendered_index", rendered_path)
    if spec is None or spec.loader is None:
        pytest.fail("rendered index could not be loaded as a module")
    rendered_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rendered_module)

    assert_that(rendered_module.BUILTIN_TOOL_MODULES).is_equal_to(("black", "ruff"))


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
    )

    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)


@pytest.fixture
def fake_repo(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Point the generator at a throwaway definitions tree.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch fixture (restores module constants).

    Returns:
        Tuple of (definitions directory, index path).
    """
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    index_path = tmp_path / "_builtin_index.py"
    monkeypatch.setattr(gen, "DEFINITIONS_DIR", definitions)
    monkeypatch.setattr(gen, "INDEX_PATH", index_path)
    monkeypatch.setattr(sys, "argv", ["generate-builtin-tool-index.py"])
    return definitions, index_path


def test_check_reports_drift(
    gen: ModuleType,
    fake_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check mode exits 1 and reports drift without rewriting the index.

    Args:
        gen: Imported generator module.
        fake_repo: Definitions directory and index path fixture.
        monkeypatch: Pytest monkeypatch fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    definitions, index_path = fake_repo
    (definitions / "ruff.py").write_text("")
    index_path.write_text(gen.render_index(["black"]))
    monkeypatch.setattr(sys, "argv", ["generate-builtin-tool-index.py", "--check"])

    exit_code = gen.main()

    assert_that(exit_code).is_equal_to(1)
    assert_that(capsys.readouterr().out).contains("out of date")
    assert_that(index_path.read_text()).contains("black")


def test_write_mode_refreshes_the_index(
    gen: ModuleType,
    fake_repo: tuple[Path, Path],
) -> None:
    """Write mode replaces a stale index with the current module list.

    Args:
        gen: Imported generator module.
        fake_repo: Definitions directory and index path fixture.
    """
    definitions, index_path = fake_repo
    (definitions / "ruff.py").write_text("")
    index_path.write_text(gen.render_index(["black"]))

    exit_code = gen.main()

    assert_that(exit_code).is_equal_to(0)
    assert_that(index_path.read_text()).is_equal_to(gen.render_index(["ruff"]))


def test_empty_definitions_directory_is_an_input_error(
    gen: ModuleType,
    fake_repo: tuple[Path, Path],
) -> None:
    """An empty definitions tree fails loudly instead of writing an empty index.

    Args:
        gen: Imported generator module.
        fake_repo: Definitions directory and index path fixture.
    """
    _, index_path = fake_repo

    exit_code = gen.main()

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
    )

    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)
