"""Render generator outputs: ``_generated_versions.py`` and ``manifest.json``."""

from __future__ import annotations

import json
import re
from typing import Any

from _generator.errors import GenerationError
from _generator.seed import Seed

# Install types that resolve from ``binary_versions`` parsed out of
# ``lintro/_tool_versions.py``. Update this set when the manifest schema gains a
# new binary-like installer such as ``binary``, ``cargo``, or ``rustup``.
BINARY_INSTALL_TYPES = frozenset({"binary", "cargo", "rustup"})

GENERATED_HEADER = '''\
"""Auto-generated tool versions. Do not edit by hand.

Run ``python3 scripts/ci/generate-tool-versions.py`` to regenerate.

Sources:
    - package.json (npm devDependencies)
    - pyproject.toml (pypi dependency tables)
    - lintro/_tool_packages.py (seed mapping)
"""
'''


def render_generated_module(
    npm_versions: dict[str, str],
    pypi_versions: dict[str, str],
) -> str:
    """Render the contents of ``_generated_versions.py``.

    Output is formatter-idempotent: passes black, ruff, and prettier without
    modification. Keys are sorted so the output is byte-stable across runs on
    different filesystems.

    Args:
        npm_versions: Package name -> version, npm tools and companions.
        pypi_versions: Package name -> version, pypi tools.

    Returns:
        Full module source text, terminated with a single trailing newline.
    """
    parts: list[str] = [GENERATED_HEADER, "\n"]
    parts.append("NPM_VERSIONS: dict[str, str] = {\n")
    for pkg in sorted(npm_versions):
        parts.append(f'    "{pkg}": "{npm_versions[pkg]}",\n')
    parts.append("}\n\n")
    parts.append("PYPI_VERSIONS: dict[str, str] = {\n")
    for pkg in sorted(pypi_versions):
        parts.append(f'    "{pkg}": "{pypi_versions[pkg]}",\n')
    parts.append("}\n")
    return "".join(parts)


def build_target_versions(
    manifest_data: dict[str, Any],
    npm_versions: dict[str, str],
    pypi_versions: dict[str, str],
    binary_versions: dict[str, str],
) -> dict[str, str]:
    """Resolve each manifest entry's expected version from the right source.

    ``install.type`` determines the source: ``npm``/``pip`` look up the
    ``install.package`` in the relevant generated dict; binary-like installers
    are matched against ``binary_versions`` by manifest entry name.

    Args:
        manifest_data: Parsed manifest.json content.
        npm_versions: Package -> version for npm packages.
        pypi_versions: Package -> version for pypi packages.
        binary_versions: Manifest-name -> version for non-npm/non-pypi tools.

    Returns:
        Mapping of manifest entry name -> desired version.
    """
    targets: dict[str, str] = {}
    for entry in manifest_data.get("tools", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        install = entry.get("install") or {}
        install_type = install.get("type") if isinstance(install, dict) else None
        package = install.get("package") if isinstance(install, dict) else None

        if (
            install_type == "npm"
            and isinstance(package, str)
            and package in npm_versions
        ):
            targets[name] = npm_versions[package]
        elif (
            install_type == "pip"
            and isinstance(package, str)
            and package in pypi_versions
        ):
            targets[name] = pypi_versions[package]
        elif install_type in BINARY_INSTALL_TYPES and name in binary_versions:
            targets[name] = binary_versions[name]
    return targets


def render_manifest(current_text: str, target_versions: dict[str, str]) -> str:
    """Apply targeted ``version`` updates to ``manifest.json`` text.

    Edits only the ``"version": "..."`` field in each target tool object after
    that tool's ``"name"`` field. All other bytes of the file (whitespace,
    key order, inline-array formatting) are preserved — round-tripping
    through ``json.dumps`` would reflow inline arrays into a noisy diff.

    Args:
        current_text: Current manifest.json contents.
        target_versions: Mapping of manifest entry name -> desired version.

    Returns:
        New manifest.json text.

    Raises:
        GenerationError: If the manifest is malformed, a target name has no
            following ``version`` field, or appears more than once.
    """
    try:
        json.loads(current_text)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"manifest.json is not valid JSON: {exc}") from exc

    text = current_text
    for name, version in target_versions.items():
        pattern = re.compile(
            rf'("name":\s*"{re.escape(name)}".*?"version":\s*")[^"]+(")',
            re.DOTALL,
        )
        text, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
        if count == 0:
            raise GenerationError(
                f"manifest.json has no '{name}' entry with a following version "
                f"field. Add the entry, or restore the conventional "
                f"name-before-version key order.",
            )
        if count > 1:
            raise GenerationError(
                f"manifest.json has multiple '{name}' entries; this should be "
                f"impossible. Inspect the file.",
            )

    return text


def validate_seed_coverage(
    seed: Seed,
    target_versions: dict[str, str],
) -> None:
    """Ensure every seeded tool is reflected in the manifest target set.

    Args:
        seed: Parsed seed mapping.
        target_versions: Result of ``build_target_versions``.

    Raises:
        GenerationError: If a seeded npm/pypi tool has no manifest entry to
            update.
    """
    expected = set()
    for owners in (seed.npm_owners, seed.pypi_owners):
        for tool in owners.values():
            if tool is not None:
                expected.add(_toolname_to_manifest_name(tool))
    missing = sorted(expected - set(target_versions))
    if missing:
        raise GenerationError(
            f"manifest.json has no entry for seeded tool(s): {missing}. "
            f"Add manifest entries before running the generator.",
        )


def _toolname_to_manifest_name(toolname_attr: str) -> str:
    """Convert ``ToolName`` attribute (uppercase) to its manifest string.

    ``ToolName`` is a ``StrEnum`` with ``auto()``; values match member names
    lowercased. ``ASTRO_CHECK`` -> ``astro_check``.

    Args:
        toolname_attr: Attribute name as written in source (e.g. ``OXFMT``).

    Returns:
        Manifest-form name (e.g. ``oxfmt``).
    """
    return toolname_attr.lower()
