"""Tests for the entry script's ``main()`` orchestration and exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from assertpy import assert_that


def test_main_writes_outputs(retargeted_gen: SimpleNamespace, fake_repo: Path) -> None:
    """Default mode writes both generated module and manifest.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
    """
    rc = retargeted_gen.main([])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_OK)

    generated = (fake_repo / "lintro" / "_generated_versions.py").read_text()
    assert_that(generated).contains('"oxfmt": "0.43.0"')
    assert_that(generated).contains('"pytest": "9.0.3"')

    manifest = (fake_repo / "lintro" / "tools" / "manifest.json").read_text()
    assert_that(manifest).contains('"version": "0.43.0"')


def test_main_reads_semgrep_from_requirements_file(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
) -> None:
    """Semgrep's version comes from requirements-semgrep.txt, not pyproject.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
    """
    seed = fake_repo / "lintro" / "_tool_packages.py"
    seed.write_text(
        seed.read_text().replace(
            '"pytest": ToolName.PYTEST,\n',
            '"pytest": ToolName.PYTEST,\n    "semgrep": ToolName.SEMGREP,\n',
        ),
    )
    (fake_repo / "requirements-semgrep.txt").write_text("semgrep==9.9.9\n")
    manifest_path = fake_repo / "lintro" / "tools" / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["tools"].append(
        {
            "name": "semgrep",
            "version": "0.0.0",
            "install": {"type": "pip", "package": "semgrep"},
        },
    )
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")

    rc = retargeted_gen.main([])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_OK)
    generated = (fake_repo / "lintro" / "_generated_versions.py").read_text()
    assert_that(generated).contains('"semgrep": "9.9.9"')
    assert_that(manifest_path.read_text()).contains('"version": "9.9.9"')


def test_main_check_clean_exits_zero(retargeted_gen: SimpleNamespace) -> None:
    """``--check`` exits 0 on a tree already in sync.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
    """
    retargeted_gen.main([])
    assert_that(retargeted_gen.main(["--check"])).is_equal_to(retargeted_gen.EXIT_OK)


def _bump_oxfmt_version(fake_repo: Path, version: str) -> None:
    """Simulate a Renovate-style pin bump in package.json.

    Args:
        fake_repo: Fake repo fixture root.
        version: New oxfmt version pin to write.
    """
    pkg = fake_repo / "package.json"
    data = json.loads(pkg.read_text())
    data["devDependencies"]["oxfmt"] = version
    pkg.write_text(json.dumps(data, indent=2))


def test_main_check_drift_exits_one(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` exits 1 with a unified diff when sources differ.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
    """
    retargeted_gen.main([])

    _bump_oxfmt_version(fake_repo, "0.99.0")

    rc = retargeted_gen.main(["--check"])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_DRIFT)
    captured = capsys.readouterr()
    assert_that(captured.out).contains("0.99.0")
    assert_that(captured.err).contains("Drift detected")


def test_pin_bump_then_regenerate_passes_check(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
) -> None:
    """Simulated Renovate pin bump plus regeneration clears the drift gate.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
    """
    retargeted_gen.main([])

    _bump_oxfmt_version(fake_repo, "0.99.0")

    assert_that(retargeted_gen.main(["--check"])).is_equal_to(
        retargeted_gen.EXIT_DRIFT,
    )
    assert_that(retargeted_gen.main([])).is_equal_to(retargeted_gen.EXIT_OK)
    assert_that(retargeted_gen.main(["--check"])).is_equal_to(retargeted_gen.EXIT_OK)


def test_main_input_error_exits_two(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A seeded package missing from package.json yields exit code 2.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
    """
    pkg = fake_repo / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {}}, indent=2))

    rc = retargeted_gen.main([])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_INPUT_ERROR)
    assert_that(capsys.readouterr().err).contains("oxfmt")


def test_main_missing_manifest_exits_two(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing manifest yields exit code 2, not an unhandled ``OSError``.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
    """
    (fake_repo / "lintro" / "tools" / "manifest.json").unlink()

    rc = retargeted_gen.main([])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_INPUT_ERROR)
    assert_that(capsys.readouterr().err).contains("manifest.json")


def test_main_invalid_manifest_exits_two(
    retargeted_gen: SimpleNamespace,
    fake_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed manifest JSON yields exit code 2.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
        fake_repo: Fake repo fixture root.
        capsys: Pytest stdout/stderr capture.
    """
    manifest = fake_repo / "lintro" / "tools" / "manifest.json"
    manifest.write_text('{"tools": [')

    rc = retargeted_gen.main([])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_INPUT_ERROR)
    assert_that(capsys.readouterr().err).contains("manifest.json")
