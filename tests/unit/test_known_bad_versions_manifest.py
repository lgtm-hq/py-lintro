"""Guard: the version lintro pins is never refused by its own rules (#3556).

For every tool in ``lintro/tools/manifest.src.json``, the pinned version must
be at or above the tool's ``min_version`` and must not match any entry in its
``known_bad_versions`` list. Each known-bad entry must also be well formed, so
none is silently dropped when the manifest loads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from lintro.tools.core.manifest_models import KnownBadVersion
from lintro.tools.core.tool_registry import ManifestRegistry
from lintro.tools.core.version_parsing import compare_versions, find_known_bad_version

_SRC = Path(__file__).resolve().parents[2] / "lintro" / "tools" / "manifest.src.json"
_ENTRIES: list[dict[str, Any]] = json.loads(_SRC.read_text(encoding="utf-8"))["tools"]


def pin_problems(
    pin: str,
    minimum: str | None,
    known_bad: tuple[KnownBadVersion, ...],
) -> list[str]:
    """List the ways a tool's own rules would refuse its pinned version.

    Args:
        pin: The version lintro pins and tests.
        minimum: The manifest ``min_version``, or None when the tool sets none.
        known_bad: The tool's known-bad entries.

    Returns:
        One message per problem; empty when the pin is accepted.
    """
    problems: list[str] = []
    if minimum is not None and compare_versions(pin, minimum) < 0:
        problems.append(f"pin {pin} is below min_version {minimum}")
    entry = find_known_bad_version(pin, known_bad)
    if entry is not None:
        problems.append(f"pin {pin} is on the known-bad list ({entry.versions})")
    return problems


@pytest.mark.parametrize(
    ("pin", "minimum", "specifier", "expected"),
    [
        ("2.7.0", "2.6.0", "==2.7.1", []),
        ("2.7.0", "2.8.0", "==2.7.1", ["pin 2.7.0 is below min_version 2.8.0"]),
        (
            "2.7.0",
            None,
            ">=2.7.0,<2.7.1",
            ["pin 2.7.0 is on the known-bad list (>=2.7.0,<2.7.1)"],
        ),
    ],
    ids=["accepted", "below-minimum", "known-bad"],
)
def test_pin_problems_detects_a_refused_pin(
    pin: str,
    minimum: str | None,
    specifier: str,
    expected: list[str],
) -> None:
    """The guard's check reports exactly the rule a pin breaks.

    Args:
        pin: Pinned version under test.
        minimum: Minimum version under test.
        specifier: A known-bad specifier.
        expected: Problems the check must report.
    """
    entry = KnownBadVersion(versions=specifier, reason="r", reference="ref")

    assert_that(pin_problems(pin, minimum, (entry,))).is_equal_to(expected)


@pytest.mark.parametrize("entry", _ENTRIES, ids=[e["name"] for e in _ENTRIES])
def test_pinned_version_is_never_refused(entry: dict[str, Any]) -> None:
    """The pin meets the tool's minimum and is not on its known-bad list.

    Args:
        entry: One tool entry from manifest.src.json.
    """
    tool = ManifestRegistry.load().get(entry["name"])

    problems = pin_problems(
        tool.version,
        entry.get("min_version"),
        tool.known_bad_versions,
    )

    assert_that(problems).described_as(entry["name"]).is_empty()


@pytest.mark.parametrize("entry", _ENTRIES, ids=[e["name"] for e in _ENTRIES])
def test_known_bad_entries_are_well_formed(entry: dict[str, Any]) -> None:
    """Every entry parses as a specifier and carries a reason and a reference.

    Args:
        entry: One tool entry from manifest.src.json.
    """
    raw = entry.get("known_bad_versions", [])
    loaded = ManifestRegistry.load().get(entry["name"]).known_bad_versions

    assert_that(loaded).described_as(f"{entry['name']}: entries dropped").is_length(
        len(raw),
    )
    for item in loaded:
        try:
            SpecifierSet(item.versions)
        except InvalidSpecifier:
            pytest.fail(f"{entry['name']}: bad specifier {item.versions!r}")
