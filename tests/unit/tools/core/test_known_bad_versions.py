"""Known-bad tool versions are refused, and old-but-working ones get a hint (#3556).

The rule has three cases for an installed tool version:

1. Refused: below the minimum, or on the tool's known-bad list. A refused
   security scanner fails the run; any other tool is skipped as before.
2. Accepted but older than the pin: runs, with one upgrade suggestion per
   tool per run.
3. The pin or newer: runs, no message.

The known-bad entries here are test-only: lintro still pins osv-scanner 2.7.0,
so the real 2.7.0 entry cannot be listed until the pin moves to 2.7.1.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.tool_outcome import Skipped, Unavailable
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core.manifest_models import KnownBadVersion
from lintro.tools.core.snapshots import ToolSnapshot
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import (
    check_tool_version,
    find_known_bad_version,
)
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.utils.tool_snapshots import reset_upgrade_suggestions

#: A test-only bad release between osv-scanner's minimum (2.6.0) and its pin.
_BAD = "2.6.5"

_ENTRY = KnownBadVersion(
    versions=f"=={_BAD}",
    reason="skips npm packages with no resolved field",
    reference="https://example.invalid/known-bad",
)


def _fake_osv_scanner(directory: Path, version: str) -> Path:
    """Write an executable that answers ``--version`` like osv-scanner.

    Args:
        directory: Directory to create the executable in.
        version: Release version the executable reports.

    Returns:
        Path to the executable.
    """
    binary = directory / "osv-scanner"
    binary.write_text(
        f"#!/bin/sh\necho 'osv-scanner version: {version}'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


@pytest.fixture
def osv_known_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give osv_scanner a test-only known-bad entry in the loaded manifest.

    Args:
        monkeypatch: Restores the manifest loader after the test.
    """
    real = ManifestRegistry.load()
    tools = {tool.name: tool for tool in real.all_tools(include_dev=True)}
    tools["osv_scanner"] = replace(
        tools["osv_scanner"],
        known_bad_versions=(_ENTRY,),
    )
    patched = ManifestRegistry(
        tools=tools,
        language_map=real.language_map,
        profiles=real.profiles,
    )
    monkeypatch.setattr(
        ManifestRegistry,
        "load",
        classmethod(lambda _cls: patched),
    )


@pytest.mark.parametrize(
    ("version", "specifier", "listed"),
    [
        ("2.7.0", "==2.7.0", True),
        ("v2.7.0", "==2.7.0", True),
        ("2.7.1", "==2.7.0", False),
        ("2.5.1", ">=2.4.0,<2.6.0", True),
        ("2.6.0", ">=2.4.0,<2.6.0", False),
        ("not-a-version", "==2.7.0", False),
        ("2.7.0", "not a specifier", False),
    ],
)
def test_find_known_bad_version_matches_specifiers(
    version: str,
    specifier: str,
    listed: bool,
) -> None:
    """An entry names one release or a range; anything unparseable never matches.

    Args:
        version: Installed version string.
        specifier: The entry's PEP 440 specifier.
        listed: Whether the version is on the list.
    """
    entry = replace(_ENTRY, versions=specifier)

    found = find_known_bad_version(version, [entry])

    assert_that(found).is_equal_to(entry if listed else None)


