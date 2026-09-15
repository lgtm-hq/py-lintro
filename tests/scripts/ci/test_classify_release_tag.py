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
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None  # narrow type for mypy
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

    monkeypatch.delenv(module.VALIDATION_CHANNELS_ENV, raising=False)
    monkeypatch.delenv(module.RELEASE_FAULT_ENV, raising=False)

    exit_code = module.main()

    expected = [
        "is_prerelease=false",
        "is_rc=false",
        "validation_channels=false",
        "release_fault=",
    ]
    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out.strip().splitlines()).is_equal_to(expected)
    assert_that(output_file.read_text(encoding="utf-8")).is_equal_to(
        "".join(f"{line}\n" for line in expected),
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
    assert_that(capsys.readouterr().out.splitlines()[0]).is_equal_to(
        "is_prerelease=true",
    )


def test_main_rejects_wrong_arg_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns exit code 1 when not given exactly one tag argument."""
    module = _load_module()
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py"])
    assert_that(module.main()).is_equal_to(1)


# --- #2633: release candidates and the validation-only switches --------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # Only the PEP 440 bare rcN form (what the checkpoint procedure tags).
        ("v0.160.3rc1", True),
        ("0.160.3rc4", True),
        ("v1.2.3rc10", True),
        # Every other prerelease form is not a candidate for the channels.
        ("v0.160.3a1", False),
        ("v0.160.3b2", False),
        ("v1.2.3-rc.1", False),
        ("v1.2.3rc1+build.1", False),
        ("v1.2.3rc", False),
        # Stable, malformed and empty are never candidates.
        ("v1.2.3", False),
        ("v1.2.3+build.1", False),
        ("garbage", False),
        ("", False),
    ],
)
def test_is_rc_tag(tag: str, expected: bool) -> None:
    """Only ``X.Y.ZrcN`` is a release candidate; an rc is always a prerelease."""
    module = _load_module()
    assert_that(module.is_rc_tag(tag=tag)).is_equal_to(expected)
    if expected:
        assert_that(module.is_prerelease_tag(tag=tag)).is_true()


@pytest.mark.parametrize(
    ("tag", "switch", "expected"),
    [
        # The switch opens the channels for an rc only.
        ("v0.160.3rc1", "true", True),
        ("v0.160.3rc1", " true ", True),
        # Default off: unset, empty or anything but the literal ``true``.
        ("v0.160.3rc1", None, False),
        ("v0.160.3rc1", "", False),
        ("v0.160.3rc1", "false", False),
        ("v0.160.3rc1", "TRUE", False),
        ("v0.160.3rc1", "1", False),
        # A stable tag, an alpha, a beta or a SemVer rc never opens them,
        # whatever the variable holds: the switch cannot touch a release.
        ("v1.2.3", "true", False),
        ("v0.160.3a1", "true", False),
        ("v0.160.3b1", "true", False),
        ("v1.2.3-rc.1", "true", False),
    ],
)
def test_validation_channels_open_for_rc_tags_only(
    tag: str,
    switch: str | None,
    expected: bool,
) -> None:
    """``validation_channels`` is rc AND the variable equals ``true``."""
    module = _load_module()
    assert_that(
        module.validation_channels_enabled(tag=tag, switch=switch),
    ).is_equal_to(expected)


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("fail-build", "fail-build"),
        ("fail-publish-npm", "fail-publish-npm"),
        (" fail-build\n", "fail-build"),
        # Unknown names pass through: no step matches them, and the run log
        # shows what the variable held instead of silently dropping it.
        ("fail-everything", "fail-everything"),
    ],
)
def test_release_fault_is_trimmed_and_passed_through(
    fault: str | None,
    expected: str,
) -> None:
    """The fault output is the trimmed variable; unset is the empty no-op."""
    module = _load_module()
    assert_that(module.release_fault(fault=fault)).is_equal_to(expected)
    assert_that(module.KNOWN_FAULTS).contains("fail-build", "fail-publish-npm")


def test_classify_emits_every_output_in_order() -> None:
    """The job maps each output by name, so the set and order are the contract."""
    module = _load_module()
    outputs = module.classify(
        tag="v0.160.3rc2",
        environ={
            module.VALIDATION_CHANNELS_ENV: "true",
            module.RELEASE_FAULT_ENV: "fail-publish-npm",
        },
    )
    assert_that(list(outputs)).is_equal_to(
        ["is_prerelease", "is_rc", "validation_channels", "release_fault"],
    )
    assert_that(outputs).is_equal_to(
        {
            "is_prerelease": "true",
            "is_rc": "true",
            "validation_channels": "true",
            "release_fault": "fail-publish-npm",
        },
    )


def test_classify_defaults_are_off_without_the_variables() -> None:
    """With neither variable set an rc classifies exactly as before #2633."""
    module = _load_module()
    outputs = module.classify(tag="v0.160.3rc1", environ={})
    assert_that(outputs["is_prerelease"]).is_equal_to("true")
    assert_that(outputs["validation_channels"]).is_equal_to("false")
    assert_that(outputs["release_fault"]).is_equal_to("")


def test_main_reads_the_switches_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() reads RELEASE_VALIDATION_CHANNELS and RELEASE_FAULT from env."""
    module = _load_module()
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setenv(module.VALIDATION_CHANNELS_ENV, "true")
    monkeypatch.setenv(module.RELEASE_FAULT_ENV, "fail-build")
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v0.160.3rc3"])

    assert_that(module.main()).is_equal_to(0)
    assert_that(capsys.readouterr().out.splitlines()).is_equal_to(
        [
            "is_prerelease=true",
            "is_rc=true",
            "validation_channels=true",
            "release_fault=fail-build",
        ],
    )
