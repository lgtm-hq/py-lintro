"""Tool version requirements and checking utilities.

This module centralizes version management for external lintro tools.

## Version Sources

External tool versions come from two sources:
1. npm tools (prettier, oxlint, etc.): Read from package.json at runtime
2. Non-npm tools (hadolint, shellcheck, etc.): Defined in _tool_versions.py

Both are accessed via the get_tool_version() and get_all_expected_versions()
functions in lintro/_tool_versions.py.

Bundled Python tools (ruff, black, bandit, mypy, yamllint) are managed
via pyproject.toml dependencies and don't need tracking in _tool_versions.py.

## Adding a New Tool

### For npm Tools:
1. Add to package.json devDependencies
2. Add mapping in _NPM_PACKAGE_TO_TOOL in _tool_versions.py
3. Renovate updates package.json automatically

### For Non-npm External Tools:
1. Add to TOOL_VERSIONS in _tool_versions.py
2. Add Renovate regex manager in renovate.json

### For Bundled Python Tools:
1. Add as dependency in pyproject.toml
2. Renovate tracks it automatically
"""

import os
import threading

from loguru import logger

from lintro._tool_versions import (
    _NPM_PACKAGE_TO_TOOL,
    get_all_expected_versions,
)
from lintro.enums.tool_name import ToolName

# Module-level set to track logged warnings and prevent duplicates
# during parallel execution
_logged_warnings: set[str] = set()
_logged_warnings_lock: threading.Lock = threading.Lock()


def _get_version_timeout() -> int:
    """Return the validated version check timeout.

    Returns:
        int: Timeout in seconds; falls back to default when the env var is invalid.
    """
    default_timeout = 30
    env_value = os.getenv("LINTRO_VERSION_TIMEOUT")
    if env_value is None:
        return default_timeout

    try:
        timeout = int(env_value)
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid LINTRO_VERSION_TIMEOUT '{env_value}'; "
            f"using default {default_timeout}",
        )
        return default_timeout

    if timeout < 1:
        logger.warning(
            f"LINTRO_VERSION_TIMEOUT must be >= 1; using default {default_timeout}",
        )
        return default_timeout

    return timeout


VERSION_CHECK_TIMEOUT: int = _get_version_timeout()


def get_minimum_versions() -> dict[str, str]:
    """Get minimum version requirements for external tools.

    Returns versions from _tool_versions module for tools that users
    must install separately. Includes both npm-managed tools (from package.json)
    and non-npm tools (from TOOL_VERSIONS).

    Returns:
        dict[str, str]: Dictionary mapping tool names (as strings) to minimum
            version strings. Includes string equivalents of ToolName enums
            (e.g., "pytest") and package aliases (e.g., "typescript" for TSC).
    """
    result: dict[str, str] = {}

    # Get all versions (both npm and non-npm tools)
    all_versions = get_all_expected_versions()

    # Convert ToolName keys to their string values
    for tool_name, version in all_versions.items():
        if isinstance(tool_name, ToolName):
            result[tool_name.value] = version
        else:
            result[tool_name] = version

    # Add npm package aliases (e.g., "typescript" -> tsc version)
    for npm_pkg, tool_name in _NPM_PACKAGE_TO_TOOL.items():
        npm_version = all_versions.get(tool_name)
        if npm_version is not None:
            result[npm_pkg] = npm_version

    return result


