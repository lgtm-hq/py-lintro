"""Project language and package manager detection.

Scans a project tree for language/framework indicators and available
package managers. Used by the ``setup`` and ``install`` commands and by
no-config first-run tool selection. Pass ``root=`` to inspect a directory
other than the current working directory.

Usage:
    from lintro.utils.project_detection import detect_project_languages

    langs = detect_project_languages()   # ["docker", "python", "typescript"]
    langs = detect_project_languages(root=Path("src"))
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

# Directories that never carry first-party project markers. Pruned during
# os.walk so vendored/generated trees are not even entered.
_VENDOR_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "vendor",
        ".git",
        "__pycache__",
        "build",
        "dist",
        "target",
        ".tox",
        "site-packages",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
    },
)


def _iter_project_files(cwd: Path) -> Iterator[Path]:
    """Yield first-party files under *cwd*, pruning vendored directories.

    Args:
        cwd: Project root to scan.

    Yields:
        Path: File paths that are not inside ``_VENDOR_SKIP_DIRS``.
    """
    for dirpath, dirnames, filenames in os.walk(cwd, topdown=True):
        dirnames[:] = [name for name in dirnames if name not in _VENDOR_SKIP_DIRS]
        for filename in filenames:
            yield Path(dirpath) / filename


def _has_project_file(
    cwd: Path,
    *,
    match: Callable[[Path], bool],
) -> bool:
    """Return True if any first-party file satisfies *match*.

    Args:
        cwd: Project root to scan.
        match: Predicate applied to each walked file.

    Returns:
        True when the first matching file is found.
    """
    return any(match(path) for path in _iter_project_files(cwd))


def _has_source_files(
    cwd: Path,
    *suffixes: str,
    exclude_name_suffix: str | None = None,
) -> bool:
    """Return True if any first-party file uses one of *suffixes*.

    Walks the tree with vendored directories pruned. Suffixes include the
    leading dot (e.g. ``.py``).

    Args:
        cwd: Project root to scan.
        *suffixes: Filename suffixes to accept (``".py"``, ``".js"``, …).
        exclude_name_suffix: Optional filename suffix to skip (e.g. ``.d.ts``).

    Returns:
        True when at least one matching first-party file exists.
    """
    suffix_set = {suffix.lower() for suffix in suffixes}

    def _match(path: Path) -> bool:
        if exclude_name_suffix is not None and path.name.endswith(exclude_name_suffix):
            return False
        return path.suffix.lower() in suffix_set or any(
            path.name.lower().endswith(suffix) for suffix in suffix_set
        )

    return _has_project_file(cwd, match=_match)


def _is_requirements_file(path: Path) -> bool:
    """Return True if *path* looks like a pip requirements file.

    Args:
        path: Candidate file.

    Returns:
        True for ``requirements*.txt`` or ``requirements/*.txt``.
    """
    if path.suffix != ".txt":
        return False
    return path.name.startswith("requirements") or path.parent.name == "requirements"


def _is_dotenv_file(path: Path) -> bool:
    """Return True if *path* is a dotenv file, not direnv or similar.

    Args:
        path: Candidate file.

    Returns:
        True for ``.env`` and ``.env.*`` (e.g. ``.env.local``), not ``.envrc``.
    """
    name = path.name
    return name == ".env" or name.startswith(".env.")


def _is_yaml_content(path: Path) -> bool:
    """Return True if *path* is generic YAML, not compose or Actions workflow.

    Args:
        path: Candidate file.

    Returns:
        True for first-party YAML that should select yamllint.
    """
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return False
    if path.name in {
        ".lintro-config.yaml",
        ".lintro-config.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }:
        return False
    parts = set(path.parts)
    return not (".github" in parts and "workflows" in parts)


def detect_project_languages(*, root: Path | None = None) -> list[str]:
    """Detect all languages and ecosystems in a project tree.

    Checks for Python, JavaScript/TypeScript (including Astro, Svelte, Vue),
    Rust, Go, Ruby, Shell, Docker, GitHub Actions, SQL, YAML, Markdown, TOML,
    HTML, CSS, and dotenv files by inspecting manifests, directories, and
    source-file extensions. Language tools still run in source-only trees that
    have no ``pyproject.toml`` / ``package.json`` / ``Cargo.toml``. Nested
    YAML/Markdown/shell files count; a single root README.md does not enable
    Markdown tools.

    Args:
        root: Directory (or file, whose parent is used) to scan. ``None``
            inspects ``Path.cwd()``.

    Returns:
        Sorted list of lowercase language/ecosystem identifiers.
    """
    cwd = (root or Path.cwd()).resolve()
    if cwd.is_file():
        cwd = cwd.parent
    langs: set[str] = set()

    # Python — manifests, requirements files, or a first-party ``*.py``.
    # Requirements files are discovered recursively (e.g. ``requirements/base.txt``
    # or ``services/api/requirements.txt``); vendored/generated trees are skipped
    # and next() short-circuits so at most one file is visited.
    if (
        (cwd / "pyproject.toml").exists()
        or (cwd / "setup.py").exists()
        or (cwd / "setup.cfg").exists()
        or (cwd / "Pipfile").exists()
        or _has_project_file(cwd, match=_is_requirements_file)
        or _has_source_files(cwd, ".py", ".pyi")
    ):
        langs.add("python")

    # JavaScript / TypeScript — package.json *or* source files so a folder of
    # ``.js`` / ``.ts`` without a manifest still selects prettier/oxlint/tsc.
    has_package_json = (cwd / "package.json").exists()
    all_deps: dict[str, object] = {}
    if has_package_json:
        try:
            import json

            pkg = json.loads((cwd / "package.json").read_text())
            if not isinstance(pkg, dict):
                pkg = {}
            deps = pkg.get("dependencies") or {}
            dev_deps = pkg.get("devDependencies") or {}
            if not isinstance(deps, dict):
                deps = {}
            if not isinstance(dev_deps, dict):
                dev_deps = {}
            all_deps = {**deps, **dev_deps}
        except (ImportError, OSError, ValueError):
            all_deps = {}

    if has_package_json or _has_source_files(
        cwd,
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
    ):
        langs.add("javascript")

    if (
        (cwd / "tsconfig.json").exists()
        or "typescript" in all_deps
        or _has_source_files(
            cwd,
            ".ts",
            ".tsx",
            ".mts",
            ".cts",
            exclude_name_suffix=".d.ts",
        )
    ):
        langs.add("typescript")

    if "astro" in all_deps or _has_source_files(cwd, ".astro"):
        langs.add("astro")
    if "svelte" in all_deps or _has_source_files(cwd, ".svelte"):
        langs.add("svelte")
    if "vue" in all_deps or _has_source_files(cwd, ".vue"):
        langs.add("vue")

    # Rust
    if (cwd / "Cargo.toml").exists() or _has_source_files(cwd, ".rs"):
        langs.add("rust")

    # Go
    if (cwd / "go.mod").exists() or _has_source_files(cwd, ".go"):
        langs.add("go")

    # Ruby
    if (cwd / "Gemfile").exists() or _has_source_files(cwd, ".rb"):
        langs.add("ruby")

    # Shell scripts anywhere in the tree (not only root / scripts/).
    if _has_source_files(cwd, ".sh"):
        langs.add("shell")

    # Docker (Dockerfile, docker-compose, and standalone compose files)
    if any(
        next(cwd.glob(pat), None) is not None
        for pat in (
            "Dockerfile*",
            "docker-compose*.yml",
            "docker-compose*.yaml",
            "compose.yml",
            "compose.yaml",
        )
    ):
        langs.add("docker")

    # GitHub Actions
    if (cwd / ".github" / "workflows").is_dir():
        langs.add("github_actions")

    # SQL
    if _has_source_files(cwd, ".sql"):
        langs.add("sql")

    # YAML (beyond compose / lintro config / Actions workflows).
    if _has_project_file(cwd, match=_is_yaml_content):
        langs.add("yaml")

    # Markdown (more than just a lone README; nested docs/ counts).
    md_count = 0
    for path in _iter_project_files(cwd):
        if path.suffix.lower() == ".md":
            md_count += 1
            if md_count >= 2:
                langs.add("markdown")
                break

    # TOML (beyond pyproject.toml / Cargo.toml)
    if _has_project_file(
        cwd,
        match=lambda path: (
            path.suffix.lower() == ".toml"
            and path.name not in ("pyproject.toml", "Cargo.toml")
        ),
    ):
        langs.add("toml")

    # Markup and stylesheets that language_map already knows about.
    if _has_source_files(cwd, ".html", ".htm"):
        langs.add("html")
    if _has_source_files(cwd, ".css", ".scss", ".sass", ".less"):
        langs.add("css")
    if _has_project_file(cwd, match=_is_dotenv_file):
        langs.add("dotenv")

    # Prose/documentation formats beyond Markdown (vale targets).
    docs_dir = cwd / "docs"
    has_rst = any(cwd.glob("*.rst")) or (
        docs_dir.is_dir() and any(docs_dir.glob("**/*.rst"))
    )
    if has_rst:
        langs.add("restructuredtext")
    has_adoc = any(cwd.glob("*.adoc")) or (
        docs_dir.is_dir() and any(docs_dir.glob("**/*.adoc"))
    )
    if has_adoc:
        langs.add("asciidoc")

    return sorted(langs)


def detect_package_managers() -> dict[str, str]:
    """Detect available package managers for the current project.

    Returns:
        Dict mapping manager name (e.g., ``"uv"``) to its manifest file
        (e.g., ``"pyproject.toml"``).
    """
    cwd = Path.cwd()
    managers: dict[str, str] = {}

    if (cwd / "pyproject.toml").exists():
        if shutil.which("uv"):
            managers["uv"] = "pyproject.toml"
        else:
            managers["pip"] = "pyproject.toml"
    elif (cwd / "setup.py").exists():
        managers["pip"] = "setup.py"

    if (cwd / "package.json").exists():
        if shutil.which("bun"):
            managers["bun"] = "package.json"
        else:
            managers["npm"] = "package.json"

    if (cwd / "Cargo.toml").exists() and shutil.which("cargo"):
        managers["cargo"] = "Cargo.toml"

    if (cwd / "go.mod").exists() and shutil.which("go"):
        managers["go"] = "go.mod"

    return managers
