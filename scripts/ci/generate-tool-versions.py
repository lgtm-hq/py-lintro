#!/usr/bin/env python3
"""Generate ``lintro/_generated_versions.py`` and sync ``manifest.json`` versions.

Single writer for all tool-version artifacts derived from ``package.json`` and
``pyproject.toml``. The seed mapping at ``lintro/_tool_packages.py`` declares
which packages are tools (and which `ToolName` they own) and which are
companions.

Modes:
    default: write outputs, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 if outputs are already in sync, exit 2 on input error.

Stdlib-only on purpose: this script runs inside Renovate's container after
``postUpgradeTasks`` so it must not require pip-installed dependencies.
Requires Python 3.11+ for ``tomllib``. Helper modules live in the
``_generator`` package alongside this script; ``sys.path`` is bootstrapped
below so they import cleanly when the script is executed directly.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

# Make sibling ``_generator`` package importable when this file is run as a
# script (``python3 scripts/ci/generate-tool-versions.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Imports must follow the sys.path bootstrap above.
from _generator.errors import GenerationError  # noqa: E402
from _generator.inputs import (  # noqa: E402
    read_binary_tool_versions,
    read_package_json,
    read_pyproject_versions,
)
from _generator.outputs import (  # noqa: E402
    build_target_versions,
    render_generated_module,
    render_manifest,
    validate_seed_coverage,
)
from _generator.seed import Seed, parse_seed  # noqa: E402

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parents[2]

SEED_PATH = REPO_ROOT / "lintro" / "_tool_packages.py"
TOOL_VERSIONS_PATH = REPO_ROOT / "lintro" / "_tool_versions.py"
PACKAGE_JSON_PATH = REPO_ROOT / "package.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MANIFEST_PATH = REPO_ROOT / "lintro" / "tools" / "manifest.json"
GENERATED_PATH = REPO_ROOT / "lintro" / "_generated_versions.py"


def collect_outputs(seed: Seed) -> tuple[str, str]:
    """Compute desired ``_generated_versions.py`` and ``manifest.json`` text.

    Args:
        seed: Parsed seed mapping.

    Returns:
        Tuple of (generated module text, manifest text).

    Raises:
        GenerationError: If any input is missing, malformed, or inconsistent.
    """
    pkg_versions = read_package_json(
        PACKAGE_JSON_PATH,
        strict_packages=set(seed.npm_owners),
    )

    npm_versions: dict[str, str] = {}
    for pkg in seed.npm_owners:
        if pkg not in pkg_versions:
            raise GenerationError(
                f"npm package '{pkg}' from seed not found in package.json",
            )
        npm_versions[pkg] = pkg_versions[pkg]

    pypi_versions = read_pyproject_versions(
        PYPROJECT_PATH,
        set(seed.pypi_owners),
        repo_root=REPO_ROOT,
    )

    binary_versions = read_binary_tool_versions(TOOL_VERSIONS_PATH)

    manifest_current = MANIFEST_PATH.read_text()
    manifest_data = json.loads(manifest_current)
    target_versions = build_target_versions(
        manifest_data=manifest_data,
        npm_versions=npm_versions,
        pypi_versions=pypi_versions,
        binary_versions=binary_versions,
    )
    validate_seed_coverage(seed, target_versions)

    generated_text = render_generated_module(npm_versions, pypi_versions)
    manifest_text = render_manifest(manifest_current, target_versions)
    return generated_text, manifest_text


def diff_text(label: str, current: str, desired: str) -> str:
    """Return a unified diff between current and desired text, or empty.

    Args:
        label: File label used in the unified-diff header.
        current: Current file contents.
        desired: Desired file contents.

    Returns:
        Unified diff string, or empty when ``current == desired``.
    """
    if current == desired:
        return ""
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        desired.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate tool-version artifacts from canonical sources.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 with a diff if outputs would change; do not write.",
    )
    args = parser.parse_args(argv)

    try:
        seed = parse_seed(SEED_PATH)
        generated_text, manifest_text = collect_outputs(seed)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    current_generated = GENERATED_PATH.read_text() if GENERATED_PATH.exists() else ""
    current_manifest = MANIFEST_PATH.read_text()

    gen_diff = diff_text(
        str(GENERATED_PATH.relative_to(REPO_ROOT)),
        current_generated,
        generated_text,
    )
    manifest_diff = diff_text(
        str(MANIFEST_PATH.relative_to(REPO_ROOT)),
        current_manifest,
        manifest_text,
    )

    if args.check:
        if gen_diff or manifest_diff:
            sys.stdout.write(gen_diff)
            sys.stdout.write(manifest_diff)
            print(
                "\nDrift detected. Run scripts/ci/generate-tool-versions.py "
                "to regenerate.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        return EXIT_OK

    GENERATED_PATH.write_text(generated_text)
    MANIFEST_PATH.write_text(manifest_text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
