"""Known-bad tool versions are refused, and old-but-working ones get a hint (#3556).

The rule has three cases for an installed tool version:

1. Refused: below the minimum, or on the tool's known-bad list. A refused
   security scanner fails the run; any other tool is skipped as before.
2. Accepted but older than the pin: runs, with one upgrade suggestion per
   tool per run.
3. The pin or newer: runs, no message.

The known-bad entries here are test-only: lintro still pins osv-scanner 2.7.0,
so the real 2.7.0 entry cannot be listed until the pin moves to 2.7.1. One
test-only bad release sits between the minimum (2.6.0) and the pin, the other
above the pin.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger
from rich.console import Console

from lintro.cli_utils.commands.doctor import _render_tool_line
from lintro.cli_utils.commands.versions import versions_command
from lintro.enums.action import Action
from lintro.enums.tool_status import ToolStatus
from lintro.models.core.tool_outcome import Skipped, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core import snapshots
from lintro.tools.core.install_context import RuntimeContext
from lintro.tools.core.manifest_models import KnownBadVersion
from lintro.tools.core.snapshots import ToolSnapshot
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import (
    check_tool_version,
    find_known_bad_version,
)
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.utils.doctor_report import check_tool
from lintro.utils.json_output import serialize_tool_result
from lintro.utils.tool_metadata import tool_notes
from lintro.utils.tool_snapshots import (
    reset_upgrade_suggestions,
    snapshot_is_runnable,
    with_version_lag_note,
)

#: A test-only bad release between osv-scanner's minimum (2.6.0) and its pin.
_BAD = "2.6.5"

#: A test-only bad release above osv-scanner's pin (2.7.0).
_BAD_ABOVE_PIN = "2.8.0"

_ENTRY = KnownBadVersion(
    versions=f"=={_BAD}",
    reason="skips npm packages with no resolved field",
    reference="https://example.invalid/known-bad",
)

_ENTRY_ABOVE_PIN = replace(_ENTRY, versions=f"=={_BAD_ABOVE_PIN}")

#: A test-only bad release that is also below osv-scanner's minimum (2.6.0).
_BAD_BELOW_MIN = "2.5.0"

_ENTRY_BELOW_MIN = KnownBadVersion(
    versions=">=2.4.0,<2.6.0",
    reason="reads only the first document of a pnpm lockfile",
    reference="https://example.invalid/below-minimum",
)

#: What every surface says to install instead of a known-bad osv-scanner.
_INSTALL_PIN = "Install 2.7.0, the version lintro tests."


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
        f"#!/bin/sh\necho call >> \"$0.calls\"\necho 'osv-scanner version: {version}'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _probe_count(binary: Path) -> int:
    """Count how often the fake osv-scanner ran.

    Args:
        binary: The fake executable.

    Returns:
        The number of runs recorded next to it.
    """
    calls = binary.parent / f"{binary.name}.calls"
    return len(calls.read_text().splitlines()) if calls.exists() else 0


def _list_osv_known_bad(
    monkeypatch: pytest.MonkeyPatch,
    entries: tuple[KnownBadVersion, ...],
) -> None:
    """Make the loaded manifest give osv_scanner these known-bad entries.

    Args:
        monkeypatch: Restores the manifest loader after the test.
        entries: The known-bad entries.
    """
    real = ManifestRegistry.load()
    tools = {tool.name: tool for tool in real.all_tools(include_dev=True)}
    tools["osv_scanner"] = replace(tools["osv_scanner"], known_bad_versions=entries)
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


@pytest.fixture
def osv_known_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give osv_scanner test-only known-bad entries in the loaded manifest.

    Args:
        monkeypatch: Restores the manifest loader after the test.
    """
    _list_osv_known_bad(monkeypatch, (_ENTRY, _ENTRY_ABOVE_PIN, _ENTRY_BELOW_MIN))


