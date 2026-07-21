"""Tests for the Python and npm ecosystem adapters."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.config.licenses_config import LicensesConfig
from lintro.licenses.ecosystems import NpmLicenseAdapter, PythonLicenseAdapter
from lintro.licenses.models import LicenseStatus
from lintro.licenses.policy_engine import LicensePolicyEngine


def test_python_adapter_collects_installed_packages() -> None:
    """The Python adapter reports installed distributions with versions."""
    packages = PythonLicenseAdapter().get_installed_licenses()
    assert_that(packages).is_not_empty()
    names = {p.name.lower() for p in packages}
    # pydantic is a hard runtime dependency of lintro.
    assert_that(names).contains("pydantic")
    for package in packages:
        assert_that(package.ecosystem).is_equal_to("python")
        assert_that(package.version).is_not_empty()


def test_python_adapter_normalizes_known_license() -> None:
    """At least one installed package resolves to a known SPDX id."""
    packages = PythonLicenseAdapter().get_installed_licenses()
    resolved = [p for p in packages if p.license_id is not None]
    assert_that(resolved).is_not_empty()


def test_npm_adapter_reads_node_modules(tmp_path: Path) -> None:
    """The npm adapter reads licenses from node_modules manifests.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "root", "devDependencies": {"eslint": "1.0.0"}}),
    )
    node_modules = tmp_path / "node_modules"
    (node_modules / "react").mkdir(parents=True)
    (node_modules / "react" / "package.json").write_text(
        json.dumps({"name": "react", "version": "18.2.0", "license": "MIT"}),
    )
    (node_modules / "eslint").mkdir(parents=True)
    (node_modules / "eslint" / "package.json").write_text(
        json.dumps({"name": "eslint", "version": "1.0.0", "license": "MIT"}),
    )

    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    by_name = {p.name: p for p in packages}
    assert_that(by_name).contains_key("react", "eslint")
    assert_that(by_name["react"].license_id).is_equal_to("MIT")
    assert_that(by_name["react"].is_dev).is_false()
    assert_that(by_name["eslint"].is_dev).is_true()


def test_npm_adapter_scoped_package(tmp_path: Path) -> None:
    """The npm adapter discovers scoped (@scope/name) packages.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(json.dumps({"name": "root"}))
    scoped = tmp_path / "node_modules" / "@types" / "node"
    scoped.mkdir(parents=True)
    scoped.joinpath("package.json").write_text(
        json.dumps({"name": "@types/node", "version": "20.0.0", "license": "MIT"}),
    )
    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    assert_that([p.name for p in packages]).contains("@types/node")


def test_npm_adapter_legacy_licenses_array(tmp_path: Path) -> None:
    """The npm adapter supports the legacy licenses array field.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(json.dumps({"name": "root"}))
    pkg = tmp_path / "node_modules" / "old-pkg"
    pkg.mkdir(parents=True)
    pkg.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "old-pkg",
                "version": "1.0.0",
                "licenses": [{"type": "Apache-2.0"}],
            },
        ),
    )
    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    assert_that(packages[0].license_id).is_equal_to("Apache-2.0")


def test_npm_adapter_missing_file_returns_empty(tmp_path: Path) -> None:
    """A missing package.json yields no packages.

    Args:
        tmp_path: Temporary directory without a manifest.
    """
    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    assert_that(packages).is_empty()


