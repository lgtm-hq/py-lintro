"""Tool-version artifact generation.

Importable implementation behind ``scripts/ci/generate-tool-versions.py``
(moved here from ``scripts/ci/_generator/``). Public helpers are re-exported
for convenience.

Stdlib-only on purpose: the generator runs in minimal containers without any
pip-installed dependencies.
"""

from __future__ import annotations

from ..exit_codes import EXIT_DRIFT, EXIT_INPUT_ERROR, EXIT_OK
from .errors import GenerationError
from .generate import collect_outputs, diff_text, main
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

__all__ = [
    "EXIT_DRIFT",
    "EXIT_INPUT_ERROR",
    "EXIT_OK",
    "GenerationError",
    "GeneratorPaths",
    "Seed",
    "build_target_versions",
    "collect_outputs",
    "diff_text",
    "main",
    "parse_seed",
    "read_binary_tool_versions",
    "read_package_json",
    "read_pyproject_versions",
    "read_requirements_pin",
    "render_generated_module",
    "render_manifest",
    "validate_seed_coverage",
]
