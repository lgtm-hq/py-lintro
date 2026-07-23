"""Path utilities for Lintro.

Small helpers to normalize paths for display consistency and path safety validation.
"""

from collections.abc import Iterable, Sequence
from itertools import islice
from pathlib import Path

from loguru import logger

# Bound the upward search for ``.lintro-ignore`` so it stays project-scoped and
# never loads an ignore file from a far filesystem ancestor. This preserves the
# historical ~20-directory limit that guarded the walk.
_IGNORE_SEARCH_MAX_DEPTH = 20


def absolute_path_without_resolving(path: Path) -> str:
    """Return an absolute path without resolving symlinks.

    Args:
        path: Path to convert.

    Returns:
        Absolute path with ``..`` segments normalized, matching
        ``os.path.abspath`` semantics without following symlinks.
    """
    absolute_path = path if path.is_absolute() else Path.cwd() / path
    normalized_parts: list[str] = []

    for part in absolute_path.parts:
        if part in {absolute_path.anchor, ""}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)

    return str(Path(absolute_path.anchor, *normalized_parts))


def find_file_upward(
    start: Path,
    filenames: Sequence[str],
    *,
    max_depth: int | None = None,
) -> Path | None:
    """Walk up from start to filesystem root, return first matching file.

    Starting at ``start``, each directory up to the filesystem root is
    checked for the candidate ``filenames`` in the order given. The first
    existing candidate encountered wins. The walk is bounded by
    ``Path.parents``, which terminates at the filesystem root, so no manual
    depth guard is required.

    Args:
        start: Directory to begin searching from.
        filenames: Candidate filenames to look for in each directory,
            checked in order.
        max_depth: Maximum number of directories to inspect, counting
            ``start`` itself. ``None`` (the default) searches all the way up
            to the filesystem root. Pass a positive integer to keep the walk
            project-scoped and avoid picking up config from far ancestors.

    Returns:
        Path to the first matching file found, or None if none exists
        anywhere within the searched directories.
    """
    directories: Iterable[Path] = (start, *start.parents)
    if max_depth is not None:
        directories = islice(directories, max_depth)
    for directory in directories:
        for name in filenames:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def validate_safe_path(path: str | Path, base_dir: Path | None = None) -> bool:
    """Validate that a path doesn't escape the project boundaries.

    This function prevents path traversal attacks by ensuring the resolved path
    stays within the specified base directory (or current working directory).

    Args:
        path: The path to validate (can be absolute or relative).
        base_dir: The base directory that paths must stay within.
                  Defaults to current working directory if not specified.

    Returns:
        True if the path is safe (within boundaries), False otherwise.

    Examples:
        >>> validate_safe_path("./src/file.py")  # Safe relative path
        True
        >>> validate_safe_path("../../../etc/passwd")  # Escapes project
        False
        >>> validate_safe_path("/absolute/path/outside")  # Outside project
        False
    """
    try:
        base = (base_dir or Path.cwd()).resolve()
        resolved = Path(path).resolve()

        # Check if resolved path is within base directory
        resolved.relative_to(base)
        return True
    except ValueError:
        # Path escapes the base directory
        return False
    except OSError:
        # Invalid path (e.g., too long, invalid characters on some systems)
        return False


def find_lintro_ignore() -> Path | None:
    """Find .lintro-ignore file by searching upward from current directory.

    Searches upward from the current working directory to find the project root
    by looking for .lintro-ignore or pyproject.toml files. The walk is bounded
    to ``_IGNORE_SEARCH_MAX_DEPTH`` directories so an unrelated ``.lintro-ignore``
    from a far filesystem ancestor is never picked up for the current run.

    Returns:
        Path | None: Path to .lintro-ignore file if found, None otherwise.
    """
    # Walk upward looking for either marker. Within a directory ``.lintro-ignore``
    # takes precedence over ``pyproject.toml``. Finding ``pyproject.toml`` first
    # marks the project root and short-circuits the search: return None because
    # no closer ``.lintro-ignore`` exists. The search is depth-bounded to keep it
    # project-scoped rather than walking all the way to the filesystem root.
    found = find_file_upward(
        Path.cwd(),
        [".lintro-ignore", "pyproject.toml"],
        max_depth=_IGNORE_SEARCH_MAX_DEPTH,
    )
    if found is not None and found.name == ".lintro-ignore":
        return found
    return None


def load_lintro_ignore() -> list[str]:
    """Load ignore patterns from .lintro-ignore file.

    Returns:
        list[str]: List of ignore patterns.
    """
    ignore_patterns: list[str] = []
    lintro_ignore_path = find_lintro_ignore()

    if lintro_ignore_path and lintro_ignore_path.exists():
        try:
            with open(lintro_ignore_path, encoding="utf-8") as f:
                for line in f:
                    line_stripped = line.strip()
                    if not line_stripped or line_stripped.startswith("#"):
                        continue
                    ignore_patterns.append(line_stripped)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to load .lintro-ignore: {e}")

    return ignore_patterns


def normalize_file_path_for_display(file_path: str) -> str:
    """Normalize file path to be relative to project root for consistent display.

    This ensures all tools show file paths in the same format:
    - Relative to project root (like ./src/file.py)
    - Consistent across all tools regardless of how they output paths

    Args:
        file_path: File path (can be absolute or relative). If empty, returns as is.

    Returns:
        Normalized relative path from project root (e.g., "./src/file.py")
    """
    # Fast-path: empty or whitespace-only input
    if not file_path or not str(file_path).strip():
        return file_path

    try:
        project_root = Path.cwd().resolve()
        abs_path = Path(file_path).resolve()

        # Attempt to make path relative to project root
        try:
            rel_path = abs_path.relative_to(project_root)
            rel_path_str = str(rel_path)

            # Ensure it starts with "./" for consistency
            if not rel_path_str.startswith("./"):
                rel_path_str = "./" + rel_path_str

            return rel_path_str

        except ValueError:
            # Path is outside project root - log warning and return with ../
            logger.debug(f"Path '{file_path}' is outside project root")
            # Use the original behavior for paths outside project
            # Calculate relative path that may include ../
            try:
                # Find common ancestor and build relative path
                rel_parts: list[str] = []
                # Walk up from project_root to find common ancestor
                project_parts = project_root.parts
                path_parts = abs_path.parts

                # Find common prefix length
                common_len = 0
                for p1, p2 in zip(project_parts, path_parts, strict=False):
                    if p1 == p2:
                        common_len += 1
                    else:
                        break

                # Build relative path
                ups = len(project_parts) - common_len
                rel_parts = [".."] * ups + list(path_parts[common_len:])
                return "/".join(rel_parts) if rel_parts else "."

            except (ValueError, IndexError):
                return file_path

    except (OSError, ValueError):
        # If path normalization fails, return the original path
        return file_path
