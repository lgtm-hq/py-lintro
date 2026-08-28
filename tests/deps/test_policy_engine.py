"""Tests for the dependency policy engine."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.config.deps_config import DepsConfig, DepsPolicy, PackageException
from lintro.deps.models import Dependency, Ecosystem, VersionSpecType
from lintro.deps.parsers import parse_file
from lintro.deps.policy_engine import PolicyEngine


def _dep(
    name: str,
    spec: str,
    spec_type: VersionSpecType,
    has_upper: bool,
) -> Dependency:
    """Build a Dependency for testing.

    Args:
        name: Package name.
        spec: Raw version spec.
        spec_type: Classified type.
        has_upper: Whether it caps the upper bound.

    Returns:
        Dependency: The constructed dependency.
    """
    return Dependency(
        name=name,
        version_spec=spec,
        spec_type=spec_type,
        ecosystem=Ecosystem.PYTHON,
        has_upper_bound=has_upper,
        file="pyproject.toml",
    )


def test_flexible_flags_unbounded_and_any() -> None:
    """Flexible policy flags unbounded and any specs only."""
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.FLEXIBLE))
    deps = [
        _dep("caretpkg", "^1.0", VersionSpecType.CARET, True),
        _dep("unb", ">=1.0", VersionSpecType.UNBOUNDED, False),
        _dep("anypkg", "*", VersionSpecType.ANY, False),
    ]
    violations = engine.validate(deps)
    flagged = {v.dependency.name for v in violations}
    assert_that(flagged).is_equal_to({"unb", "anypkg"})


def test_strict_requires_exact() -> None:
    """Strict policy flags anything that is not an exact pin."""
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.STRICT))
    deps = [
        _dep("exactpkg", "==1.0", VersionSpecType.EXACT, True),
        _dep("caretpkg", "^1.0", VersionSpecType.CARET, True),
    ]
    violations = engine.validate(deps)
    flagged = {v.dependency.name for v in violations}
    assert_that(flagged).is_equal_to({"caretpkg"})


def test_loose_only_flags_any() -> None:
    """Loose policy flags only fully unconstrained specs."""
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.LOOSE))
    deps = [
        _dep("unb", ">=1.0", VersionSpecType.UNBOUNDED, False),
        _dep("anypkg", "*", VersionSpecType.ANY, False),
    ]
    violations = engine.validate(deps)
    flagged = {v.dependency.name for v in violations}
    assert_that(flagged).is_equal_to({"anypkg"})


def test_no_double_flag_for_disallowed_unbounded() -> None:
    """An unbounded spec yields a single violation, not two."""
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.FLEXIBLE))
    deps = [_dep("unb", ">=1.0", VersionSpecType.UNBOUNDED, False)]
    violations = engine.validate(deps)
    assert_that(violations).is_length(1)


def test_package_exception_relaxes_policy() -> None:
    """A matching exception overrides the base policy for a package."""
    config = DepsConfig(
        policy=DepsPolicy.STRICT,
        exceptions=[
            PackageException(package="pytest", allowed_types=["tilde", "caret"]),
        ],
    )
    engine = PolicyEngine(config)
    deps = [_dep("pytest", "~=8.1", VersionSpecType.TILDE, True)]
    violations = engine.validate(deps)
    assert_that(violations).is_empty()


def test_package_exception_glob_match() -> None:
    """Glob-based exceptions match by pattern."""
    config = DepsConfig(
        policy=DepsPolicy.STRICT,
        exceptions=[PackageException(package="aws-*", allowed_types=["caret"])],
    )
    engine = PolicyEngine(config)
    deps = [_dep("aws-sdk", "^1.0", VersionSpecType.CARET, True)]
    assert_that(engine.validate(deps)).is_empty()


def test_package_exception_glob_matches_pep503_normalized_name() -> None:
    """Exception globs match PEP 503 names (``_`` vs ``-``)."""
    config = DepsConfig(
        policy=DepsPolicy.STRICT,
        exceptions=[
            PackageException(
                package="google-cloud-*",
                allowed_types=["caret"],
            ),
        ],
    )
    engine = PolicyEngine(config)
    deps = [
        _dep("google_cloud_storage", "^1.0", VersionSpecType.CARET, True),
    ]
    assert_that(engine.validate(deps)).is_empty()


def test_custom_policy_uses_explicit_fields() -> None:
    """Custom policy honors explicit allowed/disallowed fields."""
    config = DepsConfig(
        policy=DepsPolicy.CUSTOM,
        allowed_types=["exact"],
        disallowed_types=["caret", "any", "unbounded"],
        require_upper_bound=True,
    )
    engine = PolicyEngine(config)
    deps = [
        _dep("exactpkg", "==1.0", VersionSpecType.EXACT, True),
        _dep("caretpkg", "^1.0", VersionSpecType.CARET, True),
    ]
    flagged = {v.dependency.name for v in engine.validate(deps)}
    assert_that(flagged).is_equal_to({"caretpkg"})


def test_get_preset_rules_custom_falls_back_to_flexible() -> None:
    """Requesting preset rules for custom returns the flexible ruleset."""
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.FLEXIBLE))
    rules = engine.get_preset_rules(DepsPolicy.CUSTOM)
    assert_that(rules.require_upper_bound).is_true()


def test_unknown_custom_type_name_is_rejected() -> None:
    """A typo in a custom type list fails closed instead of dropping a rule."""
    config = DepsConfig(
        policy=DepsPolicy.CUSTOM,
        allowed_types=["exact"],
        disallowed_types=["unbounded", "unbouded"],
    )
    assert_that(PolicyEngine).raises(ValueError).when_called_with(config).contains(
        "unbouded",
    )


def test_unknown_exception_type_name_is_rejected() -> None:
    """A typo in a package exception's allowed_types fails closed."""
    config = DepsConfig(
        policy=DepsPolicy.STRICT,
        exceptions=[PackageException(package="boto3", allowed_types=["exac"])],
    )
    assert_that(PolicyEngine).raises(ValueError).when_called_with(config).contains(
        "exac",
    )


