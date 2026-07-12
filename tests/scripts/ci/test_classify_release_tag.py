# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release-tag prerelease classifier script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"


def _load_module() -> Any:
    """Load the hyphenated classifier script as an importable module."""
    spec = importlib.util.spec_from_file_location(
        "classify_release_tag",
        CLASSIFY_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # Stable releases (with and without a leading v).
        ("v1.2.3", False),
        ("1.2.3", False),
        ("v0.70.6", False),
        ("v10.20.30", False),
        # Build metadata must never mark a tag prerelease (the core defect:
        # the b in "build" tripped the old contains() gate).
        ("v1.2.3+build.1", False),
        ("v1.2.3+20240101", False),
        ("1.2.3+build.abc", False),
        # PEP 440 bare prerelease forms (the repo's actual prerelease tags).
        ("v1.2.3a1", True),
        ("v1.2.3b2", True),
        ("v1.2.3rc1", True),
        ("1.2.3a1", True),
        # SemVer prerelease components.
        ("v1.2.3-alpha", True),
        ("v1.2.3-beta.2", True),
        ("v1.2.3-rc.1", True),
        # Combined prerelease + build metadata stays prerelease.
        ("v1.2.3-rc.1+build.2", True),
        # actions-v tags are not stable version cores (also excluded upstream
        # by startsWith in the workflow); fail closed to prerelease.
        ("actions-v1.0.0", True),
        # Unrecognized / malformed tags fail closed to prerelease.
        ("garbage", True),
        ("v1.2", True),
        ("v1.2.3.4", True),
        ("", True),
    ],
)
def test_is_prerelease_tag(tag: str, expected: bool) -> None:
    """Classify representative stable and prerelease tag forms."""
    module = _load_module()
    assert_that(module.is_prerelease_tag(tag=tag)).is_equal_to(expected)


def test_surrounding_whitespace_is_ignored() -> None:
    """A tag padded with whitespace classifies like its trimmed form."""
    module = _load_module()
    assert_that(module.is_prerelease_tag(tag="  v1.2.3  ")).is_false()
    assert_that(module.is_prerelease_tag(tag="  v1.2.3-rc.1  ")).is_true()


def test_main_writes_github_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() prints and appends is_prerelease to GITHUB_OUTPUT."""
    module = _load_module()
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v1.2.3+build.1"])

    exit_code = module.main()

    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out.strip()).is_equal_to("is_prerelease=false")
    assert_that(output_file.read_text(encoding="utf-8")).is_equal_to(
        "is_prerelease=false\n",
    )


def test_main_prerelease_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() reports prerelease tags without requiring GITHUB_OUTPUT."""
    module = _load_module()
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v1.2.3-rc.1"])

    exit_code = module.main()

    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out.strip()).is_equal_to("is_prerelease=true")


def test_main_rejects_wrong_arg_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns exit code 1 when not given exactly one tag argument."""
    module = _load_module()
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py"])
    assert_that(module.main()).is_equal_to(1)
