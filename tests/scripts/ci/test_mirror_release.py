# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for lintro-pre-commit mirror release automation scripts."""

from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404 - drives repo shell scripts with shell=False
import textwrap
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MIRROR_DIR = ROOT / "scripts" / "ci" / "mirror"
RESOLVE_SCRIPT = MIRROR_DIR / "resolve-version.sh"
BUMP_SCRIPT = MIRROR_DIR / "bump_pin.py"
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"


def _load_bump_pin_module() -> Any:
    """Load bump_pin.py as an importable module."""
    spec = importlib.util.spec_from_file_location("bump_pin", BUMP_SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_resolve(
    *,
    release_tag: str,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run resolve-version.sh with the given release tag."""
    env = os.environ.copy()
    env["RELEASE_TAG"] = release_tag
    if github_output is not None:
        env["GITHUB_OUTPUT"] = str(github_output)
    else:
        env.pop("GITHUB_OUTPUT", None)
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(RESOLVE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


@pytest.mark.parametrize(
    ("tag", "expected_prerelease"),
    [
        ("v1.2.3", "false"),
        ("1.2.3", "false"),
        ("v1.2.3+build.1", "false"),
        ("v1.2.3rc1", "true"),
        ("v1.2.3-rc.1", "true"),
        ("v1.2.3-alpha.1", "true"),
        ("v1.2.3.dev1", "true"),
        ("v1.2.3RC1", "true"),
        ("garbage", "true"),
    ],
)
def test_resolve_version_matches_classifier(
    tag: str,
    expected_prerelease: str,
    tmp_path: Path,
) -> None:
    """resolve-version.sh classifies tags like classify-release-tag.py."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag=tag, github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains(f"is_prerelease={expected_prerelease}")
    assert_that(body).contains(f"version={tag.lstrip('v')}")


def test_resolve_version_writes_tag_and_version(
    tmp_path: Path,
) -> None:
    """resolve-version.sh emits tag and version outputs."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag="v0.69.0", github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains("tag=v0.69.0")
    assert_that(body).contains("version=0.69.0")
    assert_that(body).contains("is_prerelease=false")


def test_bump_updates_real_dependency_not_decoy_comment(tmp_path: Path) -> None:
    """bump_pin.py rewrites the parsed dependency, not an earlier decoy string."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            # Example only: "lintro==0.1.0"
            [project]
            name = "lintro-pre-commit"
            dependencies = [
              "lintro==0.69.0",
            ]
            """,
        ),
        encoding="utf-8",
    )

    changed = module.bump(path=pyproject, version="0.70.0")

    assert_that(changed).is_true()
    updated = pyproject.read_text(encoding="utf-8")
    assert_that(updated).contains('"lintro==0.70.0"')
    assert_that(updated).contains('# Example only: "lintro==0.1.0"')


def test_bump_check_reports_drift(tmp_path: Path) -> None:
    """--check exits non-zero when the parsed pin does not match."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            """,
        ),
        encoding="utf-8",
    )

    ok = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            "python3",
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.69.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    drift = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            "python3",
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.70.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(ok.returncode).is_equal_to(0)
    assert_that(drift.returncode).is_equal_to(1)
    assert_that(drift.stderr).contains("Drift")


def test_bump_missing_pin_raises(tmp_path: Path) -> None:
    """Missing lintro dependency raises a clear error."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["other==1.0.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="No 'lintro==<version>' pin"):
        module.bump(path=pyproject, version="1.0.0")


def test_bump_multiple_pins_raises(tmp_path: Path) -> None:
    """Multiple lintro pins in dependency tables fail closed."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            optional-dependencies.dev = ["lintro==0.68.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Expected exactly one"):
        module.bump(path=pyproject, version="1.0.0")
