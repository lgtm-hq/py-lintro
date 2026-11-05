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

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

from lintro._tool_versions import (
    _NPM_PACKAGE_TO_TOOL,
    get_all_minimum_versions,
    get_tool_version,
)
from lintro.config.lintro_config import LintroConfig
from lintro.enums.tool_name import ToolName
from lintro.enums.update_channel import UpdateChannel
from lintro.tools.core import update_channels as update_channel_ops
from lintro.tools.core.install_hints import ISOLATED_INSTALL_HINTS
from lintro.tools.core.update_channels import VersionAdvisory

# Module-level set to track logged warnings and prevent duplicates
# during parallel execution
_logged_warnings: set[str] = set()
_logged_warnings_lock: threading.Lock = threading.Lock()
_COMMITLINT_CONFIG_CONVENTIONAL_PACKAGE = "@commitlint/config-conventional"
_COMMITLINT_HINT_KEYS = {"commitlint", "@commitlint/cli"}


#: Ceiling for the retry budget of a timed-out version probe (#3083): the
#: retry doubles the first budget up to this value, and a first budget that
#: already meets it is retried unchanged.
VERSION_PROBE_RETRY_CAP: int = 120

#: Longest config-error detail quoted in the fallback warning; a longer one
#: is cut and ends in ``...``.
_CONFIG_ERROR_DETAIL_LIMIT: int = 200


def _warn_once(key: str, message: str) -> None:
    """Log *message* at warning level the first time *key* is seen.

    The version timeout is resolved once per probe, so a bad setting would
    otherwise repeat its warning for every tool checked.

    Args:
        key: Deduplication key, recorded in ``_logged_warnings``.
        message: Warning text to log.
    """
    with _logged_warnings_lock:
        if key in _logged_warnings:
            return
        _logged_warnings.add(key)
    logger.warning(message)


def _env_version_timeout(*, warn: bool = True) -> int | None:
    """Return the ``LINTRO_VERSION_TIMEOUT`` override when it is set and valid.

    Args:
        warn: Whether an invalid value is reported. When False the value is
            ignored silently and its warn-once key stays unused, so a later
            read still reports it.

    Returns:
        int | None: Timeout in seconds, or None when the env var is unset or
            invalid. An invalid value is ignored; with ``warn`` it is also
            logged, once per process per distinct value.
    """
    env_value = os.getenv("LINTRO_VERSION_TIMEOUT")
    if env_value is None:
        return None

    try:
        timeout = int(env_value)
    except (TypeError, ValueError):
        if warn:
            _warn_once(
                f"version_timeout_env_invalid:{env_value}",
                f"Invalid LINTRO_VERSION_TIMEOUT '{env_value}'; ignoring it",
            )
        return None

    if timeout < 1:
        if warn:
            _warn_once(
                f"version_timeout_env_below_one:{env_value}",
                "LINTRO_VERSION_TIMEOUT must be >= 1; ignoring it",
            )
        return None

    return timeout


def _get_version_timeout(*, warn: bool = True) -> int:
    """Return the version check timeout from the environment or the default.

    This ignores ``[tool.lintro.execution] version_check_timeout``; the
    version probe uses :func:`get_version_check_timeout` instead.

    Args:
        warn: Whether an invalid ``LINTRO_VERSION_TIMEOUT`` is reported.

    Returns:
        int: Timeout in seconds; falls back to default when the env var is invalid.
    """
    default_timeout = 30
    env_timeout = _env_version_timeout(warn=warn)
    return default_timeout if env_timeout is None else env_timeout


#: Import-time value of ``LINTRO_VERSION_TIMEOUT`` (or the default), kept for
#: backward compatibility. The version probe resolves its budget at call time
#: through :func:`get_version_check_timeout`. Read silently: the run's log
#: sinks (``debug.log``) do not exist at import time, and warning here would
#: use up the warn-once key, so the first run-time read reports a bad value.
VERSION_CHECK_TIMEOUT: int = _get_version_timeout(warn=False)


