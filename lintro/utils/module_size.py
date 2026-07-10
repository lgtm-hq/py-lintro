"""Module-size gate for Lintro's own source tree.

This module implements a lightweight, warn-level guard against the
oversized-module pattern that has repeatedly recurred in Lintro's codebase
(see issue #1052). Ruff does not ship a file-length rule, so this check is
wired into the existing post-check pipeline (``lintro/utils/post_checks.py``)
rather than a dedicated linter.

Behaviour:
    * Counts physical lines per Python module under the scanned paths.
    * Emits a warning for any module whose line count exceeds the configured
      threshold and is not present in the baseline allowlist.
    * Never fails the build. The gate is warn-level only so it can land green
      while the historical violators are burned down.

Ratchet-down plan:
    The threshold starts at ``DEFAULT_MODULE_SIZE_THRESHOLD`` (800 lines) with
    the modules in ``DEFAULT_MODULE_SIZE_BASELINE`` grandfathered in. As those
    modules are refactored below the threshold, remove them from the baseline
    (in ``[tool.lintro.module_size]`` in ``pyproject.toml``). Once the baseline
    is empty, lower the threshold in steps (e.g. 800 -> 700 -> 600), tightening
    the gate over time. A possible future enhancement is to promote module-size
    enforcement to a first-class Lintro tool with fail-level support; that is
    intentionally out of scope here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from lintro.utils.path_filtering import walk_files_with_excludes

# Warn-level threshold in physical lines. Ratchet this down over time (see the
# module docstring) once the baseline has been burned down.
DEFAULT_MODULE_SIZE_THRESHOLD: int = 800

# Paths that are never subject to the module-size gate. These are intentionally
# large or generated: deliberate lint fixtures and vendored/virtual-env trees.
DEFAULT_MODULE_SIZE_EXCLUDES: tuple[str, ...] = (
    "test_samples/",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    "__pycache__/",
    ".lintro/",
)

# Historical violators grandfathered in at the time of implementation (issue
# #1052). Remove entries here as the modules are refactored below the
# threshold. Paths are matched against each discovered file's path suffix, so
# repo-relative POSIX paths are used.
DEFAULT_MODULE_SIZE_BASELINE: tuple[str, ...] = (
    "lintro/ai/review/orchestrator.py",
    "lintro/utils/tool_executor.py",
    "lintro/cli_utils/commands/doctor.py",
    "lintro/tools/definitions/tsc.py",
    "lintro/ai/review/checklist_builtin.py",
)


def _coerce_threshold(
    *,
    value: object,
) -> int:
    """Coerce a raw ``threshold`` config value into a positive integer.

    Booleans are rejected (``True``/``False`` are not meaningful line counts)
    and non-numeric values fall back to the default rather than raising.

    Args:
        value: Raw ``threshold`` value from configuration.

    Returns:
        int: The parsed threshold, or ``DEFAULT_MODULE_SIZE_THRESHOLD`` when the
        value is missing, non-numeric, or not strictly positive.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return DEFAULT_MODULE_SIZE_THRESHOLD
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MODULE_SIZE_THRESHOLD
    return threshold if threshold > 0 else DEFAULT_MODULE_SIZE_THRESHOLD


