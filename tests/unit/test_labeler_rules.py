"""Classification tests for `.github/labeler.yml` path rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that
from pathspec import GitIgnoreSpec

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
        Glob patterns configured for that label, including ``!`` excludes.
    """
    rules = _load_labeler()[label]
    return list(rules[0]["changed-files"][0]["any-glob-to-any-file"])


def _labels_for(*, path: str) -> set[str]:
    """Return every labeler label whose globs match ``path``.

    Matching uses ``pathspec.GitIgnoreSpec`` so ``**`` and ``!`` follow the
    same gitignore-style rules this repo already prefers over a hand-rolled
    minimatch dialect.

    Args:
        path: Repository-relative POSIX path.

    Returns:
        Exact set of label names that apply to the path.
    """
    matched: set[str] = set()
    for label in _load_labeler():
        spec = GitIgnoreSpec.from_lines(_globs_for(label=label))
        if spec.match_file(path):
            matched.add(label)
    return matched


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/unit/test_labeler_rules.py", {"testing"}),
        ("pytest.ini", {"testing"}),
        ("test_samples/README.md", {"testing", "documentation"}),
        ("pyproject.toml", {"dependencies", "release"}),
        ("renovate.json", {"maintenance", "infrastructure"}),
        ("CHANGELOG.md", {"documentation", "release"}),
        ("lintro/__init__.py", {"enhancement", "release"}),
        ("npm/lintro/package.json", {"release"}),
        ("package.json", set()),
        ("apps/site/package.json", set()),
        (".node-version", set()),
        ("scripts/ci/finalize-version-pr.py", {"ci", "release"}),
        ("scripts/ci/check-release-version-skew.py", {"ci", "release"}),
        ("scripts/ci/classify-release-tag.py", {"ci", "release"}),
        ("scripts/ci/testing/lintro-report-generate.sh", {"ci", "testing"}),
        (
            ".github/workflows/docker-tools-publish.yml",
            {"ci", "release"},
        ),
        (".github/workflows/docker-ci.yml", {"ci"}),
        (".allstar/allstar.yaml", {"infrastructure"}),
        (
            "lintro/tools/definitions/bandit.py",
            {"enhancement", "security"},
        ),
        (".github/PULL_REQUEST_TEMPLATE.md", set()),
    ],
)
def test_labeler_classifies_representative_paths(
    path: str,
    expected: set[str],
) -> None:
    """Labeler globs must classify representative diffs as intended.

    Args:
        path: Repository-relative POSIX path to classify.
        expected: Exact label set that must apply.
    """
    assert_that(_labels_for(path=path)).is_equal_to(expected)
