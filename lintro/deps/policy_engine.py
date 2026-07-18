"""Policy engine that validates dependencies against version-spec rules."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from lintro.config.deps_config import DepsConfig, DepsPolicy, PackageException
from lintro.deps.models import Dependency, VersionSpecType, VersionViolation

__all__ = ["PolicyEngine", "PolicyRules"]


@dataclass(frozen=True)
class PolicyRules:
    """Resolved rule set applied to a dependency.

    Attributes:
        allowed_types: Version-spec types that satisfy the policy.
        disallowed_types: Version-spec types that always violate the policy.
        require_upper_bound: Whether an upper bound is mandatory.
    """

    allowed_types: frozenset[VersionSpecType]
    disallowed_types: frozenset[VersionSpecType]
    require_upper_bound: bool


_ALL_TYPES: frozenset[VersionSpecType] = frozenset(VersionSpecType)

_PRESETS: dict[DepsPolicy, PolicyRules] = {
    DepsPolicy.STRICT: PolicyRules(
        allowed_types=frozenset({VersionSpecType.EXACT}),
        disallowed_types=_ALL_TYPES - {VersionSpecType.EXACT},
        require_upper_bound=True,
    ),
    DepsPolicy.FLEXIBLE: PolicyRules(
        allowed_types=frozenset(
            {
                VersionSpecType.EXACT,
                VersionSpecType.TILDE,
                VersionSpecType.CARET,
                VersionSpecType.RANGE,
                VersionSpecType.WILDCARD,
            },
        ),
        disallowed_types=frozenset(
            {VersionSpecType.ANY, VersionSpecType.UNBOUNDED},
        ),
        require_upper_bound=True,
    ),
    DepsPolicy.LOOSE: PolicyRules(
        allowed_types=_ALL_TYPES,
        disallowed_types=frozenset({VersionSpecType.ANY}),
        require_upper_bound=False,
    ),
}


class PolicyEngine:
    """Apply version-spec policy rules and generate violations."""

    def __init__(self, config: DepsConfig) -> None:
        """Initialize the engine.

        Args:
            config: Resolved dependency policy configuration.
        """
        self.config = config
        self._base_rules = self._resolve_base_rules(config)

    def get_preset_rules(self, preset: DepsPolicy) -> PolicyRules:
        """Return the rule set for a named policy preset.

        Args:
            preset: The policy preset to look up.

        Returns:
            PolicyRules: The preset's rules (flexible when ``custom``).
        """
        return _PRESETS.get(preset, _PRESETS[DepsPolicy.FLEXIBLE])

    def validate(self, dependencies: list[Dependency]) -> list[VersionViolation]:
        """Validate dependencies against the active policy.

        Args:
            dependencies: Parsed dependencies to check.

        Returns:
            list[VersionViolation]: One violation per failing rule.
        """
        violations: list[VersionViolation] = []
        for dep in dependencies:
            violations.extend(self._validate_one(dep))
        return violations

    def _validate_one(self, dep: Dependency) -> list[VersionViolation]:
        """Validate a single dependency.

        Args:
            dep: The dependency to check.

        Returns:
            list[VersionViolation]: Violations raised for this dependency.
        """
        rules = self._rules_for(dep)
        violations: list[VersionViolation] = []

        if dep.spec_type in rules.disallowed_types:
            violations.append(
                VersionViolation(
                    dependency=dep,
                    rule="disallowed_type",
                    message=self._disallowed_message(dep.spec_type),
                ),
            )
        elif dep.spec_type not in rules.allowed_types:
            violations.append(
                VersionViolation(
                    dependency=dep,
                    rule="allowed_types",
                    message=(f"Spec type '{dep.spec_type}' is not allowed by policy"),
                ),
            )

        if rules.require_upper_bound and not dep.has_upper_bound:
            already_flagged = any(
                v.rule in {"disallowed_type", "allowed_types"} for v in violations
            )
            if not already_flagged:
                violations.append(
                    VersionViolation(
                        dependency=dep,
                        rule="require_upper_bound",
                        message="No upper bound",
                    ),
                )

        return violations

    def _rules_for(self, dep: Dependency) -> PolicyRules:
        """Resolve rules for a dependency, honoring package exceptions.

        Args:
            dep: The dependency being validated.

        Returns:
            PolicyRules: The effective rule set for this dependency.
        """
        exception = self._matching_exception(dep.name)
        if exception is None:
            return self._base_rules

        allowed = self._base_rules.allowed_types
        disallowed = self._base_rules.disallowed_types
        if exception.allowed_types:
            allowed = frozenset(
                self._parse_types(exception.allowed_types),
            )
            disallowed = _ALL_TYPES - allowed

        require_upper = self._base_rules.require_upper_bound
        if exception.require_upper_bound is not None:
            require_upper = exception.require_upper_bound

        return PolicyRules(
            allowed_types=allowed,
            disallowed_types=disallowed,
            require_upper_bound=require_upper,
        )

    def _matching_exception(self, name: str) -> PackageException | None:
        """Find the first package exception matching ``name``.

        Args:
            name: Package name to match against exception globs.

        Returns:
            PackageException | None: The matching exception, if any.
        """
        for exception in self.config.exceptions:
            if fnmatch.fnmatch(name.lower(), exception.package.lower()):
                return exception
        return None

    def _resolve_base_rules(self, config: DepsConfig) -> PolicyRules:
        """Resolve the base rule set from the config's policy.

        Args:
            config: Dependency policy configuration.

        Returns:
            PolicyRules: Rules from the preset, or custom fields when the
            policy is ``custom``.
        """
        if config.policy is DepsPolicy.CUSTOM:
            allowed = frozenset(self._parse_types(config.allowed_types))
            disallowed = frozenset(self._parse_types(config.disallowed_types))
            return PolicyRules(
                allowed_types=allowed,
                disallowed_types=disallowed,
                require_upper_bound=config.require_upper_bound,
            )
        return self.get_preset_rules(config.policy)

    @staticmethod
    def _parse_types(names: list[str]) -> set[VersionSpecType]:
        """Convert type name strings into :class:`VersionSpecType` values.

        Args:
            names: Version-spec type names (unknown names are ignored).

        Returns:
            set[VersionSpecType]: The parsed types.
        """
        parsed: set[VersionSpecType] = set()
        for name in names:
            try:
                parsed.add(VersionSpecType(name.strip().lower()))
            except ValueError:
                continue
        return parsed

    @staticmethod
    def _disallowed_message(spec_type: VersionSpecType) -> str:
        """Return a friendly message for a disallowed spec type.

        Args:
            spec_type: The disallowed spec type.

        Returns:
            str: Human-readable violation message.
        """
        messages = {
            VersionSpecType.ANY: "No constraint",
            VersionSpecType.UNBOUNDED: "No upper bound",
            VersionSpecType.WILDCARD: "Wildcard spec not allowed",
            VersionSpecType.RANGE: "Range spec not allowed",
            VersionSpecType.CARET: "Caret spec not allowed",
            VersionSpecType.TILDE: "Tilde spec not allowed",
            VersionSpecType.EXACT: "Exact spec not allowed",
        }
        return messages.get(spec_type, f"Spec type '{spec_type}' not allowed")
