"""Tests for the ``lintro setup`` CLI command."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.setup import _generate_config, setup_command
from lintro.utils.project_detection import (
    detect_package_managers,
    detect_project_languages,
)

# ── detect_project_languages ─────────────────────────────────────────


def test_detect_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect Python when pyproject.toml exists."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("python")


def test_detect_python_via_requirements_txt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect Python from a top-level ``requirements.txt`` (no pyproject)."""
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("python")


def test_detect_python_via_nested_requirements_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect Python from the ``requirements/base.txt`` layout.

    The filename here is ``base.txt`` (not ``requirements*.txt``), so this
    covers the pip-tools-style directory layout that a filename-only glob
    would miss.
    """
    reqs_dir = tmp_path / "requirements"
    reqs_dir.mkdir()
    (reqs_dir / "base.txt").write_text("requests==2.32.0\n")
    (reqs_dir / "prod.txt").write_text("-r base.txt\ngunicorn==22.0.0\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("python")


def test_detect_python_ignores_vendored_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requirements file only inside a vendored tree does not mark Python."""
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "requirements.txt").write_text("requests==2.32.0\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).does_not_contain("python")


def test_detect_javascript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect JavaScript when package.json exists."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("javascript")


def test_detect_typescript_via_tsconfig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect TypeScript when tsconfig.json exists."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "tsconfig.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("typescript")


def test_detect_rust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect Rust when Cargo.toml exists."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("rust")


def test_detect_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect Shell when .sh files exist in scripts/ directory."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "deploy.sh").write_text("#!/bin/bash\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("shell")


def test_detect_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect Docker when Dockerfile exists."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.13\n")
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("docker")


def test_detect_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect GitHub Actions when .github/workflows/ exists."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("github_actions")


def test_detect_multiple_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect multiple languages from various indicators."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.13\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).contains("python", "docker", "github_actions")


def test_detect_empty_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return empty list for a project with no indicator files."""
    monkeypatch.chdir(tmp_path)

    langs = detect_project_languages()
    assert_that(langs).is_empty()


# ── detect_package_managers ──────────────────────────────────────────


def test_detect_uv_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect uv when pyproject.toml exists and uv is available."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)

    with patch(
        "lintro.utils.project_detection.shutil.which",
        side_effect=lambda n: "/bin/uv" if n == "uv" else None,
    ):
        managers = detect_package_managers()

    assert_that(managers).contains_key("uv")


def test_detect_pip_manager_no_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to pip when uv is not available."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.chdir(tmp_path)

    with patch("lintro.utils.project_detection.shutil.which", return_value=None):
        managers = detect_package_managers()

    assert_that(managers).contains_key("pip")


def test_detect_bun_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect bun when package.json exists and bun is available."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    monkeypatch.chdir(tmp_path)

    with patch(
        "lintro.utils.project_detection.shutil.which",
        side_effect=lambda n: "/bin/bun" if n == "bun" else None,
    ):
        managers = detect_package_managers()

    assert_that(managers).contains_key("bun")


# ── _generate_config ─────────────────────────────────────────────────


def test_generate_config_structure() -> None:
    """Generated YAML contains expected sections."""
    config = _generate_config(["ruff", "mypy"], ["python"])

    assert_that(config).contains("enforce:")
    assert_that(config).contains("execution:")
    assert_that(config).contains("tools:")
    assert_that(config).contains("ruff:")
    assert_that(config).contains("mypy:")


def test_generate_config_python_line_length() -> None:
    """Python projects get line_length in enforce section."""
    config = _generate_config(["ruff"], ["python"])
    assert_that(config).contains("line_length: 88")


def test_generate_config_no_python() -> None:
    """Non-Python projects omit line_length."""
    config = _generate_config(["hadolint"], ["docker"])
    assert_that(config).does_not_contain("line_length")


# ── CLI invocation ───────────────────────────────────────────────────


def _patch_setup_deps() -> tuple[Any, Any, Any, Any]:
    """Common patches for setup CLI tests."""
    registry = MagicMock()
    registry.profile_names = ["minimal", "recommended", "complete", "ci"]
    registry.profiles = {}
    registry.tools_for_profile.return_value = []
    ctx = MagicMock()

    return (
        patch(
            "lintro.cli_utils.commands.setup.ManifestRegistry.load",
            return_value=registry,
        ),
        patch(
            "lintro.cli_utils.commands.setup.RuntimeContext.detect",
            return_value=ctx,
        ),
        patch(
            "lintro.cli_utils.commands.setup.detect_project_languages",
            return_value=["python"],
        ),
        patch(
            "lintro.cli_utils.commands.setup.detect_package_managers",
            return_value={"uv": "pyproject.toml"},
        ),
    )


def test_setup_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--dry-run does not write a config file."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    p1, p2, p3, p4 = _patch_setup_deps()

    with p1, p2, p3, p4:
        result = runner.invoke(
            setup_command,
            ["--profile", "minimal", "--yes", "--dry-run"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that((tmp_path / ".lintro-config.yaml").exists()).is_false()


def test_setup_profile_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--profile minimal --yes runs non-interactively."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    p1, p2, p3, p4 = _patch_setup_deps()

    with p1, p2, p3, p4:
        result = runner.invoke(
            setup_command,
            ["--profile", "minimal", "--yes", "--skip-install"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that((tmp_path / ".lintro-config.yaml").exists()).is_true()


def test_setup_invalid_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid profile name raises an error."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    p1, p2, p3, p4 = _patch_setup_deps()

    with p1, p2, p3, p4:
        result = runner.invoke(setup_command, ["--profile", "nonexistent", "--yes"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Unknown profile")


def test_setup_skip_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--skip-install skips the installer entirely."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    p1, p2, p3, p4 = _patch_setup_deps()

    with p1, p2, p3, p4:
        result = runner.invoke(
            setup_command,
            ["--profile", "minimal", "--yes", "--skip-install"],
        )

    assert_that(result.exit_code).is_equal_to(0)
