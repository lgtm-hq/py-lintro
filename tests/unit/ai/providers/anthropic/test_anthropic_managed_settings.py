"""The Anthropic managed-settings path table and its override (#2308).

``claude_auth`` reaches this helper on every ``--bare`` decision, and the
per-platform paths are what an enterprise ``apiKeyHelper`` is discovered
through. The auth suite always sets the override to a temp file, so without
these tests a typo in the table — or a regression of the blank-override
fallthrough — would never fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.providers.anthropic.metadata import (
    ANTHROPIC_MANAGED_SETTINGS_ENV,
    ANTHROPIC_MANAGED_SETTINGS_PATHS,
    managed_settings_path,
)


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the documented override so the no-kwarg cases match production.

    ``managed_settings_path`` takes the override as an argument and never reads
    the environment itself — :mod:`lintro.ai.providers.claude_auth` is what
    passes it — but a developer with the variable exported would otherwise be
    testing a different default than CI.

    Args:
        monkeypatch: Pytest environment patcher.
    """
    monkeypatch.delenv(ANTHROPIC_MANAGED_SETTINGS_ENV, raising=False)


def test_the_helper_does_not_read_the_environment_itself(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The override arrives as an argument, so the caller owns the env read.

    Args:
        monkeypatch: Pytest environment patcher.
        tmp_path: Per-test temporary directory.
    """
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv(ANTHROPIC_MANAGED_SETTINGS_ENV, str(tmp_path / "ignored.json"))

    assert_that(managed_settings_path()).is_equal_to(
        Path(ANTHROPIC_MANAGED_SETTINGS_PATHS["darwin"]),
    )


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "/Library/Application Support/ClaudeCode/managed-settings.json"),
        ("win32", "C:/ProgramData/ClaudeCode/managed-settings.json"),
        ("linux", "/etc/claude-code/managed-settings.json"),
        ("linux2", "/etc/claude-code/managed-settings.json"),
    ],
)
def test_documented_platform_paths(
    platform: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each documented platform resolves to Claude Code's own location.

    Args:
        platform: ``sys.platform`` value to simulate.
        expected: Path Claude Code documents for it.
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setattr("sys.platform", platform)

    assert_that(managed_settings_path()).is_equal_to(Path(expected))


def test_unknown_platform_has_no_managed_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform Claude Code does not document yields no candidate.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setattr("sys.platform", "freebsd14")

    assert_that(managed_settings_path()).is_none()


def test_override_wins_over_the_platform_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The env override relocates the file a sandbox cannot place.

    Args:
        monkeypatch: Pytest attribute patcher.
        tmp_path: Per-test temporary directory.
    """
    monkeypatch.setattr("sys.platform", "darwin")
    target = tmp_path / "managed-settings.json"

    assert_that(managed_settings_path(override=f"  {target}  ")).is_equal_to(target)


@pytest.mark.parametrize("override", ["", "   ", None])
def test_blank_override_falls_through_to_the_platform_table(
    override: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty export must not silently disable the lookup.

    Args:
        override: Blank or absent override value.
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setattr("sys.platform", "darwin")

    assert_that(managed_settings_path(override=override)).is_equal_to(
        Path(ANTHROPIC_MANAGED_SETTINGS_PATHS["darwin"]),
    )
