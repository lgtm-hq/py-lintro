"""Helper modules for the tool-version generator.

The entry script ``scripts/ci/generate-tool-versions.py`` bootstraps
``sys.path`` so this package is importable when the script is executed
directly (e.g. by Renovate's ``postUpgradeTasks``). Public helpers are
re-exported here for convenience.

Stdlib-only on purpose: the generator runs inside Renovate's container
without any pip-installed dependencies.
"""

from __future__ import annotations

from _generator.errors import GenerationError
from _generator.inputs import (
    read_binary_tool_versions,
    read_package_json,
    read_pyproject_versions,
)
from _generator.outputs import (
    build_target_versions,
    render_generated_module,
    render_manifest,
    validate_seed_coverage,
)
from _generator.seed import Seed, parse_seed

__all__ = [
    "GenerationError",
    "Seed",
    "build_target_versions",
    "parse_seed",
    "read_binary_tool_versions",
    "read_package_json",
    "read_pyproject_versions",
    "render_generated_module",
    "render_manifest",
    "validate_seed_coverage",
]
