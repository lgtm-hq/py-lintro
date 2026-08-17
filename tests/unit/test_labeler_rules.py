"""Classification tests for `.github/labeler.yml` path rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LABELER = _REPO_ROOT / ".github" / "labeler.yml"


def _load_labeler() -> dict[str, Any]:
    """Load the labeler config as a mapping of label name to glob list."""
    data = yaml.safe_load(_LABELER.read_text(encoding="utf-8"))
    assert_that(data).is_instance_of(dict)
    return cast(dict[str, Any], data)


def _globs_for(*, label: str) -> list[str]:
    """Return any-glob-to-any-file patterns for one label.

    Args:
        label: Labeler top-level key.

    Returns:
        Glob patterns configured for that label.
    """
    rules = _load_labeler()[label]
    return list(rules[0]["changed-files"][0]["any-glob-to-any-file"])


def _expand_braces(pattern: str) -> list[str]:
    """Expand a single `{a,b}` brace group used by some labeler globs.

    Args:
        pattern: Minimatch-style glob, optionally containing one brace group.

    Returns:
        One or more concrete globs after brace expansion.
    """
    match = re.search(r"\{([^{}]+)\}", pattern)
    if match is None:
        return [pattern]
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    return [f"{prefix}{choice}{suffix}" for choice in match.group(1).split(",")]


def _glob_to_regex(*, pattern: str) -> re.Pattern[str]:
    """Compile a minimatch-style glob used by ``actions/labeler``.

    Args:
        pattern: Expanded glob without brace groups.

    Returns:
        Anchored regular expression matching the same paths.
    """
    parts = pattern.split("**")
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if index:
            rendered.append(".*")
        escaped = ""
        for char in part:
            if char == "*":
                escaped += "[^/]*"
            elif char == "?":
                escaped += "[^/]"
            else:
                escaped += re.escape(char)
        rendered.append(escaped)
    return re.compile(f"^{''.join(rendered)}$")


def _path_matches(*, path: str, pattern: str) -> bool:
    """Return whether a repo-relative path matches a labeler glob.

    Args:
        path: Repository-relative POSIX path.
        pattern: Minimatch-style glob from ``labeler.yml``.

    Returns:
        True when the path matches the glob.
    """
    if pattern.startswith("!"):
        return False
    return any(
        _glob_to_regex(pattern=expanded).match(path) is not None
        for expanded in _expand_braces(pattern)
    )


def _labels_for(*, path: str) -> set[str]:
    """Return every labeler label whose globs match ``path``.

    Args:
        path: Repository-relative POSIX path.

    Returns:
        Set of label names that would apply to the path.
    """
    matched: set[str] = set()
    for label in _load_labeler():
        if any(
            _path_matches(path=path, pattern=pattern)
            for pattern in _globs_for(label=label)
        ):
            matched.add(label)
    return matched


@pytest.mark.parametrize(
    ("path", "expected", "forbidden"),
    [
        ("tests/unit/test_labeler_rules.py", {"testing"}, {"enhancement"}),
        ("pyproject.toml", {"dependencies", "release"}, set()),
        ("renovate.json", {"maintenance", "infrastructure"}, set()),
        ("npm/lintro/package.json", {"release"}, set()),
        ("package.json", set(), {"release"}),
        ("apps/site/package.json", set(), {"release"}),
        (".node-version", set(), {"release"}),
        ("scripts/ci/finalize-version-pr.py", {"ci", "release"}, set()),
        ("scripts/ci/check-release-version-skew.py", {"ci", "release"}, set()),
        (
            ".github/workflows/docker-tools-publish.yml",
            {"ci", "release"},
            set(),
        ),
        (
            "lintro/tools/definitions/bandit.py",
            {"enhancement", "security"},
            set(),
        ),
    ],
)
def test_labeler_classifies_representative_paths(
    path: str,
    expected: set[str],
    forbidden: set[str],
) -> None:
    """Labeler globs must classify representative diffs as intended.

    Args:
        path: Repository-relative POSIX path to classify.
        expected: Labels that must apply.
        forbidden: Labels that must not apply.
    """
    labels = _labels_for(path=path)
    if expected:
        assert_that(labels).contains(*expected)
    else:
        assert_that(labels.intersection({"release"})).is_empty()
    assert_that(labels.intersection(forbidden)).is_empty()