def get_install_hints() -> dict[str, str]:
    """Generate installation hints for external tools.

    Returns:
        dict[str, str]: Dictionary mapping tool names to installation hint strings.
    """
    # Static templates mapping tool -> install hint template with {version} placeholder
    templates: dict[str, str] = {
        "bandit": (
            "Install via: pip install bandit>={version} or uv add bandit>={version}"
        ),
        "black": (
            "Install via: pip install black>={version} or uv add black>={version}"
        ),
        "mypy": ("Install via: pip install mypy>={version} or uv add mypy>={version}"),
        "pydoclint": (
            "Install via: pip install pydoclint>={version} "
            "or uv add pydoclint>={version}"
        ),
        "ruff": ("Install via: pip install ruff>={version} or uv add ruff>={version}"),
        "yamllint": (
            "Install via: pip install yamllint>={version} or uv add yamllint>={version}"
        ),
        "pytest": (
            "Install via: pip install pytest>={version} or uv add pytest>={version}"
        ),
        "markdownlint": "Install via: bun add -d markdownlint-cli2@>={version}",
        "markdownlint-cli2": "Install via: bun add -d markdownlint-cli2@>={version}",
        "oxfmt": "Install via: bun add -d oxfmt@>={version}",
        "oxlint": "Install via: bun add -d oxlint@>={version}",
        "prettier": "Install via: bun add -d prettier@>={version}",
        "tsc": (
            "Install via: bun add -g typescript@{version}, "
            "npm install -g typescript@{version}, or brew install typescript"
        ),
        "typescript": (
            "Install via: bun add -g typescript@{version}, "
            "npm install -g typescript@{version}, or brew install typescript"
        ),
        "hadolint": (
            "Install via: https://github.com/hadolint/hadolint/releases (v{version}+)"
        ),
        "actionlint": (
            "Install via: https://github.com/rhysd/actionlint/releases (v{version}+)"
        ),
        "clippy": "Install via: rustup component add clippy (requires Rust {version}+)",
        "rustc": (
            "Install via: rustup toolchain install {version} "
            "&& rustup default {version}"
        ),
        "rustfmt": "Install via: rustup component add rustfmt (v{version}+)",
        "cargo_audit": "Install via: cargo install cargo-audit (v{version}+)",
        "cargo_deny": "Install via: cargo install cargo-deny (v{version}+)",
        "biome": "Install via: bun add -d @biomejs/biome@>={version}",
        "semgrep": (
            "Install via: pip install semgrep>={version} or brew install semgrep"
        ),
        "gitleaks": (
            "Install via: https://github.com/gitleaks/gitleaks/releases (v{version}+)"
        ),
        "osv_scanner": (
            "Install via: https://github.com/google/osv-scanner/releases (v{version}+)"
        ),
        "shellcheck": (
            "Install via: https://github.com/koalaman/shellcheck/releases (v{version}+)"
        ),
        "shfmt": "Install via: https://github.com/mvdan/sh/releases (v{version}+)",
        "sqlfluff": (
            "Install via: pip install sqlfluff>={version} or uv add sqlfluff>={version}"
        ),
        "taplo": (
            "Install via: cargo install taplo-cli "
            "or download from https://github.com/tamasfe/taplo/releases (v{version}+)"
        ),
        "astro_check": (
            "Install via: bun add astro@>={version} or npm install astro@>={version}"
        ),
        "astro": (
            "Install via: bun add astro@>={version} or npm install astro@>={version}"
        ),
        "svelte_check": (
            "Install via: bun add -D svelte-check@>={version} "
            "or npm install -D svelte-check@>={version}"
        ),
        "svelte-check": (
            "Install via: bun add -D svelte-check@>={version} "
            "or npm install -D svelte-check@>={version}"
        ),
        "vue_tsc": (
            "Install via: bun add -D vue-tsc@>={version} "
            "or npm install -D vue-tsc@>={version}"
        ),
        "vue-tsc": (
            "Install via: bun add -D vue-tsc@>={version} "
            "or npm install -D vue-tsc@>={version}"
        ),
    }

    versions = get_minimum_versions()
    hints: dict[str, str] = {}

    # Build hints only for tools that exist in versions
    for tool, template in templates.items():
        version = versions.get(tool)
        if version is not None:
            hints[tool] = template.format(version=version)

    # Warn about tools in versions that don't have templates (only once)
    missing = set(versions) - set(templates)
    if missing:
        warning_key = f"missing_hints:{','.join(sorted(missing))}"
        with _logged_warnings_lock:
            if warning_key not in _logged_warnings:
                _logged_warnings.add(warning_key)
                logger.warning(
                    f"Missing install hints for tools: {', '.join(sorted(missing))}",
                )

    return hints