@pytest.fixture
def osv_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str], Path]:
    """Put a fake osv-scanner first on a PATH that holds no other wrapped tool.

    Args:
        tmp_path: Directory for the fake binary.
        monkeypatch: Sets PATH and clears the version-lag override.

    Returns:
        A function that writes the fake binary for a given version.
    """
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    return lambda version: _fake_osv_scanner(tmp_path, version)


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages for one test, then remove the sink.

    Yields:
        list[str]: The list the messages are appended to.
    """
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}", level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink)


@pytest.mark.parametrize(
    ("version", "specifier", "listed"),
    [
        ("2.7.0", "==2.7.0", True),
        ("v2.7.0", "==2.7.0", True),
        ("2.7.1", "==2.7.0", False),
        ("2.5.1", ">=2.4.0,<2.6.0", True),
        ("2.6.0", ">=2.4.0,<2.6.0", False),
        ("not-a-version", "==2.7.0", False),
    ],
)
def test_find_known_bad_version_matches_specifiers(
    version: str,
    specifier: str,
    listed: bool,
) -> None:
    """An entry names one release or a range; an unreadable version never matches.

    Args:
        version: Installed version string.
        specifier: The entry's PEP 440 specifier.
        listed: Whether the version is on the list.
    """
    entry = replace(_ENTRY, versions=specifier)

    found = find_known_bad_version(version, [entry])

    assert_that(found).is_equal_to(entry if listed else None)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"versions": "2.6.5"}, "must be comma-separated clauses"),
        ({"versions": "not a specifier"}, "must be comma-separated clauses"),
        ({"versions": ""}, "non-empty 'versions'"),
        ({"reason": " "}, "non-empty 'reason'"),
        ({"reference": ""}, "non-empty 'reference'"),
    ],
    ids=["bare-version", "prose", "empty-versions", "blank-reason", "no-reference"],
)
def test_unreadable_known_bad_entry_is_an_error(
    fields: dict[str, str],
    message: str,
) -> None:
    """An entry that cannot be applied raises instead of matching nothing.

    Args:
        fields: Fields that replace the valid entry's.
        message: Text the error must contain.
    """
    with pytest.raises(ValueError, match=message):
        KnownBadVersion(
            versions=fields.get("versions", _ENTRY.versions),
            reason=fields.get("reason", _ENTRY.reason),
            reference=fields.get("reference", _ENTRY.reference),
        )


def _write_manifest(path: Path, known_bad: object) -> Path:
    """Write a one-tool manifest with the given ``known_bad_versions`` value.

    Args:
        path: Directory for the manifest file.
        known_bad: The value stored under ``known_bad_versions``.

    Returns:
        Path to the manifest file.
    """
    manifest = path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "tools": [
                    {
                        "name": "demo",
                        "version": "3.0.0",
                        "known_bad_versions": known_bad,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    return manifest


def test_registry_reads_known_bad_versions_in_order(tmp_path: Path) -> None:
    """Well-formed entries load in manifest order.

    Args:
        tmp_path: Directory for the manifest file.
    """
    manifest = _write_manifest(
        tmp_path,
        [
            {"versions": "==2.0.0", "reason": "r1", "reference": "x"},
            {"versions": "<1.5", "reason": "r3", "reference": "z"},
        ],
    )

    tool = ManifestRegistry._load_from_path(manifest).get("demo")

    assert_that(tool.known_bad_versions).is_equal_to(
        (
            KnownBadVersion(versions="==2.0.0", reason="r1", reference="x"),
            KnownBadVersion(versions="<1.5", reason="r3", reference="z"),
        ),
    )


@pytest.mark.parametrize(
    ("known_bad", "message"),
    [
        (
            [{"versions": "2.6.5", "reason": "r", "reference": "x"}],
            "must be comma-separated clauses",
        ),
        ([{"versions": "==2.1.0", "reference": "y"}], "must have exactly the keys"),
        (
            [{"versions": "==2.1.0", "reasons": "r", "reference": "y"}],
            "must have exactly the keys",
        ),
        ("==2.1.0", "known_bad_versions must be a list"),
    ],
    ids=["bare-version", "missing-reason", "misspelled-key", "non-list"],
)
def test_registry_refuses_to_load_a_malformed_known_bad_entry(
    tmp_path: Path,
    known_bad: object,
    message: str,
) -> None:
    """A malformed entry fails the load and names the tool.

    Args:
        tmp_path: Directory for the manifest file.
        known_bad: The malformed ``known_bad_versions`` value.
        message: Text the error must contain.
    """
    manifest = _write_manifest(tmp_path, known_bad)

    with pytest.raises(ValueError, match=message) as raised:
        ManifestRegistry._load_from_path(manifest)

    assert_that(str(raised.value)).contains("Tool 'demo'")


def test_version_check_fails_loudly_on_a_malformed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest that does not load stops the check instead of passing it.

    Swallowing the load error used to leave the known-bad list empty, so the
    release the bad entry meant to refuse ran and passed.

    Args:
        tmp_path: Directory for the manifest and the fake binary.
        monkeypatch: Points the registry at the malformed manifest.
    """
    manifest = _write_manifest(
        tmp_path,
        [{"versions": _BAD, "reason": "r", "reference": "x"}],
    )
    monkeypatch.setattr("lintro.tools.core.tool_registry._MANIFEST_PATH", manifest)
    ManifestRegistry.clear_cache()
    binary = _fake_osv_scanner(tmp_path, _BAD)

    try:
        with pytest.raises(
            ValueError,
            match="must be comma-separated clauses",
        ) as raised:
            check_tool_version("osv_scanner", [str(binary)])
    finally:
        ManifestRegistry.clear_cache()

    assert_that(str(raised.value)).contains("Tool 'demo'", f"'{_BAD}'")


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
@pytest.mark.parametrize("version", [_BAD, _BAD_ABOVE_PIN])
def test_refused_security_scanner_fails_the_run(
    tmp_path: Path,
    osv_on_path: Callable[[str], Path],
    version: str,
) -> None:
    """A known-bad osv-scanner fails; the output names the version to install.

    The install hint's range ("v2.6.0+") includes the refused release, and
    ``lintro install --upgrade`` cannot replace it yet (#3567), so the message
    names only the pinned version.

    Args:
        tmp_path: Directory holding the fake binary.
        osv_on_path: Writes the fake binary first on PATH.
        version: The refused release.
    """
    osv_on_path(version)

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
        f"Version {version} is refused",
        _ENTRY.reason,
        _ENTRY.reference,
    )
    assert_that(result.output).ends_with(
        f"(see {_ENTRY.reference}). Install 2.7.0, the version lintro tests.",
    )
    assert_that(result.output).does_not_contain("v2.6.0+", "lintro install")


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


