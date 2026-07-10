"""Tests for the version specification analyzer."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.deps.models import Ecosystem, VersionSpecType
from lintro.deps.version_analyzer import VersionAnalyzer


@pytest.fixture
def analyzer() -> VersionAnalyzer:
    """Provide a VersionAnalyzer instance.

    Returns:
        VersionAnalyzer: A fresh analyzer.
    """
    return VersionAnalyzer()


@pytest.mark.parametrize(
    ("spec", "ecosystem", "expected"),
    [
        ("==1.2.3", Ecosystem.PYTHON, VersionSpecType.EXACT),
        ("~=1.2.3", Ecosystem.PYTHON, VersionSpecType.TILDE),
        ("^1.2.3", Ecosystem.PYTHON, VersionSpecType.CARET),
        (">=1.2.0,<2.0.0", Ecosystem.PYTHON, VersionSpecType.RANGE),
        (">=1.0.0", Ecosystem.PYTHON, VersionSpecType.UNBOUNDED),
        ("1.2.*", Ecosystem.PYTHON, VersionSpecType.WILDCARD),
        ("*", Ecosystem.PYTHON, VersionSpecType.ANY),
        ("", Ecosystem.PYTHON, VersionSpecType.ANY),
        ("1.2.3", Ecosystem.NPM, VersionSpecType.EXACT),
        ("~1.2.3", Ecosystem.NPM, VersionSpecType.TILDE),
        ("^5.0.0", Ecosystem.NPM, VersionSpecType.CARET),
        ("1.x", Ecosystem.NPM, VersionSpecType.WILDCARD),
        (">=1.0.0 <2.0.0", Ecosystem.NPM, VersionSpecType.RANGE),
        ("latest", Ecosystem.NPM, VersionSpecType.ANY),
        ("1.0.100", Ecosystem.CARGO, VersionSpecType.CARET),
        ("=0.8.5", Ecosystem.CARGO, VersionSpecType.EXACT),
        ("~1.2", Ecosystem.CARGO, VersionSpecType.TILDE),
        ("*", Ecosystem.CARGO, VersionSpecType.ANY),
        ("!=1.2.3", Ecosystem.PYTHON, VersionSpecType.UNBOUNDED),
        ("==1.2.3 || ==2.0.0", Ecosystem.NPM, VersionSpecType.RANGE),
    ],
)
def test_classify(
    analyzer: VersionAnalyzer,
    spec: str,
    ecosystem: Ecosystem,
    expected: VersionSpecType,
) -> None:
    """Classification returns the expected spec type per ecosystem.

    Args:
        analyzer: The analyzer under test.
        spec: Raw version spec.
        ecosystem: Ecosystem for the spec.
        expected: Expected classification.
    """
    assert_that(analyzer.classify(spec, ecosystem)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("spec", "ecosystem", "expected"),
    [
        ("==1.2.3", Ecosystem.PYTHON, True),
        ("~=1.2.3", Ecosystem.PYTHON, True),
        ("^1.2.3", Ecosystem.PYTHON, True),
        (">=1.0,<2.0", Ecosystem.PYTHON, True),
        ("<2.0", Ecosystem.PYTHON, True),
        (">=1.0.0", Ecosystem.PYTHON, False),
        (">1.0", Ecosystem.PYTHON, False),
        ("*", Ecosystem.PYTHON, False),
        ("1.2.*", Ecosystem.PYTHON, True),
        ("1.0.100", Ecosystem.CARGO, True),
    ],
)
def test_has_upper_bound(
    analyzer: VersionAnalyzer,
    spec: str,
    ecosystem: Ecosystem,
    expected: bool,
) -> None:
    """Upper-bound detection matches expectations.

    Args:
        analyzer: The analyzer under test.
        spec: Raw version spec.
        ecosystem: Ecosystem for the spec.
        expected: Whether an upper bound is expected.
    """
    assert_that(analyzer.has_upper_bound(spec, ecosystem)).is_equal_to(expected)
