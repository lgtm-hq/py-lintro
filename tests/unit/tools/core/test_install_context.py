"""Tests for lintro.tools.core.install_context module.

Hint generation tests have moved to test_install_strategies.py.
This file covers RuntimeContext construction, install context detection,
and CI detection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.install_context import CISystem, InstallContext, PackageManager
from lintro.tools.core.install_context import (
    RuntimeContext,
    _detect_install_context,
    _is_ci,
)
from lintro.tools.core.install_strategies.environment import InstallEnvironment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    install_context: InstallContext = InstallContext.PIP,
    platform_label: str = "macOS arm64",
    managers: frozenset[PackageManager] = frozenset(PackageManager),
    is_ci: bool = False,
    ci_name: CISystem | None = None,
) -> RuntimeContext:
    """Build a RuntimeContext with sensible defaults for testing.

    Args:
        install_context: How lintro was installed.
        platform_label: Platform description.
        managers: Available package manager names.
        is_ci: Running in CI.
        ci_name: CI system name.

    Returns:
        A RuntimeContext instance.
    """
    return RuntimeContext(
        install_context=install_context,
        platform_label=platform_label,
        environment=InstallEnvironment(
            install_context=install_context,
            available_managers=managers,
        ),
        is_ci=is_ci,
        ci_name=ci_name,
    )


# ---------------------------------------------------------------------------
# RuntimeContext construction
# ---------------------------------------------------------------------------


def test_runtime_context_has_environment() -> None:
    """RuntimeContext carries an InstallEnvironment."""
    ctx = _make_ctx()
    assert_that(ctx.environment).is_instance_of(InstallEnvironment)
    assert_that(ctx.environment.has(PackageManager.UV)).is_true()


def test_runtime_context_environment_reflects_managers() -> None:
    """InstallEnvironment correctly reports available managers."""
    ctx = _make_ctx(managers=frozenset({PackageManager.CARGO}))
    assert_that(ctx.environment.has(PackageManager.CARGO)).is_true()
    assert_that(ctx.environment.has(PackageManager.UV)).is_false()


# ---------------------------------------------------------------------------
# _detect_install_context
# ---------------------------------------------------------------------------


def test_detect_docker_via_dockerenv() -> None:
    """Detect Docker context when /.dockerenv file exists."""
    with patch(
        "lintro.tools.core.install_context.os.path.exists",
        return_value=True,
    ):
        result = _detect_install_context()

    assert_that(result).is_equal_to(InstallContext.DOCKER)


def test_detect_docker_via_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect Docker context via LINTRO_DOCKER=1 env var."""
    monkeypatch.setenv("LINTRO_DOCKER", "1")
    with patch(
        "lintro.tools.core.install_context.os.path.exists",
        return_value=False,
    ):
        result = _detect_install_context()

    assert_that(result).is_equal_to(InstallContext.DOCKER)


def test_detect_docker_via_container_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect Docker context via CONTAINER=docker env var.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("LINTRO_DOCKER", raising=False)
    monkeypatch.setenv("CONTAINER", "docker")
    with patch(
        "lintro.tools.core.install_context.os.path.exists",
        return_value=False,
    ):
        result = _detect_install_context()

    assert_that(result).is_equal_to(InstallContext.DOCKER)