def get_version_check_timeout() -> int:
    """Return the effective version probe timeout, resolved at call time.

    Precedence: the ``LINTRO_VERSION_TIMEOUT`` env var, then
    ``[tool.lintro.execution] version_check_timeout``, then the default
    (``30``). A config that cannot be loaded, including malformed YAML,
    falls back to the default with one warning per distinct error per
    process.

    Returns:
        int: Timeout in seconds for one version probe attempt.
    """
    env_timeout = _env_version_timeout()
    if env_timeout is not None:
        return env_timeout

    from lintro.config.config_loader import get_config

    try:
        return get_config().execution.version_check_timeout
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        # Keying on the detail lets a different error later in the process
        # (watch mode) still warn once.
        detail = _config_error_detail(exc)
        _warn_once(
            f"version_timeout_config_unreadable:{detail}",
            "Could not read execution.version_check_timeout; using the "
            f"default version probe timeout: {detail}",
        )
        return _get_version_timeout()


def _config_error_detail(exc: BaseException) -> str:
    """Return a one-line description of why the config could not be read.

    PyYAML renders a parse error as several lines that open with the context
    (``while parsing a flow sequence``), and pydantic's opens with a count
    (``1 validation error for ExecutionConfig``); neither first line names
    the problem. The config loader re-raises a ``ValidationError`` as a
    ``ConfigurationError``, so the exception's ``__cause__`` is checked too.

    Args:
        exc: Error raised while loading the config.

    Returns:
        str: For a ``MarkedYAMLError``, its problem and position; for a
            ``ValidationError``, what :func:`_validation_error_detail`
            returns; otherwise the first non-empty line of the message. Line
            breaks are collapsed to single spaces, the result is cut to
            :data:`_CONFIG_ERROR_DETAIL_LIMIT` characters, and it is the
            exception's type name when there is no text.
    """
    detail = ""
    # Only ``exc`` and its ``__cause__`` are inspected: ``load_config``
    # re-raises with ``raise ConfigurationError(...) from exc``, which puts the
    # original error in ``__cause__``, so neither ``__context__`` nor a deeper
    # chain is walked.
    for error in (exc, exc.__cause__):
        if isinstance(error, yaml.MarkedYAMLError) and error.problem:
            detail = error.problem
            mark = error.problem_mark
            if mark is not None:
                # Marks are 0-based; PyYAML reports them 1-based.
                line, column = mark.line + 1, mark.column + 1
                detail += f" ({mark.name}, line {line}, column {column})"
            break
        if isinstance(error, ValidationError):
            detail = _validation_error_detail(error)
            if detail:
                break
    if not detail:
        lines = (line.strip() for line in str(exc).splitlines())
        detail = next((line for line in lines if line), "")
    # A YAML problem or a validator's message can span several lines, and the
    # warning is one line.
    detail = " ".join(line.strip() for line in detail.splitlines() if line.strip())
    if len(detail) > _CONFIG_ERROR_DETAIL_LIMIT:
        detail = detail[: _CONFIG_ERROR_DETAIL_LIMIT - 3].rstrip() + "..."
    return detail or type(exc).__name__


def _validation_error_detail(error: ValidationError) -> str:
    """Return the first error of *error* as ``location: message``.

    Args:
        error: Validation error raised while building a config model.

    Returns:
        str: The first error's dotted location, opened with its config
            section when :func:`_config_section_of` names one, then its
            message; an empty string when the error lists no errors.
    """
    errors = error.errors()
    if not errors:
        return ""
    first = errors[0]
    section = _config_section_of(error.title)
    parts = [section] if section else []
    parts.extend(str(part) for part in first["loc"])
    location = ".".join(parts)
    return f"{location}: {first['msg']}" if location else first["msg"]


