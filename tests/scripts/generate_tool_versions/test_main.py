"""Tests for the entry script's ``main()`` orchestration and exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that


def test_main_writes_outputs(retargeted_gen: ModuleType, fake_repo: Path) -> None:
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


def test_main_check_clean_exits_zero(retargeted_gen: ModuleType) -> None:
    """``--check`` exits 0 on a tree already in sync.

    Args:
        retargeted_gen: Generator module pointed at the fake repo.
    """
    retargeted_gen.main([])
    assert_that(retargeted_gen.main(["--check"])).is_equal_to(retargeted_gen.EXIT_OK)


def test_main_check_drift_exits_one(
    retargeted_gen: ModuleType,
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

    pkg = fake_repo / "package.json"
    data = json.loads(pkg.read_text())
    data["devDependencies"]["oxfmt"] = "^0.99.0"
    pkg.write_text(json.dumps(data, indent=2))

    rc = retargeted_gen.main(["--check"])
    assert_that(rc).is_equal_to(retargeted_gen.EXIT_DRIFT)
    captured = capsys.readouterr()
    assert_that(captured.out).contains("0.99.0")
    assert_that(captured.err).contains("Drift detected")


def test_main_input_error_exits_two(
    retargeted_gen: ModuleType,
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
