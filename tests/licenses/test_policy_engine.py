"""Tests for the license policy engine and its presets."""

from __future__ import annotations

from assertpy import assert_that

from lintro.config.licenses_config import LicensesConfig, PackageException
from lintro.licenses.models import LicenseStatus, PackageLicense
from lintro.licenses.policy_engine import LicensePolicyEngine, get_preset_rules


def test_permissive_allows_mit(mit_package: PackageLicense) -> None:
    """The permissive preset allows MIT.

    Args:
        mit_package: An MIT-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(mit_package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)


def test_permissive_denies_gpl(gpl_package: PackageLicense) -> None:
    """The permissive preset denies strong copyleft.

    Args:
        gpl_package: A GPL-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(gpl_package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)
    assert_that(result.is_violation).is_true()


def test_permissive_denies_weak_copyleft(lgpl_package: PackageLicense) -> None:
    """The permissive preset denies weak copyleft.

    Args:
        lgpl_package: An LGPL-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    assert_that(engine.check(lgpl_package).status).is_equal_to(
        LicenseStatus.DENIED,
    )


def test_copyleft_ok_allows_weak_copyleft(lgpl_package: PackageLicense) -> None:
    """The copyleft-ok preset allows weak copyleft.

    Args:
        lgpl_package: An LGPL-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="copyleft-ok"))
    assert_that(engine.check(lgpl_package).status).is_equal_to(
        LicenseStatus.ALLOWED,
    )


def test_copyleft_ok_denies_strong_copyleft(gpl_package: PackageLicense) -> None:
    """The copyleft-ok preset still denies strong copyleft.

    Args:
        gpl_package: A GPL-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="copyleft-ok"))
    assert_that(engine.check(gpl_package).status).is_equal_to(
        LicenseStatus.DENIED,
    )


def test_strict_denies_unlisted_license(mit_package: PackageLicense) -> None:
    """Strict policy denies a recognized license that is not allow-listed.

    Args:
        mit_package: An MIT-licensed package fixture.
    """
    engine = LicensePolicyEngine(LicensesConfig(policy="strict"))
    assert_that(engine.check(mit_package).status).is_equal_to(
        LicenseStatus.DENIED,
    )


def test_strict_allows_explicitly_allowed(mit_package: PackageLicense) -> None:
    """Strict policy allows a license added to the allow list.

    Args:
        mit_package: An MIT-licensed package fixture.
    """
    engine = LicensePolicyEngine(
        LicensesConfig(policy="strict", allowed=["MIT"]),
    )
    assert_that(engine.check(mit_package).status).is_equal_to(
        LicenseStatus.ALLOWED,
    )


def test_explicit_denied_overrides_preset(mit_package: PackageLicense) -> None:
    """An explicit deny entry overrides preset allowance.

    Args:
        mit_package: An MIT-licensed package fixture.
    """
    engine = LicensePolicyEngine(
        LicensesConfig(policy="permissive", denied=["MIT"]),
    )
    assert_that(engine.check(mit_package).status).is_equal_to(
        LicenseStatus.DENIED,
    )


def test_unknown_policy_warn(unknown_package: PackageLicense) -> None:
    """Unknown licenses map to UNKNOWN under the warn policy.

    Args:
        unknown_package: A package without an SPDX identifier.
    """
    engine = LicensePolicyEngine(LicensesConfig(unknown_policy="warn"))
    assert_that(engine.check(unknown_package).status).is_equal_to(
        LicenseStatus.UNKNOWN,
    )


def test_unknown_policy_deny(unknown_package: PackageLicense) -> None:
    """Unknown licenses are denied under the deny policy.

    Args:
        unknown_package: A package without an SPDX identifier.
    """
    engine = LicensePolicyEngine(LicensesConfig(unknown_policy="deny"))
    assert_that(engine.check(unknown_package).status).is_equal_to(
        LicenseStatus.DENIED,
    )


def test_unknown_policy_allow(unknown_package: PackageLicense) -> None:
    """Unknown licenses are allowed under the allow policy.

    Args:
        unknown_package: A package without an SPDX identifier.
    """
    engine = LicensePolicyEngine(LicensesConfig(unknown_policy="allow"))
    assert_that(engine.check(unknown_package).status).is_equal_to(
        LicenseStatus.ALLOWED,
    )


def test_exception_allows_denied_package(gpl_package: PackageLicense) -> None:
    """A package exception can allow an otherwise-denied license.

    Args:
        gpl_package: A GPL-licensed package fixture.
    """
    config = LicensesConfig(
        policy="permissive",
        exceptions=[
            PackageException(
                package="some-lib",
                reason="Dev-only, not distributed",
                allowed=True,
            ),
        ],
    )
    result = LicensePolicyEngine(config).check(gpl_package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)
    assert_that(result.reason).contains("Dev-only")


def test_exception_treat_as_remaps_license(
    unknown_package: PackageLicense,
) -> None:
    """A treat_as exception remaps an unknown license to a known one.

    Args:
        unknown_package: A package without an SPDX identifier.
    """
    config = LicensesConfig(
        policy="permissive",
        exceptions=[
            PackageException(
                package="mystery-pkg",
                reason="Manually verified as MIT",
                treat_as="MIT",
            ),
        ],
    )
    result = LicensePolicyEngine(config).check(unknown_package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)


def test_evaluate_all_skips_dev_dependencies() -> None:
    """Dev dependencies are skipped when configured to be ignored."""
    packages = [
        PackageLicense(name="a", version="1", license_id="MIT", is_dev=False),
        PackageLicense(name="b", version="1", license_id="MIT", is_dev=True),
    ]
    engine = LicensePolicyEngine(
        LicensesConfig(ignore_dev_dependencies=True),
    )
    results = engine.evaluate_all(packages)
    assert_that(results).is_length(1)
    assert_that(results[0].package.name).is_equal_to("a")


def test_evaluate_all_includes_dev_when_configured() -> None:
    """Dev dependencies are included when not ignored."""
    packages = [
        PackageLicense(name="b", version="1", license_id="MIT", is_dev=True),
    ]
    engine = LicensePolicyEngine(
        LicensesConfig(ignore_dev_dependencies=False),
    )
    assert_that(engine.evaluate_all(packages)).is_length(1)


def test_get_preset_rules_custom_is_empty() -> None:
    """The custom preset yields empty allow/deny sets."""
    allowed, denied = get_preset_rules("custom")
    assert_that(allowed).is_empty()
    assert_that(denied).is_empty()


def test_permissive_allows_or_expression_with_permissive_branch() -> None:
    """OR expressions pass permissive policy when any branch is allowed."""
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    package = PackageLicense(
        name="dual",
        version="1",
        license_id="MIT OR Apache-2.0",
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)


def test_permissive_denies_and_expression_with_copyleft_branch() -> None:
    """AND expressions fail permissive policy when any branch is denied."""
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    package = PackageLicense(
        name="conj",
        version="1",
        license_id="MIT AND GPL-3.0-only",
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)


def test_permissive_evaluates_with_as_base_license() -> None:
    """WITH exceptions are evaluated as the base license only."""
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    package = PackageLicense(
        name="classpath",
        version="1",
        license_id="GPL-2.0-only WITH Classpath-exception-2.0",
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)
    assert_that(result.reason).contains("GPL-2.0-only")


def test_permissive_allows_parenthesized_or_expression() -> None:
    """npm-style parenthesized OR expressions evaluate like bare OR."""
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    package = PackageLicense(
        name="npm-dual",
        version="1",
        license_id="(MIT OR Apache-2.0)",
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)
