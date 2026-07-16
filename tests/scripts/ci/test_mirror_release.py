# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the lintro-pre-commit mirror release helpers.

Covers ``bump_pin.py`` (the dependency-pin rewriter) and ``resolve-version.sh``
(the release-tag/prerelease classifier).
"""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - drives the shell helper under test; shell=False
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
BUMP_PIN_SCRIPT = ROOT / "scripts" / "ci" / "mirror" / "bump_pin.py"
RESOLVE_VERSION_SCRIPT = ROOT / "scripts" / "ci" / "mirror" / "resolve-version.sh"


def _load_bump_pin() -> Any:
    """Load ``bump_pin.py`` as an importable module.

    Returns:
        Any: The imported module object.
    """
    spec = importlib.util.spec_from_file_location("bump_pin", BUMP_PIN_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIRROR_PYPROJECT = """[project]
name = "lintro-pre-commit"
version = "0.69.0"
dependencies = ["lintro==0.69.0"]
"""


def test_current_pin_reads_dependency_table(tmp_path: Path) -> None:
    """The current pin is read from the parsed ``[project].dependencies``."""
    bump_pin = _load_bump_pin()

    assert_that(bump_pin._current_pin(content=_MIRROR_PYPROJECT)).is_equal_to("0.69.0")


def test_bump_updates_real_dependency_not_decoy_comment(tmp_path: Path) -> None:
    """A stray ``lintro==`` in a comment must not be mistaken for the pin."""
    bump_pin = _load_bump_pin()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "lintro-pre-commit"\n'
        "# historical note: lintro==0.1.0 was the first pin\n"
        'dependencies = ["lintro==0.69.0"]\n',
        encoding="utf-8",
    )

    changed = bump_pin.bump(path=pyproject, version="0.70.0")

    result = pyproject.read_text(encoding="utf-8")
    assert_that(changed).is_true()
    assert_that(result).contains('lintro==0.70.0"')
    # The decoy comment pin is left exactly as it was.
    assert_that(result).contains("lintro==0.1.0 was the first pin")


def test_bump_is_noop_when_already_pinned(tmp_path: Path) -> None:
    """Bumping to the current version reports no change."""
    bump_pin = _load_bump_pin()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_MIRROR_PYPROJECT, encoding="utf-8")

    assert_that(bump_pin.bump(path=pyproject, version="0.69.0")).is_false()


def test_current_pin_missing_raises(tmp_path: Path) -> None:
    """A pyproject without a lintro pin is a hard error."""
    bump_pin = _load_bump_pin()

    with pytest.raises(ValueError, match="No 'lintro=="):
        bump_pin._current_pin(content='[project]\ndependencies = ["pyyaml"]\n')


def _run_resolve_version(tag: str, output_file: Path) -> subprocess.CompletedProcess[str]:
    """Run resolve-version.sh with ``RELEASE_TAG`` and a GITHUB_OUTPUT file.

    Args:
        tag: The release tag to classify.
        output_file: File exposed to the script as ``GITHUB_OUTPUT``.

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    return subprocess.run(  # nosec B603 - fixed argv against a real binary; shell=False
        [str(RESOLVE_VERSION_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GITHUB_OUTPUT": str(output_file),
            "RELEASE_TAG": tag,
        },
    )


def _outputs(output_file: Path) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file into a dict.

    Args:
        output_file: The written GITHUB_OUTPUT file.

    Returns:
        dict[str, str]: Parsed key/value output pairs.
    """
    pairs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            pairs[key] = value
    return pairs


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.69.0", "false"),
        ("v1.2.3", "false"),
        ("v1.2.3.post1", "false"),
        ("v1.2.3rc1", "true"),
        ("v1.2.3a1", "true"),
        ("v1.2.3b2", "true"),
        ("v1.2.3-rc.1", "true"),
        ("v1.2.3-alpha.1", "true"),
        ("v1.2.3-beta", "true"),
        ("v1.2.3.dev1", "true"),
        ("v1.2.3RC2", "true"),
    ],
)
def test_resolve_version_prerelease_classification(
    tag: str,
    expected: str,
    tmp_path: Path,
) -> None:
    """PEP 440 prerelease/dev tags classify as prerelease; stable/post do not."""
    output_file = tmp_path / "gh-output"
    output_file.touch()

    result = _run_resolve_version(tag, output_file)

    assert_that(result.returncode).is_equal_to(0)
    outputs = _outputs(output_file)
    assert_that(outputs["is_prerelease"]).is_equal_to(expected)
    assert_that(outputs["version"]).is_equal_to(tag.lstrip("v"))
