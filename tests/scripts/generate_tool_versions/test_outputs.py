"""Tests for ``_generator.outputs`` renderers and target resolution."""

from __future__ import annotations

from types import ModuleType

import pytest
from assertpy import assert_that


def test_render_generated_module_is_sorted(gen: ModuleType) -> None:
    """Rendered output orders package keys alphabetically for stability.

    Args:
        gen: Imported generator module.
    """
    text = gen.render_generated_module(
        npm_versions={"zeta": "1.0.0", "alpha": "2.0.0"},
        pypi_versions={"beta": "3.0.0", "kappa": "4.0.0"},
    )
    npm_block = text.split("NPM_VERSIONS")[1].split("PYPI_VERSIONS")[0]
    assert_that(npm_block.index('"alpha"')).is_less_than(
        npm_block.index('"zeta"'),
    )
    pypi_block = text.split("PYPI_VERSIONS")[1]
    assert_that(pypi_block.index('"beta"')).is_less_than(
        pypi_block.index('"kappa"'),
    )


def test_render_manifest_preserves_inline_arrays(gen: ModuleType) -> None:
    """Manifest update edits only ``version`` strings, not formatting.

    Args:
        gen: Imported generator module.
    """
    inline = (
        "{\n"
        '  "language_map": ["python", "js", "ts"],\n'
        '  "tools": [\n'
        "    {\n"
        '      "name": "oxfmt",\n'
        '      "version": "0.0.0",\n'
        '      "install": {"type": "npm", "package": "oxfmt"}\n'
        "    },\n"
        "    {\n"
        '      "name": "pytest",\n'
        '      "version": "0.0.0",\n'
        '      "install": {"type": "pip", "package": "pytest"}\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    new_text = gen.render_manifest(
        current_text=inline,
        target_versions={"oxfmt": "0.43.0", "pytest": "9.0.3"},
    )

    assert_that(new_text).contains('"language_map": ["python", "js", "ts"]')
    assert_that(new_text).contains('"version": "0.43.0"')
    assert_that(new_text).contains('"version": "9.0.3"')


def test_render_manifest_allows_intervening_fields(gen: ModuleType) -> None:
    """Manifest update tolerates metadata between name and version.

    Args:
        gen: Imported generator module.
    """
    manifest = (
        "{\n"
        '  "tools": [\n'
        "    {\n"
        '      "name": "oxfmt",\n'
        '      "description": "formatter",\n'
        '      "version": "0.0.0",\n'
        '      "install": {"type": "npm", "package": "oxfmt"}\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    new_text = gen.render_manifest(
        current_text=manifest,
        target_versions={"oxfmt": "0.43.0"},
    )

    assert_that(new_text).contains('"description": "formatter"')
    assert_that(new_text).contains('"version": "0.43.0"')


def test_render_manifest_missing_entry_raises(gen: ModuleType) -> None:
    """A target name with no manifest entry triggers a clear error.

    Args:
        gen: Imported generator module.
    """
    with pytest.raises(gen.GenerationError, match="oxlint"):
        gen.render_manifest(
            current_text='{"tools": []}\n',
            target_versions={"oxlint": "1.0.0"},
        )


def test_build_target_versions_dispatches_by_install_type(
    gen: ModuleType,
) -> None:
    """``build_target_versions`` resolves each entry from the right source.

    Args:
        gen: Imported generator module.
    """
    manifest = {
        "tools": [
            {"name": "oxfmt", "install": {"type": "npm", "package": "oxfmt"}},
            {"name": "pytest", "install": {"type": "pip", "package": "pytest"}},
            {"name": "hadolint", "install": {"type": "binary"}},
        ],
    }
    targets = gen.build_target_versions(
        manifest_data=manifest,
        npm_versions={"oxfmt": "0.43.0"},
        pypi_versions={"pytest": "9.0.3"},
        binary_versions={"hadolint": "2.14.0"},
    )
    assert_that(targets).is_equal_to(
        {"oxfmt": "0.43.0", "pytest": "9.0.3", "hadolint": "2.14.0"},
    )


def test_build_target_versions_does_not_fallback_for_npm_entry(
    gen: ModuleType,
) -> None:
    """Misconfigured npm entries do not fall back to binary versions by name.

    Args:
        gen: Imported generator module.
    """
    manifest = {
        "tools": [
            {"name": "oxfmt", "install": {"type": "npm", "package": "missing"}},
        ],
    }
    targets = gen.build_target_versions(
        manifest_data=manifest,
        npm_versions={"other": "0.43.0"},
        pypi_versions={},
        binary_versions={"oxfmt": "9.9.9"},
    )

    assert_that(targets).is_empty()
