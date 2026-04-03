"""Tests for lintro.tools.core.install_strategies package."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies import (
    InstallEnvironment,
    get_strategy,
    strategy_registry,
)
from lintro.tools.core.install_strategies.brew_names import BREW_FORMULA_NAMES

PM = PackageManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(
    *,
    managers: frozenset[PackageManager] = frozenset(PackageManager),
    install_context: InstallContext = InstallContext.PIP,
) -> InstallEnvironment:
    """Build an InstallEnvironment with explicit manager set.

    Args:
        managers: Set of available package managers.
        install_context: How lintro was installed.

    Returns:
        An InstallEnvironment instance.
    """
    return InstallEnvironment(
        install_context=install_context,
        available_managers=managers,
    )


# ---------------------------------------------------------------------------
# InstallEnvironment
# ---------------------------------------------------------------------------


def test_install_environment_has_true() -> None:
    """Return True when the manager is present in available_managers."""
    env = _make_env()

    assert_that(env.has(PM.UV)).is_true()


def test_install_environment_has_false() -> None:
    """Return False when available_managers is empty."""
    env = _make_env(managers=frozenset())

    assert_that(env.has(PM.UV)).is_false()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_all_five() -> None:
    """Strategy registry has entries for pip, npm, binary, cargo, and rustup."""
    registry = strategy_registry()

    assert_that(registry).contains_key("pip", "npm", "binary", "cargo", "rustup")


def test_get_strategy_pip() -> None:
    """get_strategy('pip') returns a strategy with install_type 'pip'."""
    strategy = get_strategy("pip")

    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101
    assert_that(strategy.install_type()).is_equal_to("pip")


def test_get_strategy_unknown_returns_none() -> None:
    """get_strategy returns None for an unregistered install type."""
    result = get_strategy("magic")

    assert_that(result).is_none()


# ---------------------------------------------------------------------------
# PipStrategy
# ---------------------------------------------------------------------------


def test_pip_install_hint_with_uv() -> None:
    """Prefer 'uv pip install' when uv is available."""
    env = _make_env(managers=frozenset({PM.UV}))
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "ruff", "0.14.0", "ruff", None)

    assert_that(result).is_equal_to("uv pip install 'ruff>=0.14.0'")


def test_pip_install_hint_without_uv() -> None:
    """Fall back to 'pip install' when uv is absent but pip is present."""
    env = _make_env(managers=frozenset({PM.PIP}))
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "ruff", "0.14.0", "ruff", None)

    assert_that(result).is_equal_to("pip install 'ruff>=0.14.0'")


def test_pip_install_hint_homebrew_context() -> None:
    """Use brew install for mapped tools under HOMEBREW_FULL context."""
    env = _make_env(
        managers=frozenset({PM.BREW, PM.UV}),
        install_context=InstallContext.HOMEBREW_FULL,
    )
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(
        env,
        "markdownlint",
        "0.12.0",
        "markdownlint-cli2",
        None,
    )

    assert_that(result).is_equal_to("brew install markdownlint-cli2")


def test_pip_upgrade_hint() -> None:
    """Generate upgrade hint with --upgrade flag when uv is available."""
    env = _make_env(managers=frozenset({PM.UV}))
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(env, "ruff", "0.14.0", "ruff", None)

    assert_that(result).is_equal_to("uv pip install --upgrade 'ruff>=0.14.0'")


def test_pip_upgrade_hint_homebrew() -> None:
    """Use brew upgrade for mapped tools under HOMEBREW_FULL context."""
    env = _make_env(
        managers=frozenset({PM.BREW, PM.UV}),
        install_context=InstallContext.HOMEBREW_FULL,
    )
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(
        env,
        "markdownlint",
        "0.12.0",
        "markdownlint-cli2",
        None,
    )

    assert_that(result).is_equal_to("brew upgrade markdownlint-cli2")


def test_pip_check_prerequisites_met() -> None:
    """Return None when uv is available."""
    env = _make_env(managers=frozenset({PM.UV}))
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "ruff")

    assert_that(result).is_none()


def test_pip_check_prerequisites_not_met() -> None:
    """Return skip reason when neither uv nor pip is available."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "ruff")

    assert_that(result).is_equal_to("uv/pip not available")


