"""Tests for the tool-version generator script.

Covers seed parsing, package.json/pyproject.toml ingestion, manifest
targeted-text update, deterministic rendering, and ``--check`` exit codes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "generate-tool-versions.py"


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    """Import the hyphen-named generator script as a module.

    Loading the entry script also bootstraps ``sys.path`` so its sibling
    ``_generator`` package becomes importable. Private helpers from that
    package that tests exercise directly are attached to the returned
    module for ergonomic access (``gen._collect_dep_strings``).

    Returns:
        Imported generator module exposing all top-level helpers.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_tool_versions",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load generator script at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so frozen-dataclass machinery
    # (sys.modules.get(cls.__module__).__dict__) resolves cleanly.
    sys.modules["generate_tool_versions"] = module
    spec.loader.exec_module(module)

    # Expose private package helpers needed by tests.
    from _generator.inputs import _collect_dep_strings  # noqa: PLC0415

    module._collect_dep_strings = _collect_dep_strings  # type: ignore[attr-defined]
    return module


@pytest.fixture
def fake_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a minimal fake repo with seed, package.json, pyproject, manifest.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        Path: Path to the fake repo root.
    """
    (tmp_path / "lintro").mkdir()
    (tmp_path / "lintro" / "tools").mkdir()
    (tmp_path / "scripts" / "ci").mkdir(parents=True)

    (tmp_path / "lintro" / "_tool_packages.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "NPM_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        '    "oxfmt": ToolName.OXFMT,\n'
        '    "@astrojs/check": None,\n'
        "}\n"
        "PYPI_PACKAGE_OWNERS: dict[str, ToolName | None] = {\n"
        '    "pytest": ToolName.PYTEST,\n'
        "}\n",
    )

    (tmp_path / "lintro" / "_tool_versions.py").write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "TOOL_VERSIONS: dict = {\n"
        "}\n",
    )

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {
                    "oxfmt": "^0.43.0",
                    "@astrojs/check": "0.9.8",
                },
            },
            indent=2,
        ),
    )

    (tmp_path / "pyproject.toml").write_text(
        "[project]\n" 'name = "fake"\n' 'dependencies = ["pytest>=9.0.3"]\n',
    )

    (tmp_path / "lintro" / "tools" / "manifest.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "oxfmt",
                        "version": "0.0.0",
                        "install": {"type": "npm", "package": "oxfmt"},
                    },
                    {
                        "name": "pytest",
                        "version": "0.0.0",
                        "install": {"type": "pip", "package": "pytest"},
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )

    yield tmp_path


def _retarget(gen: ModuleType, fake_repo: Path) -> None:
    """Point the generator's module-level paths at a fake repo.

    Args:
        gen: Imported generator module.
        fake_repo: Path to the temporary repo root.
    """
    # Dynamic module attribute reassignment; the import is hyphen-named so
    # mypy cannot statically resolve attributes on the loaded module object.
    gen.REPO_ROOT = fake_repo  # type: ignore[attr-defined]
    gen.SEED_PATH = fake_repo / "lintro" / "_tool_packages.py"  # type: ignore[attr-defined]
    gen.TOOL_VERSIONS_PATH = fake_repo / "lintro" / "_tool_versions.py"  # type: ignore[attr-defined]
    gen.PACKAGE_JSON_PATH = fake_repo / "package.json"  # type: ignore[attr-defined]
    gen.PYPROJECT_PATH = fake_repo / "pyproject.toml"  # type: ignore[attr-defined]
    gen.MANIFEST_PATH = fake_repo / "lintro" / "tools" / "manifest.json"  # type: ignore[attr-defined]
    gen.GENERATED_PATH = fake_repo / "lintro" / "_generated_versions.py"  # type: ignore[attr-defined]


def test_parse_seed_happy_path(gen: ModuleType, fake_repo: Path) -> None:
    """Seed parsing extracts npm and pypi owner mappings.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    seed = gen.parse_seed(fake_repo / "lintro" / "_tool_packages.py")
    assert_that(seed.npm_owners).is_equal_to(
        {"oxfmt": "OXFMT", "@astrojs/check": None},
    )
    assert_that(seed.pypi_owners).is_equal_to({"pytest": "PYTEST"})


