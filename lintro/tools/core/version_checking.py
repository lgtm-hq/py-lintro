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

All tool types must be added to manifest.json with the correct install type.
CI runs verify-manifest-sync.py which validates every manifest entry against
its authoritative source (pyproject.toml for pip, package.json for npm,
TOOL_VERSIONS for binary/cargo/rustup). PRs will fail if they drift.

### For npm Tools:
1. Add to package.json devDependencies
2. Add mapping in _NPM_PACKAGE_TO_TOOL in _tool_versions.py
3. Add entry to manifest.json with install.type = "npm"
4. Renovate updates package.json automatically

### For Non-npm External Tools (binary, cargo, rustup):
1. Add to TOOL_VERSIONS in _tool_versions.py
2. Add entry to manifest.json (version must match TOOL_VERSIONS)
3. Add Renovate regex manager in renovate.json for both files
4. If installable via Homebrew: verify the Homebrew formula provides a
   version compatible with the entry in TOOL_VERSIONS and manifest.json
   before adding depends_on to lintro.rb.template. Homebrew's depends_on
   cannot pin versions, so only add it when the Homebrew package version
   matches. If versions diverge, omit the depends_on line and document
   alternative install instructions (or add a note in renovate.json).

### For Bundled Python Tools:
1. Add as dependency in pyproject.toml
2. Add entry to manifest.json with install.type = "pip"
3. Renovate tracks it automatically
4. Note: Homebrew formula excludes bundled Python tools from the venv
   (configured in lgtm-hq/homebrew-tap formulas/lintro.yml). They are
   installed as separate Homebrew formulae and discovered via PATH,
   not python -m.
"""

import os
import threading
from pathlib import Path

from loguru import logger
from packaging.version import InvalidVersion, Version

from lintro._tool_versions import (
    _NPM_PACKAGE_TO_TOOL,
    get_all_minimum_versions,
)
from lintro.enums.tool_name import ToolName
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core import update_channels as update_channel_ops
from lintro.tools.core.update_channels import VersionAdvisory

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

    # Minimum compatible versions (manifest min_version when set)
    all_versions = get_all_minimum_versions()

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
        "commitlint": (
            "Install via: bun add -g @commitlint/cli@{version} "
            "@commitlint/config-conventional@{version}"
        ),
        "markdownlint": "Install via: bun add -d markdownlint-cli2@>={version}",
        "markdownlint-cli2": "Install via: bun add -d markdownlint-cli2@>={version}",
        "oxfmt": "Install via: bun add -d oxfmt@>={version}",
        "oxlint": "Install via: bun add -d oxlint@>={version}",
        "prettier": "Install via: bun add -d prettier@>={version}",
        "stylelint": "Install via: bun add -d stylelint@>={version}",
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
        "dotenv_linter": (
            "Install via: brew install dotenv-linter, "
            "cargo install dotenv-linter, or "
            "https://github.com/dotenv-linter/dotenv-linter/releases (v{version}+)"
        ),
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
        "vale": (
            "Install via: brew install vale "
            "or download from https://github.com/errata-ai/vale/releases (v{version}+)"
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


def build_version_advisory(
    *,
    tool: str,
    installed: str,
    latest_known: str,
    binary_path: str | Path | None = None,
    install_package: str | None = None,
    install_type: str | None = None,
    channel_override: UpdateChannel | str | None = None,
) -> VersionAdvisory | None:
    """Build a version advisory when installed is below the known pin.

    Uses pinned manifest / tool-versions expectations only — no network
    calls. When path detection yields ``UNKNOWN``, falls back to a soft
    mapping from the manifest ``install.type``.

    Args:
        tool: Canonical tool name.
        installed: Currently installed version string.
        latest_known: Pinned expected/recommended version.
        binary_path: Path to the installed binary for channel detection.
        install_package: Manifest package name override.
        install_type: Manifest install type used as a soft channel fallback.
        channel_override: Explicit channel (e.g. from manifest).

    Returns:
        :class:`VersionAdvisory` when ``installed < latest_known``, else None.
    """
    try:
        if _is_at_least(installed, latest_known):
            return None
    except ValueError:
        return None

    override = channel_override
    if override is None and binary_path is None:
        override = update_channel_ops.channel_from_install_type(install_type)

    advisory = update_channel_ops.build_version_advisory(
        tool=tool,
        installed=installed,
        latest_known=latest_known,
        binary_path=binary_path,
        install_package=install_package,
        channel_override=override,
    )

    # Path said UNKNOWN/STANDALONE but manifest install.type is known —
    # prefer that for a usable update command.
    if (
        advisory.channel in (UpdateChannel.UNKNOWN, UpdateChannel.STANDALONE)
        and channel_override is None
        and install_type is not None
    ):
        fallback = update_channel_ops.channel_from_install_type(install_type)
        if fallback is not None:
            return update_channel_ops.build_version_advisory(
                tool=tool,
                installed=installed,
                latest_known=latest_known,
                binary_path=binary_path,
                install_package=install_package,
                channel_override=fallback,
            )

    return advisory


def _is_at_least(installed: str, latest_known: str) -> bool:
    """Return True when installed version is >= latest_known.

    Args:
        installed: Installed version string.
        latest_known: Expected version string.

    Returns:
        True when installed meets or exceeds latest_known.

    Raises:
        ValueError: If either version string cannot be parsed.
    """
    try:
        left = Version(installed.lstrip("vV").split("-", 1)[0].split("+", 1)[0])
        right = Version(latest_known.lstrip("vV").split("-", 1)[0].split("+", 1)[0])
    except InvalidVersion as exc:
        raise ValueError(
            f"Unable to compare versions: {installed!r} vs {latest_known!r}",
        ) from exc
    return left >= right