def _coerce_str_tuple(
    *,
    value: object,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    """Coerce a raw config value into a tuple of strings.

    Scalar strings are intentionally rejected (rather than iterated into
    individual characters) so a mistyped ``baseline``/``exclude`` value falls
    back to the provided default instead of silently misbehaving.

    Args:
        value: Raw config value, expected to be a list or tuple of strings.
        default: Fallback tuple used when ``value`` is not a valid sequence.

    Returns:
        tuple[str, ...]: The coerced string tuple, or ``default`` when the
        value is missing or not a list/tuple.
    """
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return default


def resolve_module_size_settings(
    *,
    config: Mapping[str, object],
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    """Resolve raw module-size config into validated settings.

    Coerces the optional ``[tool.lintro.module_size]`` values into concrete
    types, falling back to the module defaults for any missing or malformed
    entry. This keeps a mistyped config (e.g. a non-numeric ``threshold`` or a
    scalar ``baseline``/``exclude``) from crashing the warn-level gate.

    Args:
        config: Raw module-size configuration mapping.

    Returns:
        tuple[int, tuple[str, ...], tuple[str, ...]]: The resolved
        ``(threshold, baseline, exclude_patterns)``.
    """
    threshold = _coerce_threshold(value=config.get("threshold"))
    baseline = _coerce_str_tuple(
        value=config.get("baseline"),
        default=DEFAULT_MODULE_SIZE_BASELINE,
    )
    exclude_patterns = _coerce_str_tuple(
        value=config.get("exclude"),
        default=DEFAULT_MODULE_SIZE_EXCLUDES,
    )
    return threshold, baseline, exclude_patterns


@dataclass(frozen=True)
class OversizedModule:
    """A Python module that exceeds the module-size threshold.

    Attributes:
        path: Path to the offending module as discovered on disk.
        line_count: Number of physical lines in the module.
    """

    path: str
    line_count: int


def count_module_lines(
    *,
    file_path: str,
) -> int:
    """Count the physical lines in a file.

    Args:
        file_path: Path to the file to measure.

    Returns:
        int: Number of physical lines in the file. Returns 0 when the file
        cannot be read.
    """
    try:
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _normalize(path: str) -> str:
    """Normalize a path to a POSIX-style string for suffix matching.

    Args:
        path: Path to normalize.

    Returns:
        str: The path with backslashes converted to forward slashes.
    """
    return path.replace("\\", "/")


def _is_baselined(
    *,
    file_path: str,
    baseline: frozenset[str],
) -> bool:
    """Return whether a discovered file is in the baseline allowlist.

    A file matches when its normalized path equals a baseline entry or ends
    with ``/<entry>``. This lets baseline entries be repo-relative POSIX paths
    regardless of whether the scanner yields absolute or relative paths.

    Args:
        file_path: Discovered file path.
        baseline: Set of normalized baseline entries.

    Returns:
        bool: True when the file is baselined and should be skipped.
    """
    normalized = _normalize(file_path)
    for entry in baseline:
        if normalized == entry or normalized.endswith(f"/{entry}"):
            return True
    return False


def find_oversized_modules(
    *,
    paths: list[str],
    threshold: int = DEFAULT_MODULE_SIZE_THRESHOLD,
    baseline: tuple[str, ...] = DEFAULT_MODULE_SIZE_BASELINE,
    exclude_patterns: tuple[str, ...] = DEFAULT_MODULE_SIZE_EXCLUDES,
    include_venv: bool = False,
) -> list[OversizedModule]:
    """Find Python modules that exceed the module-size threshold.

    Baselined modules are skipped even when they exceed the threshold, so the
    gate can land green while historical violators are burned down.

    Args:
        paths: Files or directories to scan for Python modules.
        threshold: Maximum allowed physical line count. Modules with strictly
            more lines than this are reported.
        baseline: Grandfathered module paths to skip. Matched by path suffix.
        exclude_patterns: Gitignore-style patterns of paths to skip entirely.
        include_venv: Whether to descend into virtual-environment directories.

    Returns:
        list[OversizedModule]: Offending modules sorted by descending line
        count, then path. Empty when nothing exceeds the threshold.
    """
    baseline_set = frozenset(_normalize(entry) for entry in baseline)

    files = walk_files_with_excludes(
        paths=paths,
        file_patterns=["*.py"],
        exclude_patterns=list(exclude_patterns),
        include_venv=include_venv,
    )

    violations: list[OversizedModule] = []
    for file_path in files:
        if _is_baselined(file_path=file_path, baseline=baseline_set):
            continue
        line_count = count_module_lines(file_path=file_path)
        if line_count > threshold:
            display_path = _normalize(os.path.relpath(file_path))
            violations.append(
                OversizedModule(path=display_path, line_count=line_count),
            )

    violations.sort(key=lambda module: (-module.line_count, module.path))
    return violations
