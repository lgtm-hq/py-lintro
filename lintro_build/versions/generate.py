"""Orchestration for the tool-version generator.

Computes and writes ``lintro/_generated_versions.py`` and the ``version``
fields of ``lintro/tools/manifest.json`` from the canonical sources. The seed
mapping at ``lintro/_tool_packages.py`` declares which packages are tools (and
which ``ToolName`` they own) and which are companions. Semgrep is read from
``requirements-semgrep.txt`` instead of ``pyproject.toml`` so its resolver
stays isolated (#2104).

Modes:
    default: write outputs, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 if outputs are already in sync, exit 2 on input error.

Stdlib-only on purpose: the generator must run in minimal containers without
pip-installed dependencies. Requires Python 3.11+ for ``tomllib``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys

from ..exit_codes import EXIT_DRIFT, EXIT_INPUT_ERROR, EXIT_OK
from .errors import GenerationError
from .inputs import (
    read_binary_tool_versions,
    read_package_json,
    read_pyproject_versions,
    read_requirements_pin,
)
from .outputs import (
    build_target_versions,
    render_generated_module,
    render_manifest,
    validate_seed_coverage,
)
from .paths import GeneratorPaths
from .seed import Seed, parse_seed

# Pypi seed packages whose version is read from a requirements*.txt file
# rather than pyproject.toml. Values are paths relative to the repo root.
REQUIREMENTS_PYPI_SOURCES: dict[str, str] = {
    "semgrep": "requirements-semgrep.txt",
}


def collect_outputs(seed: Seed, paths: GeneratorPaths) -> tuple[str, str]:
    """Compute desired ``_generated_versions.py`` and ``manifest.json`` text.

    Args:
        seed: Parsed seed mapping.
        paths: Generator input/output paths.

    Returns:
        Tuple of (generated module text, manifest text).

    Raises:
        GenerationError: If any input is missing, malformed, or inconsistent.
    """
    pkg_versions = read_package_json(
        paths.package_json_path,
        strict_packages=set(seed.npm_owners),
    )

    npm_versions: dict[str, str] = {}
    for pkg in seed.npm_owners:
        if pkg not in pkg_versions:
            raise GenerationError(
                f"npm package '{pkg}' from seed not found in package.json",
            )
        npm_versions[pkg] = pkg_versions[pkg]

    requirements_packages = {
        pkg: paths.repo_root / filename
        for pkg, filename in REQUIREMENTS_PYPI_SOURCES.items()
        if pkg in seed.pypi_owners
    }
    pyproject_packages = set(seed.pypi_owners) - set(requirements_packages)
    pypi_versions: dict[str, str] = {}
    if pyproject_packages:
        pypi_versions.update(
            read_pyproject_versions(
                paths.pyproject_path,
                pyproject_packages,
                repo_root=paths.repo_root,
            ),
        )
    for pkg, req_path in requirements_packages.items():
        pypi_versions[pkg] = read_requirements_pin(
            req_path,
            pkg,
            repo_root=paths.repo_root,
        )

    binary_versions = read_binary_tool_versions(paths.tool_versions_path)

    try:
        manifest_current = paths.manifest_path.read_text()
        manifest_data = json.loads(manifest_current)
    except OSError as exc:
        raise GenerationError(f"manifest.json could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GenerationError(f"manifest.json is not valid JSON: {exc}") from exc
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


def main(
    argv: list[str] | None = None,
    *,
    paths: GeneratorPaths,
) -> int:
    """Run the tool-version generator.

    Args:
        argv: Optional argv override (for tests and callers).
        paths: Generator input/output paths.

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
        seed = parse_seed(paths.seed_path)
        generated_text, manifest_text = collect_outputs(seed, paths)
        current_manifest = _read_current_manifest(paths)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    current_generated = (
        paths.generated_path.read_text() if paths.generated_path.exists() else ""
    )

    gen_diff = diff_text(
        str(paths.generated_path.relative_to(paths.repo_root)),
        current_generated,
        generated_text,
    )
    manifest_diff = diff_text(
        str(paths.manifest_path.relative_to(paths.repo_root)),
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

    paths.generated_path.write_text(generated_text)
    paths.manifest_path.write_text(manifest_text)
    return EXIT_OK


def _read_current_manifest(paths: GeneratorPaths) -> str:
    """Read the committed manifest text for diffing.

    Args:
        paths: Generator input/output paths.

    Returns:
        Current ``manifest.json`` contents.

    Raises:
        GenerationError: If the manifest cannot be read, so a vanished file
            surfaces as exit code 2 rather than an unhandled ``OSError``.
    """
    try:
        return paths.manifest_path.read_text()
    except OSError as exc:
        raise GenerationError(f"manifest.json could not be read: {exc}") from exc
