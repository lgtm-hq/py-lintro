"""Project language and package manager detection.

Scans the current working directory for language/framework indicators
and available package managers. Used by the ``setup`` and ``install``
commands and by no-config first-run tool selection.

Usage:
    from lintro.utils.project_detection import detect_project_languages

    langs = detect_project_languages()   # ["docker", "python", "typescript"]
"""

from __future__ import annotations

import shutil
from itertools import chain
from pathlib import Path

# Directories that never carry first-party project markers; excluded from the
# recursive scans below to avoid false positives from vendored/generated trees.
_VENDOR_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".venv", "venv", "vendor", ".git", "__pycache__"},
)


def _has_source_files(
    cwd: Path,
    *patterns: str,
    exclude_name_suffix: str | None = None,
) -> bool:
    """Return True if any non-vendored file matches one of *patterns*.

    Globs short-circuit on the first match so a large tree is not fully
    walked. Vendored and generated directories listed in
    ``_VENDOR_SKIP_DIRS`` are ignored.

    Args:
        cwd: Project root to scan.
        *patterns: Glob patterns relative to *cwd*.
        exclude_name_suffix: Optional filename suffix to skip (e.g. ``.d.ts``).

    Returns:
        True when at least one matching first-party file exists.
    """
    return (
        next(
            (
                path
                for path in chain.from_iterable(
                    cwd.glob(pattern) for pattern in patterns
                )
                if path.is_file()
                and not _VENDOR_SKIP_DIRS.intersection(path.parts)
                and (
                    exclude_name_suffix is None
                    or not path.name.endswith(exclude_name_suffix)
                )
            ),
            None,
        )
        is not None
    )


def detect_project_languages() -> list[str]:
    """Detect all languages and ecosystems in the current project.

    Checks for Python, JavaScript/TypeScript (including Astro, Svelte, Vue),
    Rust, Go, Ruby, Shell, Docker, GitHub Actions, SQL, YAML, Markdown, TOML,
    HTML, CSS, and dotenv files by inspecting manifests, directories, and
    source-file extensions. Language tools still run in source-only trees that
    have no ``pyproject.toml`` / ``package.json`` / ``Cargo.toml``.

    Returns:
        Sorted list of lowercase language/ecosystem identifiers.
    """
    cwd = Path.cwd()
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
        or _has_source_files(
            cwd,
            "**/requirements*.txt",
            "**/requirements/*.txt",
        )
        or _has_source_files(cwd, "**/*.py", "**/*.pyi")
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
        "**/*.js",
        "**/*.jsx",
        "**/*.mjs",
        "**/*.cjs",
    ):
        langs.add("javascript")

    if (
        (cwd / "tsconfig.json").exists()
        or "typescript" in all_deps
        or _has_source_files(
            cwd,
            "**/*.ts",
            "**/*.tsx",
            "**/*.mts",
            "**/*.cts",
            exclude_name_suffix=".d.ts",
        )
    ):
        langs.add("typescript")

    if "astro" in all_deps:
        langs.add("astro")
    if "svelte" in all_deps:
        langs.add("svelte")
    if "vue" in all_deps:
        langs.add("vue")

    # Rust
    if (cwd / "Cargo.toml").exists() or _has_source_files(cwd, "**/*.rs"):
        langs.add("rust")

    # Go
    if (cwd / "go.mod").exists() or _has_source_files(cwd, "**/*.go"):
        langs.add("go")

    # Ruby
    if (cwd / "Gemfile").exists() or _has_source_files(cwd, "**/*.rb"):
        langs.add("ruby")

    # Shell scripts (root *.sh or .sh files inside scripts/)
    scripts_dir = cwd / "scripts"
    if next(cwd.glob("*.sh"), None) is not None or (
        scripts_dir.is_dir() and next(scripts_dir.glob("*.sh"), None) is not None
    ):
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

    # SQL — next() short-circuits so only one file is visited.
    if (
        next(
            (
                p
                for p in cwd.glob("**/*.sql")
                if not _VENDOR_SKIP_DIRS.intersection(p.parts)
            ),
            None,
        )
        is not None
    ):
        langs.add("sql")

    # YAML (beyond config files — actual YAML content)
    config_names = {
        ".lintro-config.yaml",
        ".lintro-config.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
    if any(
        f.name not in config_names for f in chain(cwd.glob("*.yaml"), cwd.glob("*.yml"))
    ):
        langs.add("yaml")

    # Markdown (more than just README)
    for md_count, _ in enumerate(cwd.glob("*.md"), 1):
        if md_count >= 2:
            langs.add("markdown")
            break

    # TOML (beyond pyproject.toml / Cargo.toml)
    toml_files = [
        f for f in cwd.glob("*.toml") if f.name not in ("pyproject.toml", "Cargo.toml")
    ]
    if toml_files:
        langs.add("toml")

    # Markup and stylesheets that language_map already knows about.
    if _has_source_files(cwd, "**/*.html", "**/*.htm"):
        langs.add("html")
    if _has_source_files(
        cwd,
        "**/*.css",
        "**/*.scss",
        "**/*.sass",
        "**/*.less",
    ):
        langs.add("css")
    if _has_source_files(cwd, "**/.env*"):
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