def test_npm_adapter_reports_shadowed_nested_versions(tmp_path: Path) -> None:
    """Nested installs with the same name but different versions are kept.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "root",
                "dependencies": {"parent-pkg": "1.0.0"},
            },
        ),
    )
    parent = tmp_path / "node_modules" / "parent-pkg"
    parent.mkdir(parents=True)
    parent.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "parent-pkg",
                "version": "1.0.0",
                "license": "MIT",
                "dependencies": {"shared-lib": "1.0.0"},
            },
        ),
    )
    nested = parent / "node_modules" / "shared-lib"
    nested.mkdir(parents=True)
    nested.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "shared-lib",
                "version": "1.0.0",
                "license": "GPL-3.0",
            },
        ),
    )
    hoisted = tmp_path / "node_modules" / "shared-lib"
    hoisted.mkdir(parents=True)
    hoisted.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "shared-lib",
                "version": "2.0.0",
                "license": "MIT",
            },
        ),
    )

    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    shared = [p for p in packages if p.name == "shared-lib"]
    assert_that(shared).is_length(2)
    assert_that({p.version for p in shared}).is_equal_to({"1.0.0", "2.0.0"})


def test_npm_adapter_shadowed_denied_license_fails_policy(tmp_path: Path) -> None:
    """A denied license in a shadowed nested version is still evaluated.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "root",
                "dependencies": {"parent-pkg": "1.0.0"},
            },
        ),
    )
    parent = tmp_path / "node_modules" / "parent-pkg"
    parent.mkdir(parents=True)
    parent.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "parent-pkg",
                "version": "1.0.0",
                "license": "MIT",
                "dependencies": {"shared-lib": "1.0.0"},
            },
        ),
    )
    nested = parent / "node_modules" / "shared-lib"
    nested.mkdir(parents=True)
    nested.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "shared-lib",
                "version": "1.0.0",
                "license": "GPL-3.0",
            },
        ),
    )
    hoisted = tmp_path / "node_modules" / "shared-lib"
    hoisted.mkdir(parents=True)
    hoisted.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "shared-lib",
                "version": "2.0.0",
                "license": "MIT",
            },
        ),
    )

    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    results = engine.evaluate_all(packages)
    denied_names = [r.package.name for r in results if r.status is LicenseStatus.DENIED]
    assert_that(denied_names).contains("shared-lib")


def test_npm_adapter_classifies_transitive_dev_dependencies(tmp_path: Path) -> None:
    """Transitive dependencies of a dev dependency are classified as dev.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "root",
                "devDependencies": {"eslint": "1.0.0"},
            },
        ),
    )
    eslint = tmp_path / "node_modules" / "eslint"
    eslint.mkdir(parents=True)
    eslint.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "eslint",
                "version": "1.0.0",
                "license": "MIT",
                "dependencies": {"lodash": "4.0.0"},
            },
        ),
    )
    lodash = tmp_path / "node_modules" / "lodash"
    lodash.mkdir(parents=True)
    lodash.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "lodash",
                "version": "4.0.0",
                "license": "MIT",
            },
        ),
    )

    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    by_name = {p.name: p for p in packages}
    assert_that(by_name["eslint"].is_dev).is_true()
    assert_that(by_name["lodash"].is_dev).is_true()


def test_npm_adapter_classifies_optional_and_peer_children_of_dev(
    tmp_path: Path,
) -> None:
    """Optional/peer children of a dev dependency inherit dev classification.

    A dev-only tool's ``optionalDependencies`` and ``peerDependencies`` are
    installed under node_modules; they must inherit the parent's dev flag so
    ``ignore_dev_dependencies=True`` does not evaluate them as production.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "root",
                "devDependencies": {"toolkit": "1.0.0"},
            },
        ),
    )
    toolkit = tmp_path / "node_modules" / "toolkit"
    toolkit.mkdir(parents=True)
    toolkit.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "toolkit",
                "version": "1.0.0",
                "license": "MIT",
                "optionalDependencies": {"opt-child": "1.0.0"},
                "peerDependencies": {"peer-child": "1.0.0"},
            },
        ),
    )
    for child in ("opt-child", "peer-child"):
        child_dir = tmp_path / "node_modules" / child
        child_dir.mkdir(parents=True)
        child_dir.joinpath("package.json").write_text(
            json.dumps(
                {"name": child, "version": "1.0.0", "license": "GPL-3.0-only"},
            ),
        )

    packages = NpmLicenseAdapter().get_licenses_from_package_json(
        tmp_path / "package.json",
    )
    by_name = {p.name: p for p in packages}
    assert_that(by_name["toolkit"].is_dev).is_true()
    assert_that(by_name["opt-child"].is_dev).is_true()
    assert_that(by_name["peer-child"].is_dev).is_true()