def test_detect_pip_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default to PIP context when no special indicators are present."""
    monkeypatch.delenv("LINTRO_DOCKER", raising=False)
    monkeypatch.delenv("CONTAINER", raising=False)

    with (
        patch(
            "lintro.tools.core.install_context.os.path.exists",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.install_context.__file__",
            "/usr/lib/python3.11/site-packages/lintro/tools/core/install_context.py",
            create=True,
        ),
        patch(
            "lintro.tools.core.install_context.sys.executable",
            "/usr/bin/python3",
        ),
    ):
        result = _detect_install_context()

    assert_that(result).is_equal_to(InstallContext.PIP)


@pytest.mark.parametrize(
    ("install_file", "executable", "expected"),
    [
        (
            "/usr/lib/python3.11/site-packages/lintro/tools/core/install_context.py",
            "/opt/homebrew/Cellar/lintro/0.64.1/bin/lintro",
            InstallContext.HOMEBREW_BIN,
        ),
        (
            "/opt/homebrew/Cellar/lintro-full/0.64.1/libexec/lib/python3.14/"
            "site-packages/lintro/tools/core/install_context.py",
            "/opt/homebrew/Cellar/lintro-full/0.64.1/bin/lintro",
            InstallContext.HOMEBREW_FULL,
        ),
        (
            "/opt/homebrew/lib/python3.14/site-packages/lintro/tools/core/"
            "install_context.py",
            "/usr/bin/python3",
            InstallContext.PIP,
        ),
        (
            "/usr/lib/python3.11/site-packages/lintro/tools/core/install_context.py",
            "/usr/bin/python3",
            InstallContext.PIP,
        ),
    ],
    ids=[
        "homebrew_binary_formula",
        "homebrew_full_formula",
        "pip_homebrew_lib_site_packages",
        "pip_default",
    ],
)
def test_detect_homebrew_install_context(
    install_file: str,
    executable: str,
    expected: InstallContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect Homebrew binary vs full formula paths from executable and module path."""
    monkeypatch.delenv("LINTRO_DOCKER", raising=False)
    monkeypatch.delenv("CONTAINER", raising=False)

    with (
        patch(
            "lintro.tools.core.install_context.os.path.exists",
            return_value=False,
        ),
        patch(
            "lintro.tools.core.install_context.__file__",
            install_file,
            create=True,
        ),
        patch(
            "lintro.tools.core.install_context.sys.executable",
            executable,
        ),
        patch(
            "lintro.tools.core.install_context.os.path.realpath",
            side_effect=lambda path: path,
        ),
    ):
        result = _detect_install_context()

    assert_that(result).is_equal_to(expected)


# ---------------------------------------------------------------------------
# CI detection
# ---------------------------------------------------------------------------


def test_is_ci_with_ci_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return True when generic CI env var is set."""
    monkeypatch.setenv("CI", "true")
    for var in (
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "CIRCLECI",
        "JENKINS_URL",
        "BUILDKITE",
        "TF_BUILD",
    ):
        monkeypatch.delenv(var, raising=False)

    assert_that(_is_ci()).is_true()


def test_is_ci_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return False when no CI env vars are set."""
    monkeypatch.delenv("CI", raising=False)
    for var in (
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "CIRCLECI",
        "JENKINS_URL",
        "BUILDKITE",
        "TF_BUILD",
    ):
        monkeypatch.delenv(var, raising=False)

    assert_that(_is_ci()).is_false()


@pytest.mark.parametrize(
    ("env_var", "env_value", "expected"),
    [
        ("GITHUB_ACTIONS", "1", CISystem.GITHUB_ACTIONS),
        ("GITLAB_CI", "1", CISystem.GITLAB_CI),
        ("CIRCLECI", "true", CISystem.CIRCLECI),
        ("JENKINS_URL", "https://ci.example.com", CISystem.JENKINS),
        ("BUILDKITE", "true", CISystem.BUILDKITE),
        ("TF_BUILD", "True", CISystem.AZURE_PIPELINES),
    ],
    ids=[
        "github_actions",
        "gitlab_ci",
        "circleci",
        "jenkins",
        "buildkite",
        "azure_pipelines",
    ],
)
def test_detect_ci_system(
    env_var: str,
    env_value: str,
    expected: CISystem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect specific CI system from its environment variable.

    Args:
        env_var: The CI-specific env var to set.
        env_value: The value to set for the env var.
        expected: Expected CISystem enum member.
        monkeypatch: Pytest monkeypatch fixture.
    """
    for var in (
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "CIRCLECI",
        "JENKINS_URL",
        "BUILDKITE",
        "TF_BUILD",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv(env_var, env_value)

    assert_that(CISystem.detect()).is_equal_to(expected)


def test_detect_ci_system_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None when no CI env vars are set."""
    for var in (
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "CIRCLECI",
        "JENKINS_URL",
        "BUILDKITE",
        "TF_BUILD",
    ):
        monkeypatch.delenv(var, raising=False)

    assert_that(CISystem.detect()).is_none()


def test_ci_system_is_str_enum() -> None:
    """CISystem values are human-readable strings."""
    assert_that(str(CISystem.GITHUB_ACTIONS)).is_equal_to("GitHub Actions")
    assert_that(str(CISystem.AZURE_PIPELINES)).is_equal_to("Azure Pipelines")