def test_pip_brew_only_non_homebrew_context() -> None:
    """Brew-only env with PIP context: prereq passes for mapped tools, hint uses brew."""
    env = _make_env(
        managers=frozenset({PM.BREW}),
        install_context=InstallContext.PIP,
    )
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    # Prerequisites pass for a tool with a brew formula
    assert_that(strategy.check_prerequisites(env, "markdownlint")).is_none()

    # Install hint uses brew (not pip) since pip is unavailable
    hint = strategy.install_hint(
        env,
        "markdownlint",
        "0.22.0",
        "markdownlint-cli2",
        None,
    )
    assert_that(hint).is_equal_to("brew install markdownlint-cli2")

    # Unmapped tool without pip/uv fails prereqs
    assert_that(strategy.check_prerequisites(env, "ruff")).is_equal_to(
        "uv/pip not available",
    )


def test_pip_is_available_true() -> None:
    """Return True when uv is available."""
    env = _make_env(managers=frozenset({PM.UV}))
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    assert_that(strategy.is_available(env)).is_true()


def test_pip_is_available_false() -> None:
    """Return False when no relevant package manager is available."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("pip")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    assert_that(strategy.is_available(env)).is_false()


# ---------------------------------------------------------------------------
# NpmStrategy
# ---------------------------------------------------------------------------


def test_npm_install_hint_with_bun() -> None:
    """Prefer 'bun add -g' when bun is available."""
    env = _make_env(managers=frozenset({PM.BUN}))
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "prettier", "3.2.0", "prettier", None)

    assert_that(result).is_equal_to("bun add -g prettier@3.2.0")


def test_npm_install_hint_without_bun() -> None:
    """Fall back to 'npm install -g' when bun is absent."""
    env = _make_env(managers=frozenset({PM.NPM}))
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "prettier", "3.2.0", "prettier", None)

    assert_that(result).is_equal_to("npm install -g prettier@3.2.0")


def test_npm_upgrade_hint_with_bun() -> None:
    """Npm replaces on install, so upgrade hint matches install hint."""
    env = _make_env(managers=frozenset({PM.BUN}))
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(env, "prettier", "3.2.0", "prettier", None)

    assert_that(result).is_equal_to("bun add -g prettier@3.2.0")


def test_npm_check_prerequisites_met() -> None:
    """Return None when npm is available."""
    env = _make_env(managers=frozenset({PM.NPM}))
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "prettier")

    assert_that(result).is_none()


def test_npm_check_prerequisites_not_met() -> None:
    """Return skip reason when neither bun nor npm is available."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "prettier")

    assert_that(result).is_equal_to("bun/npm not available (install Node.js first)")


def test_npm_brew_only_mapped_tool() -> None:
    """Brew-only env: mapped tool gets brew install hint and passes prereqs."""
    env = _make_env(
        managers=frozenset({PM.BREW}),
        install_context=InstallContext.PIP,
    )
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    assert_that(strategy.check_prerequisites(env, "markdownlint")).is_none()
    hint = strategy.install_hint(
        env,
        "markdownlint",
        "0.22.0",
        "markdownlint-cli2",
        None,
    )
    assert_that(hint).is_equal_to("brew install markdownlint-cli2")


def test_npm_brew_only_unmapped_tool() -> None:
    """Brew-only env: unmapped tool fails prereqs."""
    env = _make_env(
        managers=frozenset({PM.BREW}),
        install_context=InstallContext.PIP,
    )
    strategy = get_strategy("npm")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "prettier")
    assert_that(result).is_equal_to("bun/npm not available (install Node.js first)")


# ---------------------------------------------------------------------------
# BinaryStrategy
# ---------------------------------------------------------------------------


def test_binary_install_hint_with_brew() -> None:
    """Generate brew install when Homebrew is available."""
    env = _make_env(managers=frozenset({PM.BREW}))
    strategy = get_strategy("binary")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "hadolint", "2.12.0", "hadolint", None)

    assert_that(result).is_equal_to("brew install hadolint")


def test_binary_install_hint_without_brew() -> None:
    """Fall back to GitHub search URL when brew is unavailable."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("binary")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "hadolint", "2.12.0", "hadolint", None)

    assert_that(result).starts_with("See https://")
    assert_that(result).contains("hadolint")


def test_binary_upgrade_hint_with_brew() -> None:
    """Generate brew upgrade hint when Homebrew is available."""
    env = _make_env(managers=frozenset({PM.BREW}))
    strategy = get_strategy("binary")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(env, "hadolint", "2.12.0", "hadolint", None)

    assert_that(result).is_equal_to("brew upgrade hadolint")


def test_binary_check_prerequisites_always_none() -> None:
    """Binary strategy never fails prerequisite checks."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("binary")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "hadolint")

    assert_that(result).is_none()