def test_registry_reads_known_bad_versions_and_drops_malformed(
    tmp_path: Path,
) -> None:
    """Well-formed entries load in order; one missing a reason is left out.

    Args:
        tmp_path: Directory for the manifest file.
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "tools": [
                    {
                        "name": "demo",
                        "version": "3.0.0",
                        "known_bad_versions": [
                            {"versions": "==2.0.0", "reason": "r1", "reference": "x"},
                            {"versions": "==2.1.0", "reference": "y"},
                            {"versions": "<1.5", "reason": "r3", "reference": "z"},
                        ],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    tool = ManifestRegistry._load_from_path(manifest).get("demo")

    assert_that(tool.known_bad_versions).is_equal_to(
        (
            KnownBadVersion(versions="==2.0.0", reason="r1", reference="x"),
            KnownBadVersion(versions="<1.5", reason="r3", reference="z"),
        ),
    )


@pytest.mark.usefixtures("osv_known_bad")
def test_known_bad_release_above_minimum_is_refused(tmp_path: Path) -> None:
    """A listed release fails the check even though it meets the minimum.

    Args:
        tmp_path: Directory for the fake binary.
    """
    binary = _fake_osv_scanner(tmp_path, _BAD)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.version_check_passed).is_false()
    assert_that(info.known_bad).is_equal_to(_ENTRY)
    assert_that(info.error_message).is_equal_to(
        f"Version {_BAD} is refused because it is known to give wrong results: "
        f"{_ENTRY.reason} (see {_ENTRY.reference})",
    )
    assert_that(info.below_recommended).is_false()


@pytest.mark.usefixtures("osv_known_bad")
def test_unlisted_release_between_minimum_and_pin_passes(tmp_path: Path) -> None:
    """A release that is not listed passes and is marked older than the pin.

    Args:
        tmp_path: Directory for the fake binary.
    """
    binary = _fake_osv_scanner(tmp_path, "2.6.4")

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.version_check_passed).is_true()
    assert_that(info.known_bad).is_none()
    assert_that(info.error_message).is_none()
    assert_that(info.below_recommended).is_true()


@pytest.mark.usefixtures("osv_known_bad")
def test_refused_security_scanner_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known-bad osv-scanner fails, and the output says the scan did not run.

    Args:
        tmp_path: Directory holding the fake binary, put first on PATH.
        monkeypatch: Sets PATH and clears the version-lag override.
    """
    _fake_osv_scanner(tmp_path, _BAD)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)

    result = verify_tool_version(OsvScannerPlugin().definition, cwd=tmp_path)

    assert result is not None  # narrow: a refused version returns a result
    execution = result.outcome.execution
    assert_that(execution).is_instance_of(Unavailable)
    assert isinstance(execution, Unavailable)  # narrow for the attribute reads
    assert_that(execution.strict).is_true()
    assert_that(result.success).is_false()
    assert_that(execution.reason).starts_with("security scanner did not run: ")
    assert_that(result.output).contains(
        "security scanner did not run, so nothing was scanned",
        f"Version {_BAD} is refused",
        _ENTRY.reason,
        _ENTRY.reference,
        "To upgrade, run: lintro install --upgrade osv_scanner",
    )


@pytest.mark.parametrize(
    ("tool_type_security", "expected"),
    [(True, Unavailable), (False, Skipped)],
    ids=["security-fails", "other-skips"],
)
def test_below_minimum_outcome_depends_on_security(
    monkeypatch: pytest.MonkeyPatch,
    tool_type_security: bool,
    expected: type,
) -> None:
    """Below the minimum, a security scanner fails and another tool is skipped.

    Args:
        monkeypatch: Replaces the snapshot probe and clears the lag override.
        tool_type_security: Whether the definition is a security scanner.
        expected: Execution outcome type the refusal produces.
    """
    from lintro.enums.tool_type import ToolType

    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    snapshot = ToolSnapshot(
        name="demo",
        available=True,
        version="1.0.0",
        min_version="2.0.0",
        version_check_passed=False,
        probe_error="Version 1.0.0 is below minimum requirement 2.0.0",
    )
    monkeypatch.setattr(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    definition = ToolDefinition(
        name="demo",
        description="Test",
        tool_type=ToolType.SECURITY if tool_type_security else ToolType.LINTER,
    )

    result = verify_tool_version(definition)

    assert result is not None  # narrow: a refused version returns a result
    assert_that(result.outcome.execution).is_instance_of(expected)
    assert_that(result.success).is_equal_to(not tool_type_security)
    assert_that(result.output).contains(
        "Version 1.0.0 is below minimum requirement 2.0.0",
        "To upgrade, run: lintro install --upgrade demo",
    )


def test_upgrade_suggestion_is_shown_once_per_tool_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older-than-pinned tool runs; the suggestion appears once per run.

    Args:
        monkeypatch: Replaces the snapshot probe.
    """
    snapshot = ToolSnapshot(
        name="ruff",
        available=True,
        version="0.10.0",
        version_check_passed=True,
        min_version="0.9.0",
        recommended_version="0.14.0",
        below_recommended=True,
    )
    monkeypatch.setattr(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    definition = ToolDefinition(name="ruff", description="lint")
    messages: list[str] = []
    logger.add(messages.append, format="{message}", level="WARNING")

    results = [verify_tool_version(definition) for _ in range(3)]
    reset_upgrade_suggestions()
    results.append(verify_tool_version(definition))

    assert_that(results).is_equal_to([None, None, None, None])
    expected = (
        "ruff 0.10.0 works but is older than 0.14.0, the version lintro tests. "
        "To upgrade, run: lintro install --upgrade ruff\n"
    )
    assert_that(messages).is_equal_to([expected, expected])