_REFUSED = ToolSnapshot(
    name="demo",
    available=True,
    version="1.0.0",
    min_version="2.0.0",
    recommended_version="3.0.0",
    version_check_passed=False,
)


@pytest.mark.parametrize(
    ("known_bad", "runnable"),
    [(False, True), (True, False)],
    ids=["below-minimum-runs", "known-bad-stays-refused"],
)
def test_version_lag_override_never_runs_a_known_bad_release(
    monkeypatch: pytest.MonkeyPatch,
    known_bad: bool,
    runnable: bool,
) -> None:
    """``LINTRO_ALLOW_VERSION_LAG`` tolerates a lagging tool, not a bad release.

    Args:
        monkeypatch: Sets the override.
        known_bad: Whether the refused version is on the known-bad list.
        runnable: Whether the override lets it run.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "*")

    assert_that(
        snapshot_is_runnable(replace(_REFUSED, known_bad=known_bad)),
    ).is_equal_to(
        runnable,
    )


@pytest.mark.usefixtures("osv_known_bad")
def test_version_lag_override_does_not_run_a_known_bad_scanner(
    tmp_path: Path,
    osv_on_path: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the override set for every tool, a known-bad scanner still fails.

    Args:
        tmp_path: Directory holding the fake binary.
        osv_on_path: Writes the fake binary first on PATH.
        monkeypatch: Sets the override.
    """
    osv_on_path(_BAD_ABOVE_PIN)
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "*")

    result = verify_tool_version(OsvScannerPlugin().definition, cwd=tmp_path)

    assert result is not None  # narrow: a refused version returns a result
    assert_that(result.success).is_false()
    assert_that(result.unavailable_reason).contains(
        f"Version {_BAD_ABOVE_PIN} is refused",
    )


