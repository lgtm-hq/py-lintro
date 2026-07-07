"""Tests for the dependency policy engine."""

from __future__ import annotations

from assertpy import assert_that

from lintro.config.deps_config import DepsConfig, DepsPolicy, PackageException
from lintro.deps.models import Dependency, Ecosystem, VersionSpecType
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