# ---------------------------------------------------------------------------
# CargoStrategy
# ---------------------------------------------------------------------------


def test_cargo_install_hint() -> None:
    """Generate cargo install command for a crate."""
    env = _make_env(managers=frozenset({PM.CARGO}))
    strategy = get_strategy("cargo")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(
        env,
        "cargo-audit",
        "0.20.0",
        "cargo-audit",
        None,
    )

    assert_that(result).is_equal_to("cargo install cargo-audit")


def test_cargo_upgrade_hint() -> None:
    """Generate cargo install --force command for upgrades."""
    env = _make_env(managers=frozenset({PM.CARGO}))
    strategy = get_strategy("cargo")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(
        env,
        "cargo-audit",
        "0.20.0",
        "cargo-audit",
        None,
    )

    assert_that(result).is_equal_to("cargo install --force cargo-audit")


def test_cargo_check_prerequisites_met() -> None:
    """Return None when cargo is available."""
    env = _make_env(managers=frozenset({PM.CARGO}))
    strategy = get_strategy("cargo")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "cargo-audit")

    assert_that(result).is_none()


def test_cargo_check_prerequisites_not_met() -> None:
    """Return skip reason when cargo is not available."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("cargo")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "cargo-audit")

    assert_that(result).is_equal_to("cargo not available (install Rust first)")


# ---------------------------------------------------------------------------
# RustupStrategy
# ---------------------------------------------------------------------------


def test_rustup_install_hint_with_component() -> None:
    """Generate rustup component add when component is specified."""
    env = _make_env(managers=frozenset({PM.RUSTUP}))
    strategy = get_strategy("rustup")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "clippy", "0.1.0", None, "clippy")

    assert_that(result).is_equal_to("rustup component add clippy")


def test_rustup_install_hint_without_component() -> None:
    """Generate rustup toolchain install when no component is specified."""
    env = _make_env(managers=frozenset({PM.RUSTUP}))
    strategy = get_strategy("rustup")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.install_hint(env, "rustfmt", "1.0.0", None, None)

    assert_that(result).is_equal_to("rustup toolchain install stable")


def test_rustup_upgrade_hint() -> None:
    """Generate rustup update stable for upgrades."""
    env = _make_env(managers=frozenset({PM.RUSTUP}))
    strategy = get_strategy("rustup")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.upgrade_hint(env, "clippy", "0.1.0", None, "clippy")

    assert_that(result).is_equal_to("rustup update stable")


def test_rustup_check_prerequisites_met() -> None:
    """Return None when rustup is available."""
    env = _make_env(managers=frozenset({PM.RUSTUP}))
    strategy = get_strategy("rustup")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "clippy")

    assert_that(result).is_none()


def test_rustup_check_prerequisites_not_met() -> None:
    """Return skip reason when rustup is not available."""
    env = _make_env(managers=frozenset())
    strategy = get_strategy("rustup")
    assert_that(strategy).is_not_none()
    # narrow Optional for mypy — assertpy does not perform type narrowing
    assert strategy is not None  # noqa: S101

    result = strategy.check_prerequisites(env, "clippy")

    assert_that(result).is_equal_to("clippy: rustup not available (install Rust first)")


# ---------------------------------------------------------------------------
# BREW_FORMULA_NAMES
# ---------------------------------------------------------------------------


def test_brew_formula_names_markdownlint() -> None:
    """Verify markdownlint maps to markdownlint-cli2."""
    assert_that(BREW_FORMULA_NAMES).contains_key("markdownlint")
    assert_that(BREW_FORMULA_NAMES["markdownlint"]).is_equal_to(
        "markdownlint-cli2",
    )


def test_brew_formula_names_osv_scanner() -> None:
    """Verify osv_scanner maps to osv-scanner."""
    assert_that(BREW_FORMULA_NAMES).contains_key("osv_scanner")
    assert_that(BREW_FORMULA_NAMES["osv_scanner"]).is_equal_to("osv-scanner")