def test_version_lag_override_is_marked_in_the_result(
    monkeypatch: pytest.MonkeyPatch,
    warnings_logged: list[str],
) -> None:
    """A tool the override lets run carries a note and ``version_lag`` metadata.

    Args:
        monkeypatch: Replaces the snapshot probe and sets the override.
        warnings_logged: Captured warnings.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "demo")
    monkeypatch.setattr(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        lambda *_args, **_kwargs: _REFUSED,
    )
    ran = ToolResult(name="demo", success=True, issues_count=0)

    gate = verify_tool_version(ToolDefinition(name="demo", description="d"))
    marked = with_version_lag_note(ran)
    reset_upgrade_suggestions()

    assert_that(gate).is_none()
    assert_that(warnings_logged).is_length(1)
    assert_that(tool_notes(marked.metadata)).is_equal_to(
        [
            {
                "summary": "below minimum, allowed by override",
                "message": "demo 1.0.0 is below the minimum 2.0.0 and ran only "
                "because LINTRO_ALLOW_VERSION_LAG allows it",
            },
        ],
    )
    assert_that(
        serialize_tool_result(marked, action=Action.CHECK)["metadata"],
    ).contains_entry(
        {
            "version_lag": {
                "version": "1.0.0",
                "minimum": "2.0.0",
                "allowed_by": "LINTRO_ALLOW_VERSION_LAG",
            },
        },
    )
    assert_that(with_version_lag_note(ran)).is_equal_to(ran)


@pytest.mark.usefixtures("osv_known_bad")
@pytest.mark.parametrize("from_snapshot", [False, True], ids=["probe", "snapshot"])
def test_doctor_refuses_a_known_bad_release_like_the_run(
    osv_on_path: Callable[[str], Path],
    from_snapshot: bool,
) -> None:
    """``lintro doctor`` reports a known-bad release as incompatible, not OK.

    Args:
        osv_on_path: Writes the fake binary first on PATH.
        from_snapshot: Whether doctor reads a capability snapshot.
    """
    binary = osv_on_path(_BAD_ABOVE_PIN)
    tool = ManifestRegistry.load().get("osv_scanner")
    snapshot = (
        ToolSnapshot(
            name="osv_scanner",
            available=True,
            version=_BAD_ABOVE_PIN,
            binary_path=str(binary),
        )
        if from_snapshot
        else None
    )

    result = check_tool(tool=tool, context=RuntimeContext.detect(), snapshot=snapshot)

    assert_that(result.status).is_equal_to(ToolStatus.INCOMPATIBLE)
    assert_that(result.installed_version).is_equal_to(_BAD_ABOVE_PIN)
    assert_that(result.details).is_equal_to(
        f"known to give wrong results: {_ENTRY.reason} (see {_ENTRY.reference})",
    )
    assert_that(result.upgrade_hint).is_equal_to(_INSTALL_PIN)
    console = Console(record=True, width=300)
    _render_tool_line(console, result)
    rendered = console.export_text()
    assert_that(rendered).contains(f"\n         {_INSTALL_PIN}\n")
    assert_that(rendered).does_not_contain("Upgrade:", "v2.6.0+")


def test_known_bad_survives_the_snapshot_cache() -> None:
    """The snapshot cache keeps ``known_bad``; losing it let the override apply."""
    snapshot = replace(_REFUSED, known_bad=True, command=("demo",))

    assert_that(ToolSnapshot.from_dict(snapshot.to_dict()).known_bad).is_true()


def test_upgrade_suggestion_is_shown_once_per_tool_per_run(
    monkeypatch: pytest.MonkeyPatch,
    warnings_logged: list[str],
) -> None:
    """An older-than-pinned tool runs; the suggestion appears once per run.

    Args:
        monkeypatch: Replaces the snapshot probe.
        warnings_logged: Captured warnings.
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

    results = [verify_tool_version(definition) for _ in range(3)]
    reset_upgrade_suggestions()
    results.append(verify_tool_version(definition))

    assert_that(results).is_equal_to([None, None, None, None])
    expected = (
        "ruff 0.10.0 works but is older than 0.14.0, the version lintro tests. "
        "To upgrade, run: lintro install --upgrade ruff\n"
    )
    assert_that(warnings_logged).is_equal_to([expected, expected])