def test_parse_seed_missing_file_errors(gen: ModuleType, tmp_path: Path) -> None:
    """Parsing a missing seed raises GenerationError.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    with pytest.raises(gen.GenerationError, match="seed file not found"):
        gen.parse_seed(tmp_path / "nope.py")


def test_parse_seed_rejects_non_toolname_value(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Values must be ``ToolName.X`` or ``None`` literals.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    bad = tmp_path / "seed.py"
    bad.write_text(
        'NPM_PACKAGE_OWNERS: dict[str, object] = {"x": "not a toolname"}\n'
        "PYPI_PACKAGE_OWNERS: dict[str, object] = {}\n",
    )
    with pytest.raises(gen.GenerationError, match="ToolName"):
        gen.parse_seed(bad)


def test_read_package_json_strips_caret(gen: ModuleType, fake_repo: Path) -> None:
    """Caret/tilde prefixes are stripped from version specifiers.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    versions = gen.read_package_json(fake_repo / "package.json")
    assert_that(versions["oxfmt"]).is_equal_to("0.43.0")
    assert_that(versions["@astrojs/check"]).is_equal_to("0.9.8")


def test_read_pyproject_versions_dedupes(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Same package pinned identically across tables yields one version.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text(
        '[project]\ndependencies = ["pytest>=9.0.3"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=9.0.3"]\n',
    )
    versions = gen.read_pyproject_versions(pyp, {"pytest"})
    assert_that(versions).is_equal_to({"pytest": "9.0.3"})


def test_read_pyproject_versions_inconsistent_raises(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Conflicting version pins for the same package fail generation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text(
        '[project]\ndependencies = ["pytest>=9.0.3"]\n'
        '[dependency-groups]\ntest = ["pytest>=8.0.0"]\n',
    )
    with pytest.raises(gen.GenerationError, match="inconsistent"):
        gen.read_pyproject_versions(pyp, {"pytest"})


def test_read_pyproject_versions_missing_raises(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """A seeded package with no pin in pyproject.toml fails generation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pyp = tmp_path / "pyproject.toml"
    pyp.write_text("[project]\ndependencies = []\n")
    with pytest.raises(gen.GenerationError, match="not found with a version pin"):
        gen.read_pyproject_versions(pyp, {"pytest"})


def test_render_generated_module_is_sorted(gen: ModuleType) -> None:
    """Rendered output orders package keys alphabetically for stability.

    Args:
        gen: Imported generator module.
    """
    text = gen.render_generated_module(
        npm_versions={"zeta": "1.0.0", "alpha": "2.0.0"},
        pypi_versions={"beta": "3.0.0", "kappa": "4.0.0"},
    )
    npm_block = text.split("NPM_VERSIONS")[1].split("PYPI_VERSIONS")[0]
    assert_that(npm_block.index('"alpha"')).is_less_than(
        npm_block.index('"zeta"'),
    )
    pypi_block = text.split("PYPI_VERSIONS")[1]
    assert_that(pypi_block.index('"beta"')).is_less_than(
        pypi_block.index('"kappa"'),
    )


def test_render_manifest_preserves_inline_arrays(gen: ModuleType) -> None:
    """Manifest update edits only ``version`` strings, not formatting.

    Args:
        gen: Imported generator module.
    """
    inline = (
        "{\n"
        '  "language_map": ["python", "js", "ts"],\n'
        '  "tools": [\n'
        "    {\n"
        '      "name": "oxfmt",\n'
        '      "version": "0.0.0",\n'
        '      "install": {"type": "npm", "package": "oxfmt"}\n'
        "    },\n"
        "    {\n"
        '      "name": "pytest",\n'
        '      "version": "0.0.0",\n'
        '      "install": {"type": "pip", "package": "pytest"}\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    new_text = gen.render_manifest(
        current_text=inline,
        target_versions={"oxfmt": "0.43.0", "pytest": "9.0.3"},
    )

    assert_that(new_text).contains('"language_map": ["python", "js", "ts"]')
    assert_that(new_text).contains('"version": "0.43.0"')
    assert_that(new_text).contains('"version": "9.0.3"')


def test_render_manifest_missing_entry_raises(gen: ModuleType) -> None:
    """A target name with no manifest entry triggers a clear error.

    Args:
        gen: Imported generator module.
    """
    with pytest.raises(gen.GenerationError, match="oxlint"):
        gen.render_manifest(
            current_text='{"tools": []}\n',
            target_versions={"oxlint": "1.0.0"},
        )


def test_build_target_versions_dispatches_by_install_type(
    gen: ModuleType,
) -> None:
    """``build_target_versions`` resolves each entry from the right source.

    Args:
        gen: Imported generator module.
    """
    manifest = {
        "tools": [
            {"name": "oxfmt", "install": {"type": "npm", "package": "oxfmt"}},
            {"name": "pytest", "install": {"type": "pip", "package": "pytest"}},
            {"name": "hadolint", "install": {"type": "binary"}},
        ],
    }
    targets = gen.build_target_versions(
        manifest_data=manifest,
        npm_versions={"oxfmt": "0.43.0"},
        pypi_versions={"pytest": "9.0.3"},
        binary_versions={"hadolint": "2.14.0"},
    )
    assert_that(targets).is_equal_to(
        {"oxfmt": "0.43.0", "pytest": "9.0.3", "hadolint": "2.14.0"},
    )


def test_read_binary_tool_versions(gen: ModuleType, tmp_path: Path) -> None:
    """Reads a flat ``ToolName.X: "ver"`` mapping from TOOL_VERSIONS.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    tv = tmp_path / "_tool_versions.py"
    tv.write_text(
        "from lintro.enums.tool_name import ToolName\n"
        "TOOL_VERSIONS: dict = {\n"
        '    ToolName.HADOLINT: "2.14.0",\n'
        '    ToolName.RUSTFMT: "1.8.0",\n'
        "}\n"
        "OTHER_DICT = {\n"
        '    ToolName.IGNORED: "9.9.9",\n'
        "}\n",
    )
    versions = gen.read_binary_tool_versions(tv)
    assert_that(versions).is_equal_to({"hadolint": "2.14.0", "rustfmt": "1.8.0"})


def test_main_writes_outputs(gen: ModuleType, fake_repo: Path) -> None:
    """Default mode writes both generated module and manifest.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    _retarget(gen, fake_repo)
    rc = gen.main([])
    assert_that(rc).is_equal_to(gen.EXIT_OK)

    generated = (fake_repo / "lintro" / "_generated_versions.py").read_text()
    assert_that(generated).contains('"oxfmt": "0.43.0"')
    assert_that(generated).contains('"pytest": "9.0.3"')

    manifest = (fake_repo / "lintro" / "tools" / "manifest.json").read_text()
    assert_that(manifest).contains('"version": "0.43.0"')


def test_main_check_clean_exits_zero(gen: ModuleType, fake_repo: Path) -> None:
    """``--check`` exits 0 on a tree already in sync.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
    """
    _retarget(gen, fake_repo)
    gen.main([])
    assert_that(gen.main(["--check"])).is_equal_to(gen.EXIT_OK)


def test_main_check_drift_exits_one(
    gen: ModuleType,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` exits 1 with a unified diff when sources differ.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
        capsys: Pytest stdout/stderr capture.
    """
    _retarget(gen, fake_repo)
    gen.main([])

    pkg = fake_repo / "package.json"
    data = json.loads(pkg.read_text())
    data["devDependencies"]["oxfmt"] = "^0.99.0"
    pkg.write_text(json.dumps(data, indent=2))

    rc = gen.main(["--check"])
    assert_that(rc).is_equal_to(gen.EXIT_DRIFT)
    captured = capsys.readouterr()
    assert_that(captured.out).contains("0.99.0")
    assert_that(captured.err).contains("Drift detected")


def test_main_input_error_exits_two(
    gen: ModuleType,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A seeded package missing from package.json yields exit code 2.

    Args:
        gen: Imported generator module.
        fake_repo: Fake repo fixture.
        capsys: Pytest stdout/stderr capture.
    """
    _retarget(gen, fake_repo)
    pkg = fake_repo / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {}}, indent=2))

    rc = gen.main([])
    assert_that(rc).is_equal_to(gen.EXIT_INPUT_ERROR)
    assert_that(capsys.readouterr().err).contains("oxfmt")


def test_generator_runs_against_real_repo() -> None:
    """End-to-end smoke test: generator is idempotent against the real repo.

    Runs the generator twice as a subprocess and asserts the second run with
    ``--check`` exits 0.
    """
    write_rc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(write_rc.returncode).is_equal_to(0)

    check_rc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(check_rc.returncode).is_equal_to(0), check_rc.stdout + check_rc.stderr


def test_generated_module_passes_black() -> None:
    """The generator's output is byte-equivalent to what black would produce.

    Guards against future emitter regressions that would make the formatter
    and the drift gate fight each other on every PR.
    """
    generated_path = REPO_ROOT / "lintro" / "_generated_versions.py"
    rc = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--quiet", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).is_equal_to(0), rc.stdout + rc.stderr


def test_generated_module_passes_ruff() -> None:
    """The generator's output passes ruff without modification."""
    generated_path = REPO_ROOT / "lintro" / "_generated_versions.py"
    rc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).is_equal_to(0), rc.stdout + rc.stderr


def test_collect_dep_strings_skips_non_dep_tables(gen: ModuleType) -> None:
    """Strings outside known dep tables are ignored.

    Args:
        gen: Imported generator module.
    """
    data = {
        "project": {
            "dependencies": ["pytest>=9.0.3"],
            "optional-dependencies": {"dev": ["mypy>=1.19.1"]},
            "keywords": ["lint", "format"],
        },
        "dependency-groups": {"test": ["ruff>=0.15.9"]},
        "tool": {
            "uv": {
                "constraint-dependencies": ["semgrep>=1.151.0"],
                "override-dependencies": ["sqlfluff>=4.0.0"],
                "sources": {"foo": {"git": "https://example.com/foo"}},
            },
            "lintro": {"banner": "looks-like-a-package>=1.0.0"},
        },
    }
    found = sorted(gen._collect_dep_strings(data))
    assert_that(found).is_equal_to(
        [
            "mypy>=1.19.1",
            "pytest>=9.0.3",
            "ruff>=0.15.9",
            "semgrep>=1.151.0",
            "sqlfluff>=4.0.0",
        ],
    )


def test_collect_dep_strings_skips_pep735_include_group(gen: ModuleType) -> None:
    """PEP 735 ``include-group`` dict entries are ignored.

    Args:
        gen: Imported generator module.
    """
    data = {
        "dependency-groups": {
            "test": ["pytest>=9.0.3", {"include-group": "dev"}],
            "dev": ["ruff>=0.15.9"],
        },
    }
    found = sorted(gen._collect_dep_strings(data))
    assert_that(found).is_equal_to(["pytest>=9.0.3", "ruff>=0.15.9"])


@pytest.mark.parametrize(
    "spec",
    [
        ">=1.0.0",
        "*",
        "latest",
        "git+https://example.com/foo.git",
        "file:../foo",
        "workspace:*",
        "npm:foo@1.0.0",
        "1.x",
    ],
)
def test_read_package_json_strict_rejects_non_exact(
    gen: ModuleType,
    tmp_path: Path,
    spec: str,
) -> None:
    """Seeded packages with non-exact specs raise GenerationError.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
        spec: Offending version spec.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {"oxfmt": spec}}))
    with pytest.raises(gen.GenerationError, match="oxfmt"):
        gen.read_package_json(pkg, strict_packages={"oxfmt"})


@pytest.mark.parametrize(
    "spec",
    [
        "1.2.3",
        "^1.2.3",
        "~1.2.3",
        "1.2.3-rc.1",
        "1.2.3+build.5",
    ],
)
def test_read_package_json_strict_accepts_exact(
    gen: ModuleType,
    tmp_path: Path,
    spec: str,
) -> None:
    """Exact SemVer pins (with optional ^/~ prefix) pass strict validation.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
        spec: Acceptable version spec.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {"oxfmt": spec}}))
    versions = gen.read_package_json(pkg, strict_packages={"oxfmt"})
    assert_that(versions["oxfmt"]).is_equal_to(spec.lstrip("^~"))


def test_read_package_json_non_strict_passes_through(
    gen: ModuleType,
    tmp_path: Path,
) -> None:
    """Packages outside the strict set are not version-validated.

    Args:
        gen: Imported generator module.
        tmp_path: Pytest temp dir.
    """
    pkg = tmp_path / "package.json"
    pkg.write_text(
        json.dumps(
            {
                "devDependencies": {
                    "oxfmt": "0.43.0",
                    "some-other-dep": ">=1.0.0",
                },
            },
        ),
    )
    versions = gen.read_package_json(pkg, strict_packages={"oxfmt"})
    assert_that(versions["oxfmt"]).is_equal_to("0.43.0")
    assert_that(versions["some-other-dep"]).is_equal_to(">=1.0.0")
