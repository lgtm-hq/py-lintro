"""Tests for license policy configuration loading."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.config.licenses_config import (
    LicensesConfig,
    PackageException,
    load_licenses_config,
)


def test_defaults_are_permissive() -> None:
    """A config with no data defaults to the permissive preset."""
    config = LicensesConfig()
    assert_that(config.policy).is_equal_to("permissive")
    assert_that(config.unknown_policy).is_equal_to("warn")
    assert_that(config.ignore_dev_dependencies).is_true()


def test_exception_lookup_normalizes_name() -> None:
    """Exception lookup ignores case and underscore/hyphen differences."""
    config = LicensesConfig(
        exceptions=[PackageException(package="Some_Lib", reason="ok")],
    )
    assert_that(config.exception_for("some-lib")).is_not_none()
    assert_that(config.exception_for("missing")).is_none()


def test_load_from_yaml(tmp_path: Path) -> None:
    """The loader reads the licenses section from a YAML config file.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "licenses:\n  policy: strict\n  allowed:\n    - MIT\n  unknown_policy: deny\n",
    )
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("strict")
    assert_that(config.allowed).contains("MIT")
    assert_that(config.unknown_policy).is_equal_to("deny")


def test_load_from_pyproject(tmp_path: Path) -> None:
    """The loader falls back to [tool.lintro.licenses] in pyproject.toml.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lintro.licenses]\npolicy = "copyleft-ok"\ndenied = ["AGPL-3.0-only"]\n',
    )
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("copyleft-ok")
    assert_that(config.denied).contains("AGPL-3.0-only")


def test_load_defaults_when_absent(tmp_path: Path) -> None:
    """The loader returns defaults when no config is present.

    Args:
        tmp_path: Empty temporary directory.
    """
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("permissive")