def _deps_from_manifest(tmp_path: Path, body: str, name: str) -> list[Dependency]:
    """Parse a manifest snippet through the real parser pipeline.

    Args:
        tmp_path: Temporary directory fixture.
        body: Manifest contents.
        name: Manifest file name.

    Returns:
        list[Dependency]: Dependencies classified by ``VersionAnalyzer``.
    """
    manifest = tmp_path / name
    manifest.write_text(body)
    return parse_file(manifest)


def test_pipeline_flexible_flags_unbounded_npm_alternatives(tmp_path: Path) -> None:
    """A real package.json flows through parser, analyzer and engine.

    Args:
        tmp_path: Temporary directory fixture.
    """
    deps = _deps_from_manifest(
        tmp_path,
        json.dumps(
            {
                "dependencies": {
                    "mixed": ">=1.0.0 || >=2.0.0 <3.0.0",
                    "wildcard-alt": "1.2.* || >=3.0.0",
                    "hyphen": "1.2.3 - 2.3.4",
                    "dual-major": "^1.0.0 || ^2.0.0",
                },
            },
        ),
        "package.json",
    )
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.FLEXIBLE))
    flagged = {v.dependency.name for v in engine.validate(deps)}
    assert_that(flagged).is_equal_to({"mixed", "wildcard-alt"})


def test_pipeline_strict_flags_npm_hyphen_range(tmp_path: Path) -> None:
    """A hyphen range is a range, so strict policy flags it as non-exact.

    Args:
        tmp_path: Temporary directory fixture.
    """
    deps = _deps_from_manifest(
        tmp_path,
        json.dumps({"dependencies": {"hyphen": "1.2.3 - 2.3.4"}}),
        "package.json",
    )
    assert_that(deps[0].spec_type).is_equal_to(VersionSpecType.RANGE)
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.STRICT))
    assert_that(engine.validate(deps)).is_length(1)


def test_pipeline_pyproject_groups_are_policed(tmp_path: Path) -> None:
    """PEP 735 group members reach the policy engine.

    Args:
        tmp_path: Temporary directory fixture.
    """
    deps = _deps_from_manifest(
        tmp_path,
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'dependencies = ["requests==2.31.0"]',
                "[dependency-groups]",
                'dev = ["pytest>=8.0"]',
            ],
        ),
        "pyproject.toml",
    )
    engine = PolicyEngine(DepsConfig(policy=DepsPolicy.FLEXIBLE))
    flagged = {v.dependency.name for v in engine.validate(deps)}
    assert_that(flagged).is_equal_to({"pytest"})