def _config_section_of(model_name: str) -> str | None:
    """Return the config section that a model named *model_name* parses.

    A ``ValidationError``'s locations are relative to the model that raised
    it, and its ``title`` is that model's title: its class name unless its
    ``model_config`` sets a ``title``, which no section model does (a section
    model that set one would stop matching here). ``build_config_from_dict``
    builds each section model (``ExecutionConfig`` for ``execution``, ...)
    straight from its section mapping, so the section is the
    ``LintroConfig`` field typed with that model. A title is only a name, so
    a section is returned only when exactly one field's model carries it.
    The model class itself cannot be matched: pydantic's ``ValidationError``
    records the model's title, not the model.

    Args:
        model_name: ``ValidationError.title`` of the error.

    Returns:
        str | None: The section name, or ``None`` when no single top-level
            field is typed with a model of that name (``LintroConfig``
            itself, a model nested inside a section, or a mapping section
            such as ``tools``).
    """
    sections = [
        name
        for name, field in LintroConfig.model_fields.items()
        if isinstance(field.annotation, type)
        and issubclass(field.annotation, BaseModel)
        and field.annotation.__name__ == model_name
    ]
    return sections[0] if len(sections) == 1 else None


def version_probe_retry_timeout(timeout: float) -> float:
    """Return the budget for retrying a version probe that timed out.

    Args:
        timeout: Budget, in seconds, of the attempt that timed out.

    Returns:
        float: Double the budget, capped at :data:`VERSION_PROBE_RETRY_CAP`;
            a budget already at or above the cap is retried unchanged.
    """
    if timeout >= VERSION_PROBE_RETRY_CAP:
        return timeout
    return min(2 * timeout, VERSION_PROBE_RETRY_CAP)


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
    # Static templates mapping tool -> install hint template with placeholders.
    templates: dict[str, str] = {
        "bandit": (
            "Install via: pip install bandit>={version} or uv add bandit>={version}"
        ),
        "black": (
            "Install via: pip install black>={version} or uv add black>={version}"
        ),
        "buf": (
            "Install via: brew install bufbuild/buf/buf "
            "or download from https://github.com/bufbuild/buf/releases (v{version}+)"
        ),
        "import_linter": (
            "Install via: pip install 'import-linter>={version}' "
            "or uv add 'import-linter>={version}'"
        ),
        "import-linter": (
            "Install via: pip install 'import-linter>={version}' "
            "or uv add 'import-linter>={version}'"
        ),
        "mypy": ("Install via: pip install mypy>={version} or uv add mypy>={version}"),
        "pip_audit": (
            "Install via: pip install 'pip-audit>={version}' "
            "or uv add 'pip-audit>={version}'"
        ),
        "pip-audit": (
            "Install via: pip install 'pip-audit>={version}' "
            "or uv add 'pip-audit>={version}'"
        ),
        "pydoclint": (
            "Install via: pip install pydoclint>={version} "
            "or uv add pydoclint>={version}"
        ),
        "pylint": (
            "Install via: pip install 'pylint>={version}' or uv add 'pylint>={version}'"
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
            "@commitlint/config-conventional@{commitlint_config_conventional_version}"
        ),
        "@commitlint/cli": (
            "Install via: bun add -g @commitlint/cli@{version} "
            "@commitlint/config-conventional@{commitlint_config_conventional_version}"
        ),
        "html_validate": "Install via: bun add -d html-validate@>={version}",
        "html-validate": "Install via: bun add -d html-validate@>={version}",
        "markdownlint": "Install via: bun add -d markdownlint-cli2@>={version}",
        "markdownlint-cli2": "Install via: bun add -d markdownlint-cli2@>={version}",
        "oxfmt": "Install via: bun add -d oxfmt@>={version}",
        "oxlint": "Install via: bun add -d oxlint@>={version}",
        "prettier": "Install via: bun add -d prettier@>={version}",
        "spectral": "Install via: bun add -d @stoplight/spectral-cli@>={version}",
        "@stoplight/spectral-cli": (
            "Install via: bun add -d @stoplight/spectral-cli@>={version}"
        ),
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
        "cppcheck": (
            "Install via: brew install cppcheck (macOS) or "
            "apt-get install cppcheck (Debian/Ubuntu) (v{version}+)"
        ),
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
        "gitleaks": (
            "Install via: https://github.com/gitleaks/gitleaks/releases (v{version}+)"
        ),
        "golangci_lint": (
            "Install via: brew install golangci-lint or "
            "https://golangci-lint.run/welcome/install/ (v{version}+); "
            "requires the Go toolchain"
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
        "trufflehog": (
            "Install via: brew install trufflehog "
            "or download from "
            "https://github.com/trufflesecurity/trufflehog/releases (v{version}+)"
        ),
        "typos": (
            "Install via: cargo install typos-cli or brew install typos-cli "
            "(v{version}+)"
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

    # Tools that must live in their own venv carry one hint for every consumer
    # (doctor's table here, and the install strategies): merged from the single
    # registry rather than re-stated, so a third isolated tool needs one edit.
    templates.update(ISOLATED_INSTALL_HINTS)

    versions = get_minimum_versions()
    hints: dict[str, str] = {}

    # Build hints only for tools that exist in versions
    for tool, template in templates.items():
        version = versions.get(tool)
        if version is not None:
            template_values = {"version": version}
            if tool in _COMMITLINT_HINT_KEYS:
                # Fall back to the CLI version when the companion package
                # version cannot be resolved: a slightly-stale companion
                # version in an install hint beats no hint at all (#1663).
                companion_version = (
                    get_tool_version(_COMMITLINT_CONFIG_CONVENTIONAL_PACKAGE) or version
                )
                template_values["commitlint_config_conventional_version"] = (
                    companion_version
                )
            hints[tool] = template.format(**template_values)

    # Log tools in versions that don't have templates (only once).
    # Debug-level: the completeness gate in test_tool_completeness.py fails
    # CI when a registered tool or version key lacks a hint; a warning here
    # would only noise user runs for the same gap.
    missing = set(versions) - set(templates)
    if missing:
        warning_key = f"missing_hints:{','.join(sorted(missing))}"
        with _logged_warnings_lock:
            if warning_key not in _logged_warnings:
                _logged_warnings.add(warning_key)
                logger.debug(
                    f"Missing install hints for tools: {', '.join(sorted(missing))}",
                )

    return hints


def build_outdated_version_advisory(
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
    calls. When no binary path is available, falls back to a soft mapping
    from the manifest ``install.type``. Path-based ``UNKNOWN`` / ``STANDALONE``
    detections are kept honest — manifest ``install.type`` must not invent a
    pip/npm/cargo update command beside a system binary.

    Args:
        tool: Canonical tool name.
        installed: Currently installed version string.
        latest_known: Pinned expected/recommended version.
        binary_path: Path to the installed binary for channel detection.
        install_package: Manifest package name override.
        install_type: Manifest install type used when *binary_path* is absent.
        channel_override: Explicit channel (e.g. from manifest).

    Returns:
        :class:`VersionAdvisory` when ``installed < latest_known``, else None.
    """
    try:
        if _is_at_least(installed=installed, latest_known=latest_known):
            return None
    except ValueError:
        return None

    override = channel_override
    if override is None and binary_path is None:
        override = update_channel_ops.channel_from_install_type(install_type)

    return update_channel_ops.build_version_advisory(
        tool=tool,
        installed=installed,
        latest_known=latest_known,
        binary_path=binary_path,
        install_package=install_package,
        channel_override=override,
    )


def _is_at_least(*, installed: str, latest_known: str) -> bool:
    """Return True when installed version is >= latest_known.

    Delegates to :func:`lintro.tools.core.version_parsing.compare_versions` so
    doctor and ``lintro versions`` classify the same strings the same way.

    Args:
        installed: Installed version string.
        latest_known: Expected version string.

    Returns:
        True when installed meets or exceeds latest_known.
    """
    from lintro.tools.core.version_parsing import compare_versions

    return compare_versions(installed, latest_known) >= 0