@pytest.mark.usefixtures("osv_known_bad")
def test_known_bad_below_minimum_reports_the_known_bad_reason(
    tmp_path: Path,
) -> None:
    """A release below the minimum and listed is refused as known-bad.

    The known-bad reason wins over "below minimum": it is the one no
    override may waive.

    Args:
        tmp_path: Directory for the fake binary.
    """
    binary = _fake_osv_scanner(tmp_path, _BAD_BELOW_MIN)

    info = check_tool_version("osv_scanner", [str(binary)])

    assert_that(info.version_check_passed).is_false()
    assert_that(info.known_bad).is_equal_to(_ENTRY_BELOW_MIN)
    assert_that(info.error_message).is_equal_to(
        f"Version {_BAD_BELOW_MIN} is refused because it is known to give wrong "
        f"results: {_ENTRY_BELOW_MIN.reason} (see {_ENTRY_BELOW_MIN.reference})",
    )
    assert_that(info.install_hint).is_equal_to(_INSTALL_PIN)


@pytest.mark.usefixtures("osv_known_bad")
@pytest.mark.parametrize("allowed", ["*", "osv_scanner"], ids=["all", "per-tool"])
def test_version_lag_override_does_not_run_a_known_bad_release_below_minimum(
    tmp_path: Path,
    osv_on_path: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    allowed: str,
) -> None:
    """The override tolerates lag, not a listed release that also lags.

    Args:
        tmp_path: Directory holding the fake binary.
        osv_on_path: Writes the fake binary first on PATH.
        monkeypatch: Sets the override.
        allowed: The override's value.
    """
    osv_on_path(_BAD_BELOW_MIN)
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", allowed)

    result = verify_tool_version(OsvScannerPlugin().definition, cwd=tmp_path)

    assert result is not None  # narrow: a refused version returns a result
    assert_that(result.success).is_false()
    assert_that(result.unavailable_reason).is_equal_to(
        f"security scanner did not run: Version {_BAD_BELOW_MIN} is refused "
        f"because it is known to give wrong results: {_ENTRY_BELOW_MIN.reason} "
        f"(see {_ENTRY_BELOW_MIN.reference}). {_INSTALL_PIN}",
    )


@pytest.mark.usefixtures("osv_known_bad")
def test_versions_command_names_only_the_pin_for_a_known_bad_release(
    osv_on_path: Callable[[str], Path],
) -> None:
    """``lintro versions`` and MCP ``lintro_versions`` share this hint.

    Both report ``ToolVersionInfo.install_hint``; its install range
    ("v2.6.0+") would include the refused release.

    Args:
        osv_on_path: Writes the fake binary first on PATH.
    """
    osv_on_path(_BAD)

    result = CliRunner().invoke(versions_command, ["--json"])

    assert_that(result.exit_code).is_equal_to(0)
    osv = json.loads(result.output)["osv_scanner"]
    assert_that(osv["install_hint"]).is_equal_to(_INSTALL_PIN)
    assert_that(osv["error_message"]).starts_with(f"Version {_BAD} is refused")


def _warm_osv_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: tuple[KnownBadVersion, ...],
) -> ToolSnapshot:
    """Probe osv-scanner once so its snapshot lands in the disk cache.

    Args:
        tmp_path: The cache root.
        monkeypatch: Sets the manifest's known-bad entries for the probe.
        entries: osv_scanner's known-bad entries while probing.

    Returns:
        The probed snapshot.
    """
    _list_osv_known_bad(monkeypatch, entries)
    return snapshots.get_tool_snapshot("osv_scanner", cache_root=tmp_path)


