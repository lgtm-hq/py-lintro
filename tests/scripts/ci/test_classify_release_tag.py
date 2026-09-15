# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release-tag prerelease classifier script."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"
_DELIMITER_RE = re.compile(r"^EOF_[0-9a-f]{32}$")


def _parse_github_output(text: str) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file written in the delimiter form only.

    Every entry must be ``name<<EOF_<32 hex>`` / value / delimiter; a bare
    ``name=value`` line is a test failure, since that is the form a value
    could inject.

    Args:
        text: The file content.

    Returns:
        Output name to value, in file order.
    """
    outputs: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = lines[index]
        assert_that(header).contains("<<")
        name, delimiter = header.split("<<", 1)
        assert_that(delimiter).matches(_DELIMITER_RE.pattern)
        assert_that(lines[index + 2]).is_equal_to(delimiter)
        assert_that(outputs).does_not_contain_key(name)
        outputs[name] = lines[index + 1]
        index += 3
    return outputs


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

    assert_that(exit_code).is_equal_to(0)
    # stdout is the one-line contract scripts/ci/mirror/resolve-version.sh
    # captures; the other outputs reach GITHUB_OUTPUT only.
    assert_that(capsys.readouterr().out.strip()).is_equal_to("is_prerelease=false")
    assert_that(
        _parse_github_output(output_file.read_text(encoding="utf-8")),
    ).is_equal_to(
        {
            "is_prerelease": "false",
            "is_rc": "false",
            "validation_channels": "false",
            "release_fault": "",
        },
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
    ],
)
def test_release_fault_is_trimmed_and_passed_through_for_rc_tags(
    fault: str | None,
    expected: str,
) -> None:
    """For an rc the fault output is the trimmed variable; unset is the no-op."""
    module = _load_module()
    assert_that(module.release_fault(tag="v0.160.3rc2", fault=fault)).is_equal_to(
        expected,
    )
    assert_that(module.KNOWN_FAULTS).contains("fail-build", "fail-publish-npm")


@pytest.mark.parametrize(
    "tag",
    ["v1.2.3", "v1.2.3+build.1", "v0.160.3a1", "v0.160.3b1"],
)
@pytest.mark.parametrize("fault", ["fail-build", "fail-publish-npm"])
def test_release_fault_is_empty_for_every_tag_but_an_rc(tag: str, fault: str) -> None:
    """A leftover RELEASE_FAULT can never break a stable (or alpha/beta) release."""
    module = _load_module()
    assert_that(module.release_fault(tag=tag, fault=fault)).is_equal_to("")
    outputs = module.classify(tag=tag, environ={module.RELEASE_FAULT_ENV: fault})
    assert_that(outputs["release_fault"]).is_equal_to("")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RELEASE_FAULT", "fail-everything"),
        ("RELEASE_FAULT", "FAIL-BUILD"),
        ("RELEASE_FAULT", "fail-build\nis_prerelease=false"),
        ("RELEASE_FAULT", "fail-build\x00"),
        ("RELEASE_VALIDATION_CHANNELS", "TRUE"),
        ("RELEASE_VALIDATION_CHANNELS", "1"),
        ("RELEASE_VALIDATION_CHANNELS", "yes"),
        ("RELEASE_VALIDATION_CHANNELS", "true\nrelease_fault=fail-build"),
    ],
)
def test_values_outside_the_allowlist_fail_closed(name: str, value: str) -> None:
    """Anything but the exact allowlisted values raises, whatever the tag."""
    module = _load_module()
    for tag in ("v0.160.3rc1", "v1.2.3"):
        with pytest.raises(module.InvalidVariableError) as excinfo:
            module.classify(tag=tag, environ={name: value})
        assert_that(str(excinfo.value)).contains(name)


def test_main_rejects_an_unknown_value_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown value exits 2 with an error annotation and leaves GITHUB_OUTPUT untouched."""
    module = _load_module()
    output_file = tmp_path / "gh_output"
    output_file.write_text("earlier=kept\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv(module.RELEASE_FAULT_ENV, "fail-everything")
    monkeypatch.delenv(module.VALIDATION_CHANNELS_ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v0.160.3rc1"])

    assert_that(module.main()).is_equal_to(2)
    captured = capsys.readouterr()
    assert_that(captured.out).contains(
        "::error title=Invalid release validation variable::",
    )
    assert_that(captured.out).does_not_contain("is_prerelease=")
    assert_that(captured.err).contains("RELEASE_FAULT")
    assert_that(output_file.read_text(encoding="utf-8")).is_equal_to("earlier=kept\n")


def test_main_rejects_an_embedded_newline_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value that could inject a second output line is refused outright."""
    module = _load_module()
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv(module.VALIDATION_CHANNELS_ENV, "true\nis_prerelease=false")
    monkeypatch.delenv(module.RELEASE_FAULT_ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v0.160.3rc1"])

    assert_that(module.main()).is_equal_to(2)
    assert_that(output_file.exists()).is_false()


def test_main_writes_each_output_once_in_delimiter_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid values produce exactly one delimiter block per output, random delimiters."""
    module = _load_module()
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv(module.VALIDATION_CHANNELS_ENV, "true")
    monkeypatch.setenv(module.RELEASE_FAULT_ENV, "fail-publish-npm")
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v0.160.3rc3"])

    assert_that(module.main()).is_equal_to(0)
    text = output_file.read_text(encoding="utf-8")
    assert_that(_parse_github_output(text)).is_equal_to(
        {
            "is_prerelease": "true",
            "is_rc": "true",
            "validation_channels": "true",
            "release_fault": "fail-publish-npm",
        },
    )
    delimiters = re.findall(r"<<(EOF_[0-9a-f]{32})$", text, flags=re.MULTILINE)
    assert_that(delimiters).is_length(4)
    assert_that(set(delimiters)).is_length(4)
    assert_that(re.findall(r"^[a-z_]+=", text, flags=re.MULTILINE)).is_empty()


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() reads RELEASE_VALIDATION_CHANNELS and RELEASE_FAULT from env."""
    module = _load_module()
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv(module.VALIDATION_CHANNELS_ENV, "true")
    monkeypatch.setenv(module.RELEASE_FAULT_ENV, "fail-build")
    monkeypatch.setattr("sys.argv", ["classify-release-tag.py", "v0.160.3rc3"])

    assert_that(module.main()).is_equal_to(0)
    captured = capsys.readouterr()
    assert_that(captured.out.strip()).is_equal_to("is_prerelease=true")
    assert_that(captured.err).contains("release_fault='fail-build'")
    assert_that(
        _parse_github_output(output_file.read_text(encoding="utf-8")),
    ).is_equal_to(
        {
            "is_prerelease": "true",
            "is_rc": "true",
            "validation_channels": "true",
            "release_fault": "fail-build",
        },
    )
