"""Project language and package manager detection.

Scans the current working directory for language/framework indicators
and available package managers.  Used by the ``setup`` and ``install``
commands so that the detection logic lives in a single shared module.

Usage:
    from lintro.utils.project_detection import detect_project_languages

    langs = detect_project_languages()   # ["docker", "python", "typescript"]
"""

from __future__ import annotations

import shutil
from itertools import chain
from pathlib import Path


def detect_project_languages() -> list[str]:
    """Detect all languages and ecosystems in the current project.

    Checks for Python, JavaScript/TypeScript (including Astro, Svelte, Vue),
    Rust, Go, Ruby, Shell, Docker, GitHub Actions, SQL, YAML, Markdown, and
    TOML by inspecting manifest files, directory structure, and file extensions.

    Returns:
        Sorted list of lowercase language/ecosystem identifiers.
    """
    cwd = Path.cwd()
    langs: set[str] = set()

    # Python
    if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
        langs.add("python")

    # JavaScript / TypeScript
    if (cwd / "package.json").exists():
        langs.add("javascript")

        # TypeScript detection — next() short-circuits so at most one file
        # is visited per glob pattern, avoiding a full tree scan.
        if (
            (cwd / "tsconfig.json").exists()
            or next(
                (
                    p
                    for p in cwd.glob("**/*.ts")
                    if "node_modules" not in p.parts and not p.name.endswith(".d.ts")
                ),
                None,
            )
            is not None
            or next(
                (p for p in cwd.glob("**/*.tsx") if "node_modules" not in p.parts),
                None,
            )
            is not None
        ):
            langs.add("typescript")

        # Framework detection from package.json
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
            if "typescript" in all_deps:
                langs.add("typescript")
            if "astro" in all_deps:
                langs.add("astro")
            if "svelte" in all_deps:
                langs.add("svelte")
            if "vue" in all_deps:
                langs.add("vue")
        except (ImportError, OSError, ValueError):
            pass

    # Rust
    if (cwd / "Cargo.toml").exists():
        langs.add("rust")

    # Go
    if (cwd / "go.mod").exists():
        langs.add("go")

    # Ruby
    if (cwd / "Gemfile").exists():
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
    _skip_dirs = {"node_modules", ".venv", "venv", "vendor", ".git", "__pycache__"}
    if (
        next(
            (p for p in cwd.glob("**/*.sql") if not _skip_dirs.intersection(p.parts)),
            None,
        )
        is not None
    ):
        langs.add("sql")

    # Terraform (HCL sources anywhere in the tree, skipping vendored dirs)
    if (
        next(
            (p for p in cwd.glob("**/*.tf") if not _skip_dirs.intersection(p.parts)),
            None,
        )
        is not None
    ):
        langs.add("terraform")

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