def _read_osv_cache_as_a_new_process(tmp_path: Path) -> ToolSnapshot:
    """Read osv-scanner's snapshot with the in-memory cache dropped.

    Args:
        tmp_path: The cache root.

    Returns:
        The snapshot, served from the disk cache when it is still valid.
    """
    with snapshots._cache_lock:
        snapshots._memory_caches.clear()
    return snapshots.get_tool_snapshot("osv_scanner", cache_root=tmp_path)


@pytest.mark.parametrize("old_format", [False, True], ids=["current", "old-format"])
def test_cached_verdict_is_rechecked_against_the_known_bad_list(
    tmp_path: Path,
    osv_on_path: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    old_format: bool,
) -> None:
    """A release listed after the cache was written is refused without a probe.

    The cache is keyed on lintro version, binary path and mtime, so it does
    not notice a new manifest entry, and a cache file written by older code
    has no ``known_bad`` field at all.

    Args:
        tmp_path: The cache root.
        osv_on_path: Writes the fake binary first on PATH.
        monkeypatch: Sets the manifest entries and the override.
        old_format: Whether the cached entry lacks ``known_bad``.
    """
    binary = osv_on_path(_BAD)
    warm = _warm_osv_cache(tmp_path, monkeypatch, ())
    if old_format:
        cache_file = snapshots._cache_file_path(cache_root=tmp_path)
        data = json.loads(cache_file.read_text())
        del data["snapshots"]["osv_scanner"]["known_bad"]
        cache_file.write_text(json.dumps(data))
    probes = _probe_count(binary)
    _list_osv_known_bad(monkeypatch, (_ENTRY,))
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "*")

    cached = _read_osv_cache_as_a_new_process(tmp_path)

    assert_that(warm.version_check_passed).is_true()
    assert_that(_probe_count(binary)).is_equal_to(probes)
    assert_that(cached.known_bad).is_true()
    assert_that(cached.probe_error).is_equal_to(
        f"Version {_BAD} is refused because it is known to give wrong results: "
        f"{_ENTRY.reason} (see {_ENTRY.reference})",
    )
    assert_that(snapshot_is_runnable(cached)).is_false()


def test_cached_known_bad_verdict_is_not_trusted_once_unlisted(
    tmp_path: Path,
    osv_on_path: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached refusal whose entry was removed is probed again, then runs.

    Args:
        tmp_path: The cache root.
        osv_on_path: Writes the fake binary first on PATH.
        monkeypatch: Sets the manifest entries.
    """
    binary = osv_on_path(_BAD)
    warm = _warm_osv_cache(tmp_path, monkeypatch, (_ENTRY,))
    probes = _probe_count(binary)
    _list_osv_known_bad(monkeypatch, ())

    cached = _read_osv_cache_as_a_new_process(tmp_path)

    assert_that(warm.known_bad).is_true()
    assert_that(_probe_count(binary)).is_greater_than(probes)
    assert_that(cached.known_bad).is_false()
    assert_that(snapshot_is_runnable(cached)).is_true()


@pytest.mark.parametrize(
    ("version", "cached_pass", "kept"),
    [("2.6.4", True, True), ("2.5.0", True, False), ("2.6.4", False, False)],
    ids=["agrees", "minimum-raised-since", "minimum-lowered-since"],
)
def test_cached_verdict_is_rechecked_against_the_minimum(
    version: str,
    cached_pass: bool,
    kept: bool,
) -> None:
    """A cached pass or refusal that the current minimum (2.6.0) contradicts is dropped.

    Dropped entries are probed again instead of being served from the cache.

    Args:
        version: The cached osv-scanner version.
        cached_pass: The cached minimum verdict.
        kept: Whether the cached entry is still served.
    """
    cached = ToolSnapshot(
        name="osv_scanner",
        available=True,
        version=version,
        version_check_passed=cached_pass,
        min_version="2.6.0",
    )

    assert_that(snapshots._recheck_cached_verdict(cached)).is_equal_to(
        cached if kept else None,
    )
