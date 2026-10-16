"""Policy engine that evaluates package licenses against a configured policy."""

from __future__ import annotations

from license_expression import (
    AND,
    OR,
    LicenseExpression,
    LicenseSymbol,
    LicenseWithExceptionSymbol,
)

from lintro.config.licenses_config import LicensesConfig
from lintro.licenses.models import (
    LicenseResult,
    LicenseStatus,
    PackageLicense,
)
from lintro.licenses.spdx import (
    PERMISSIVE_LICENSES,
    RESTRICTED_LICENSES,
    STRONG_COPYLEFT_LICENSES,
    WEAK_COPYLEFT_LICENSES,
    normalize_to_spdx,
    parse_license_expression,
)


def get_preset_rules(preset: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return the (allowed, denied) SPDX sets for a named preset.

    Args:
        preset: One of ``permissive``, ``copyleft-ok``, ``strict``, or
            ``custom``.

    Returns:
        tuple[frozenset[str], frozenset[str]]: Allowed and denied SPDX id sets.
    """
    if preset == "permissive":
        return (
            PERMISSIVE_LICENSES,
            STRONG_COPYLEFT_LICENSES | WEAK_COPYLEFT_LICENSES | RESTRICTED_LICENSES,
        )
    if preset == "copyleft-ok":
        return (
            PERMISSIVE_LICENSES | WEAK_COPYLEFT_LICENSES,
            STRONG_COPYLEFT_LICENSES | RESTRICTED_LICENSES,
        )
    if preset == "strict":
        # Strict: nothing is allowed by default; every license must be
        # explicitly allow-listed via config.
        return (
            frozenset(),
            STRONG_COPYLEFT_LICENSES | WEAK_COPYLEFT_LICENSES | RESTRICTED_LICENSES,
        )
    # "custom": rely entirely on config allowed/denied lists.
    return (frozenset(), frozenset())


class LicensePolicyEngine:
    """Evaluate package licenses against an allow/deny policy.

    The engine combines a named preset with any explicit ``allowed`` and
    ``denied`` SPDX identifiers from the configuration, applies per-package
    exceptions, and classifies unknown licenses according to
    ``unknown_policy``. Compound SPDX expressions are evaluated per-branch:
    ``OR`` passes if any branch is allowed, ``AND`` requires every branch
    allowed, and ``WITH`` is evaluated as the base license.
    """

    def __init__(self, config: LicensesConfig) -> None:
        """Initialize the policy engine.

        Args:
            config: The license policy configuration to enforce.
        """
        self.config = config
        preset_allowed, preset_denied = get_preset_rules(config.policy)
        # Explicit config lists take precedence over preset membership.
        self.denied: set[str] = set(preset_denied) | set(config.denied)
        self.allowed: set[str] = (set(preset_allowed) | set(config.allowed)) - set(
            config.denied,
        )

    def check(self, package: PackageLicense) -> LicenseResult:
        """Evaluate a single package against the policy.

        Args:
            package: The package to evaluate.

        Returns:
            LicenseResult: The classification and a human-readable reason.
        """
        exception = self.config.exception_for(package.name)

        # Exceptions may remap the effective license before evaluation.
        effective_id = package.license_id
        if exception is not None and exception.treat_as:
            effective_id = normalize_to_spdx(exception.treat_as) or exception.treat_as

        if exception is not None and exception.allowed and exception.treat_as is None:
            reason = exception.reason or "Allowed by package exception"
            return LicenseResult(
                package=package,
                status=LicenseStatus.ALLOWED,
                reason=reason,
            )

        if effective_id is None:
            return self._classify_unknown(package)

        return self._evaluate_expression(
            package=package,
            license_id=effective_id,
        )

    def _classify_single(
        self,
        *,
        package: PackageLicense,
        license_id: str,
    ) -> LicenseResult:
        """Classify a single SPDX license identifier.

        Args:
            package: Package under evaluation (for result attachment).
            license_id: Canonical SPDX identifier.

        Returns:
            LicenseResult: Classification for this license id.
        """
        if license_id in self.denied:
            return LicenseResult(
                package=package,
                status=LicenseStatus.DENIED,
                reason=f"License {license_id} is denied by policy",
            )

        if license_id in self.allowed:
            return LicenseResult(
                package=package,
                status=LicenseStatus.ALLOWED,
                reason=f"License {license_id} is allowed by policy",
            )

        # Recognized SPDX id, but not in allow or deny sets.
        if self.config.policy == "strict":
            return LicenseResult(
                package=package,
                status=LicenseStatus.DENIED,
                reason=(
                    f"License {license_id} is not explicitly allowed (strict policy)"
                ),
            )
        return LicenseResult(
            package=package,
            status=LicenseStatus.ALLOWED,
            reason=f"License {license_id} is not restricted by policy",
        )

    def _evaluate_expression(
        self,
        *,
        package: PackageLicense,
        license_id: str,
    ) -> LicenseResult:
        """Evaluate a (possibly compound) SPDX expression against the policy.

        Args:
            package: Package under evaluation.
            license_id: Normalized SPDX id or expression string.

        Returns:
            LicenseResult: Per-branch evaluation result.
        """
        expression = parse_license_expression(license_id)
        if expression is None:
            # Policy-only markers (e.g. Commons-Clause) may be allow/deny listed
            # without being parseable SPDX; everything else follows unknown_policy.
            if license_id in self.allowed or license_id in self.denied:
                return self._classify_single(package=package, license_id=license_id)
            return self._classify_unknown(package)

        return self._evaluate_parsed(
            package=package,
            expression=expression,
            display=license_id,
        )

    def _evaluate_parsed(
        self,
        *,
        package: PackageLicense,
        expression: LicenseExpression,
        display: str,
    ) -> LicenseResult:
        """Recursively evaluate a parsed SPDX expression tree.

        Args:
            package: Package under evaluation.
            expression: Parsed ``license-expression`` tree.
            display: Human-readable expression for reasons.

        Returns:
            LicenseResult: Combined status for the expression.
        """
        if isinstance(expression, LicenseWithExceptionSymbol):
            # Conservative: WITH exceptions evaluate as the base license only.
            base = str(expression.license_symbol)
            return self._classify_single(package=package, license_id=base)

        if isinstance(expression, LicenseSymbol):
            return self._classify_single(
                package=package,
                license_id=str(expression),
            )

        if isinstance(expression, OR):
            branch_results = [
                self._evaluate_parsed(
                    package=package,
                    expression=arg,
                    display=str(arg),
                )
                for arg in expression.args
            ]
            for result in branch_results:
                if result.status is LicenseStatus.ALLOWED:
                    return LicenseResult(
                        package=package,
                        status=LicenseStatus.ALLOWED,
                        reason=(
                            f"Expression {display} allowed via branch "
                            f"({result.reason})"
                        ),
                    )
            return LicenseResult(
                package=package,
                status=LicenseStatus.DENIED,
                reason=f"Expression {display} has no allowed OR branch",
            )

        if isinstance(expression, AND):
            branch_results = [
                self._evaluate_parsed(
                    package=package,
                    expression=arg,
                    display=str(arg),
                )
                for arg in expression.args
            ]
            for result in branch_results:
                if result.status is LicenseStatus.DENIED:
                    return LicenseResult(
                        package=package,
                        status=LicenseStatus.DENIED,
                        reason=(
                            f"Expression {display} denied via branch "
                            f"({result.reason})"
                        ),
                    )
            return LicenseResult(
                package=package,
                status=LicenseStatus.ALLOWED,
                reason=f"Expression {display} allowed (all AND branches)",
            )

        return self._classify_single(package=package, license_id=display)

    def _classify_unknown(self, package: PackageLicense) -> LicenseResult:
        """Classify a package whose license could not be determined.

        Args:
            package: The package with an unknown license.

        Returns:
            LicenseResult: Classification per ``unknown_policy``.
        """
        policy = self.config.unknown_policy
        if policy == "allow":
            return LicenseResult(
                package=package,
                status=LicenseStatus.ALLOWED,
                reason="Unknown license allowed by policy",
            )
        if policy == "deny":
            return LicenseResult(
                package=package,
                status=LicenseStatus.DENIED,
                reason="Unknown license denied by policy",
            )
        return LicenseResult(
            package=package,
            status=LicenseStatus.UNKNOWN,
            reason="License could not be determined",
        )

    def evaluate_all(
        self,
        packages: list[PackageLicense],
    ) -> list[LicenseResult]:
        """Evaluate a collection of packages against the policy.

        Development dependencies are skipped when
        ``ignore_dev_dependencies`` is enabled.

        Args:
            packages: Packages to evaluate.

        Returns:
            list[LicenseResult]: Results sorted by package name.
        """
        results: list[LicenseResult] = []
        for package in packages:
            if package.is_dev and self.config.ignore_dev_dependencies:
                continue
            results.append(self.check(package))
        return sorted(results, key=lambda r: r.package.name.lower())
