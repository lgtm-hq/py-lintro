"""Configuration loader for Lintro.

Loads configuration from .lintro-config.yaml with fallback to
[tool.lintro] in pyproject.toml for backward compatibility.

Supports the new tiered configuration model:
1. execution: What tools run and how
2. enforce: Cross-cutting settings (replaces 'global')
3. defaults: Fallback config when no native config exists
4. tools: Per-tool enable/disable and config source
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, NamedTuple

from loguru import logger

from lintro.config.deps_config import DepsConfig
from lintro.config.lintro_config import (
    EnforceConfig,
    ExecutionConfig,
    LintroConfig,
    LintroToolConfig,
    OutputConfig,
)
from lintro.config.review_config import (
    ReviewChecklistConfig,
    ReviewChecklistItemConfig,
    ReviewConfig,
)
from lintro.config.score_config import ScoreConfig
from lintro.config.watch_config import WatchConfig
from lintro.enums.config_key import ConfigKey
from lintro.exceptions.errors import ConfigurationError
from lintro.utils.path_utils import find_file_upward

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Default config file name
LINTRO_CONFIG_FILENAME = ".lintro-config.yaml"
LINTRO_CONFIG_FILENAMES = [
    ".lintro-config.yaml",
    ".lintro-config.yml",
    "lintro-config.yaml",
    "lintro-config.yml",
]

# Config sections that are valid in both ``.lintro-config.yaml`` and
# ``[tool.lintro]`` but are parsed by other loaders: ``module_size`` and
# ``post_checks`` by ``lintro.utils.config``, ``licenses`` by
# ``lintro.config.licenses_config``, and ``plugins`` by
# ``lintro.plugins.discovery``. They are part of the schema even though
# ``LintroConfig`` does not model them, so consumers that build an allowlist
# of known top-level keys must include them.
EXTERNALLY_HANDLED_SECTIONS: frozenset[str] = frozenset(
    {
        "licenses",
        "module_size",
        "plugins",
    },
)

# Flat pyproject-only ordering keys read by ``get_tool_order_config``. Unlike
# the sections above these have no ``.lintro-config.yaml`` equivalent.
PYPROJECT_ORDERING_KEYS: frozenset[str] = frozenset(
    {
        "tool_order_custom",
        "tool_priorities",
    },
)


def _find_config_file(start_dir: Path | None = None) -> Path | None:
    """Find .lintro-config.yaml by searching upward from start_dir.

    Args:
        start_dir: Directory to start searching from. Defaults to cwd.

    Returns:
        Path | None: Path to config file if found.
    """
    current = Path(start_dir) if start_dir else Path.cwd()
    current = current.resolve()
    return find_file_upward(current, LINTRO_CONFIG_FILENAMES)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file.

    Args:
        path: Path to YAML file.

    Returns:
        dict[str, Any]: Parsed YAML content.

    Raises:
        ImportError: If PyYAML is not installed.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required to load .lintro-config.yaml. "
            "Install it with: pip install pyyaml",
        )

    with path.open(encoding="utf-8") as f:
        content = yaml.safe_load(f)

    return content if isinstance(content, dict) else {}


def _load_pyproject_fallback() -> tuple[dict[str, Any], Path | None]:
    """Load [tool.lintro] from pyproject.toml as fallback.

    Searches upward from current directory for pyproject.toml, consistent
    with _find_config_file's search behavior.

    Returns:
        tuple[dict[str, Any], Path | None]: Tuple of (config data, path to
            pyproject.toml). Path is None if no pyproject.toml was found.
    """
    current = Path.cwd().resolve()

    while True:
        pyproject_path = current / "pyproject.toml"
        if pyproject_path.exists():
            try:
                with pyproject_path.open("rb") as f:
                    data = tomllib.load(f)
                tool = data.get("tool", {})
                if not isinstance(tool, dict):
                    logger.warning(
                        f"'tool' in {pyproject_path} is not a table; "
                        "ignoring pyproject fallback.",
                    )
                    return {}, None
                lintro = tool.get("lintro", {})
                if not isinstance(lintro, dict):
                    logger.warning(
                        f"'tool.lintro' in {pyproject_path} is not a table; "
                        "ignoring pyproject fallback.",
                    )
                    return {}, None
                return lintro, pyproject_path
            except tomllib.TOMLDecodeError as e:
                logger.warning(
                    f"Failed to parse pyproject.toml at {pyproject_path}: {e}",
                )
                return {}, None
            except OSError as e:
                logger.debug(f"Could not read pyproject.toml at {pyproject_path}: {e}")
                return {}, None

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return {}, None


def _parse_enforce_config(data: dict[str, Any]) -> EnforceConfig:
    """Parse enforce configuration section.

    Args:
        data: Raw 'enforce' or 'global' section from config.

    Returns:
        EnforceConfig: Parsed enforce configuration.
    """
    return EnforceConfig(
        line_length=data.get("line_length"),
        target_python=data.get("target_python"),
    )


def _parse_execution_config(data: dict[str, Any]) -> ExecutionConfig:
    """Parse execution configuration section.

    Args:
        data: Raw 'execution' section from config.

    Returns:
        ExecutionConfig: Parsed execution configuration.

    Raises:
        ValueError: If max_fix_retries is not a valid positive integer.
    """
    enabled_tools = data.get("enabled_tools", [])
    if isinstance(enabled_tools, str):
        enabled_tools = [enabled_tools]

    tool_order = data.get("tool_order", "priority")

    # Validate max_fix_retries
    raw_retries = data.get("max_fix_retries")
    if raw_retries is None:
        max_fix_retries = 3
    elif isinstance(raw_retries, bool):
        raise ValueError(
            "execution.max_fix_retries must be an integer, got bool",
        )
    elif isinstance(raw_retries, int):
        max_fix_retries = raw_retries
    elif isinstance(raw_retries, str):
        try:
            max_fix_retries = int(raw_retries.strip())
        except ValueError:
            raise ValueError(
                f"execution.max_fix_retries must be an integer, "
                f"got {type(raw_retries).__name__}: {raw_retries!r}",
            ) from None
    else:
        raise ValueError(
            f"execution.max_fix_retries must be an integer, "
            f"got {type(raw_retries).__name__}: {raw_retries!r}",
        )
    if not 1 <= max_fix_retries <= 10:
        raise ValueError(
            f"execution.max_fix_retries must be between 1 and 10, "
            f"got {max_fix_retries}",
        )

    # max_workers and artifacts are optional; when absent, ExecutionConfig
    # applies its own defaults (CPU count and empty list respectively). Only
    # forward them when present so the model defaults still win on omission.
    optional_fields: dict[str, Any] = {}
    if "max_workers" in data:
        optional_fields["max_workers"] = data["max_workers"]
    if "artifacts" in data:
        optional_fields["artifacts"] = data["artifacts"]

    return ExecutionConfig(
        enabled_tools=enabled_tools,
        tool_order=tool_order,
        fail_fast=data.get("fail_fast", False),
        parallel=data.get("parallel", True),
        auto_install_deps=data.get("auto_install_deps"),
        max_fix_retries=max_fix_retries,
        **optional_fields,
    )


def _parse_tool_config(tool_name: str, data: dict[str, Any]) -> LintroToolConfig:
    """Parse a single tool configuration.

    In the tiered model, tools only have enabled and optional config_source.

    Args:
        tool_name: Name of the tool being parsed (used in error messages).
        data: Raw tool configuration dict.

    Returns:
        LintroToolConfig: Parsed tool configuration.

    Raises:
        ValueError: If auto_install is not a boolean.
    """
    enabled = data.get("enabled", True)
    config_source = data.get("config_source")
    auto_install_raw = data.get("auto_install")
    auto_install: bool | None = None
    if isinstance(auto_install_raw, bool):
        auto_install = auto_install_raw
    elif auto_install_raw is not None:
        type_name = type(auto_install_raw).__name__
        raise ValueError(
            f"tools.{tool_name}.auto_install must be a boolean, got {type_name}",
        )

    return LintroToolConfig(
        enabled=enabled,
        config_source=config_source,
        auto_install=auto_install,
    )


def _parse_tools_config(data: dict[str, Any]) -> dict[str, LintroToolConfig]:
    """Parse all tool configurations.

    Each ``tools.<name>`` value must be a mapping (including ``{}``) or a
    boolean. A bare YAML entry such as ``tools.ruff:`` is null and is
    rejected.

    Args:
        data: Raw 'tools' section from config.

    Returns:
        dict[str, LintroToolConfig]: Tool configurations keyed by tool name.

    Raises:
        ValueError: If a tool name is not a string, or a tool entry is
            neither a mapping nor a boolean.
    """
    tools: dict[str, LintroToolConfig] = {}

    for tool_name, tool_data in data.items():
        if not isinstance(tool_name, str):
            raise ValueError(
                f"tool name must be a string, got {type(tool_name).__name__}.",
            )
        name = tool_name.lower()
        if isinstance(tool_data, dict):
            tools[name] = _parse_tool_config(
                name,
                tool_data,
            )
        elif isinstance(tool_data, bool):
            # Simple enabled/disabled flag
            tools[name] = LintroToolConfig(enabled=tool_data)
        else:
            actual = "null" if tool_data is None else type(tool_data).__name__
            raise ValueError(
                f"tools.{name} must be a mapping or boolean, got {actual}.",
            )

    return tools


def _parse_defaults(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse defaults configuration section.

    Args:
        data: Raw 'defaults' section from config.

    Returns:
        dict[str, dict[str, Any]]: Defaults configurations keyed by tool name.
    """
    defaults: dict[str, dict[str, Any]] = {}

    for tool_name, tool_defaults in data.items():
        if isinstance(tool_defaults, dict):
            defaults[tool_name.lower()] = tool_defaults

    return defaults


def _parse_review_checklist_item_config(data: Any) -> dict[str, Any]:
    """Filter unknown keys from a single custom checklist item mapping.

    Args:
        data: Raw checklist item mapping from config.

    Returns:
        Mapping containing only recognized checklist item fields.

    Raises:
        ValueError: When the checklist item entry is not a mapping.
    """
    if not isinstance(data, dict):
        msg = "review.checklist.items entries must be mappings"
        raise ValueError(msg)

    known_fields = set(ReviewChecklistItemConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        msg = f"Unknown review.checklist.items keys: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    return dict(data)


def _parse_review_checklist_config(data: Any) -> dict[str, Any]:
    """Filter unknown keys from the review checklist section.

    Args:
        data: Raw ``review.checklist`` mapping from config.

    Returns:
        Mapping containing only recognized checklist fields.

    Raises:
        ValueError: When the checklist section is not a mapping.
    """
    if not isinstance(data, dict):
        msg = "review.checklist config must be a mapping"
        raise ValueError(msg)

    known_fields = set(ReviewChecklistConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        msg = f"Unknown review.checklist keys: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    filtered = dict(data)
    items_data = filtered.get("items")
    if isinstance(items_data, list):
        parsed_items: list[dict[str, Any]] = []
        for item in items_data:
            if not isinstance(item, dict):
                msg = "review.checklist.items entries must be mappings"
                raise ValueError(msg)
            parsed_items.append(_parse_review_checklist_item_config(item))
        filtered["items"] = parsed_items
    return filtered


def _parse_review_config(data: Any) -> ReviewConfig:
    """Parse review configuration section.

    Args:
        data: Raw ``review`` section from config.

    Returns:
        ReviewConfig: Parsed review configuration.

    Raises:
        ValueError: When the review section is not a mapping.
    """
    if data is None:
        return ReviewConfig()
    if not isinstance(data, dict):
        msg = f"review config must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)
    if not data:
        return ReviewConfig()

    known_fields = set(ReviewConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        logger.warning(
            "Unknown review config keys ignored: {}",
            ", ".join(sorted(unknown)),
        )
    filtered = {key: value for key, value in data.items() if key in known_fields}
    checklist_data = filtered.get("checklist")
    if checklist_data is not None:
        filtered["checklist"] = _parse_review_checklist_config(checklist_data)
    return ReviewConfig(**filtered)


def _parse_output_config(data: Any) -> OutputConfig:
    """Parse the console output configuration section.

    Args:
        data: Raw ``output`` section from config.

    Returns:
        OutputConfig: Parsed output configuration.

    Raises:
        ValueError: When the output section is not a mapping or ``art`` is not
            a boolean.
    """
    if data is None:
        return OutputConfig()
    if not isinstance(data, dict):
        msg = f"output config must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)
    if not data:
        return OutputConfig()

    known_fields = set(OutputConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        logger.warning(
            "Unknown output config keys ignored: {}",
            ", ".join(sorted(unknown)),
        )
    filtered = {key: value for key, value in data.items() if key in known_fields}

    art = filtered.get("art")
    if art is not None and not isinstance(art, bool):
        msg = f"output.art must be a boolean, got {type(art).__name__}"
        raise ValueError(msg)

    return OutputConfig(**filtered)


def _parse_score_config(data: Any) -> ScoreConfig:
    """Parse the health score configuration section.

    Args:
        data: Raw ``score`` section from config.

    Returns:
        ScoreConfig: Parsed score configuration.

    Raises:
        ValueError: When the score section is not a mapping.
    """
    if not data:
        return ScoreConfig()
    if not isinstance(data, dict):
        msg = f"score config must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)
    known_fields = set(ScoreConfig.model_fields.keys())
    unknown = set(data.keys()) - known_fields
    if unknown:
        logger.warning(
            "Unknown score config keys ignored: {}",
            ", ".join(sorted(unknown)),
        )
    filtered = {key: value for key, value in data.items() if key in known_fields}
    return ScoreConfig(**filtered)

def _parse_deps_config(data: Any) -> DepsConfig:
    """Parse the ``deps`` configuration section.

    Args:
        data: Raw ``deps`` section from config.

    Returns:
        DepsConfig: Parsed dependency policy configuration.

    Raises:
        ValueError: When the deps section is not a mapping, or contains an
            unknown key. Because ``deps`` gates dependency-spec enforcement, a
            misspelled key (for example ``pollicy`` for ``policy``) must fail
            loudly rather than silently fall back to the default policy and let
            specs that should fail pass in CI.
    """
    if data is None:
        return DepsConfig()
    if not isinstance(data, dict):
        msg = f"deps config must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)
    if not data:
        return DepsConfig()

    known_fields = set(DepsConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        known = ", ".join(sorted(known_fields))
        msg = (
            f"Unknown deps config key(s): {', '.join(sorted(unknown))}. "
            f"Valid keys are: {known}"
        )
        raise ValueError(msg)
    return DepsConfig(**data)


def _parse_watch_config(data: Any) -> WatchConfig:
    """Parse the ``watch`` configuration section.

    Args:
        data: Raw ``watch`` section from config.

    Returns:
        WatchConfig: Parsed watch configuration.

    Raises:
        ValueError: When the watch section is not a mapping.
    """
    if data is None:
        return WatchConfig()
    if not isinstance(data, dict):
        msg = f"watch config must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)
    if not data:
        return WatchConfig()

    known_fields = set(WatchConfig.model_fields)
    unknown = set(data) - known_fields
    if unknown:
        logger.warning(
            "Unknown watch config keys ignored: {}",
            ", ".join(sorted(unknown)),
        )
    filtered = {key: value for key, value in data.items() if key in known_fields}
    if isinstance(filtered.get("tools"), str):
        filtered["tools"] = [filtered["tools"]]
    return WatchConfig(**filtered)


class _PyprojectLintroCatalog(NamedTuple):
    """Name catalog shared by the pyproject converter and validator.

    Attributes:
        known_tools: Tool names including hyphen/underscore aliases.
        tool_aliases: Alias-to-canonical tool name map.
        reserved_keys: Non-tool keys reserved under ``[tool.lintro]``.
        execution_keys: ``ExecutionConfig`` field names.
        enforce_keys: ``EnforceConfig`` field names.
    """

    known_tools: set[str]
    tool_aliases: dict[str, str]
    reserved_keys: set[str]
    execution_keys: frozenset[str]
    enforce_keys: frozenset[str]


def _pyproject_lintro_catalog() -> _PyprojectLintroCatalog:
    """Return known tools, aliases, and reserved keys for ``[tool.lintro]``.

    Shared by the pyproject converter and the config validator so YAML
    ``tools:`` entries and TOML tool tables accept the same name set
    (``ToolName``, legacy aliases, and installed plugins). Plugin names come
    from :func:`~lintro.plugins.discovery.get_known_plugin_tool_names`, which
    does not trigger a discovery pass. Execution and enforce key sets come
    from the Pydantic models so the converter and validator cannot drift.

    Returns:
        _PyprojectLintroCatalog: Known tool names (including aliases),
            alias-to-canonical map, reserved non-tool keys, and the
            execution/enforce field sets.
    """
    # Inline imports: ToolName is a static StrEnum that does not trigger
    # the plugin registry. Discovery is imported here to avoid a circular
    # dependency between config_loader and the tool subsystem.
    from lintro.enums.tool_name import ToolName
    from lintro.plugins.discovery import get_known_plugin_tool_names
    from lintro.utils.config import LEGACY_TOOL_SECTION_ALIASES

    known_tools = {t.value for t in ToolName} | {
        t.value.replace("_", "-") for t in ToolName
    }
    tool_aliases = dict(LEGACY_TOOL_SECTION_ALIASES)

    execution_keys = frozenset(ExecutionConfig.model_fields)
    enforce_keys = frozenset(EnforceConfig.model_fields)
    externally_handled_sections = set(EXTERNALLY_HANDLED_SECTIONS) | set(
        PYPROJECT_ORDERING_KEYS,
    )
    reserved_keys = (
        set(execution_keys)
        | set(enforce_keys)
        | externally_handled_sections
        | {
            "ai",
            "defaults",
            "deps",
            "output",
            "review",
            "score",
            "tool",
            "tools",
            "watch",
            ConfigKey.POST_CHECKS.value.lower(),
            ConfigKey.VERSIONS.value.lower(),
        }
    )
    reserved_keys |= {name.replace("_", "-") for name in reserved_keys}

    # ToolName never sees entry-point-discovered tools, so config for an
    # externally installed plugin used to be dropped on the floor (#1757).
    for plugin_name in get_known_plugin_tool_names():
        variants = {
            plugin_name,
            plugin_name.replace("_", "-"),
            plugin_name.replace("-", "_"),
        }
        for variant in variants:
            if variant not in known_tools and variant not in reserved_keys:
                tool_aliases.setdefault(variant, plugin_name)

    known_tools.update(tool_aliases.keys())
    return _PyprojectLintroCatalog(
        known_tools=known_tools,
        tool_aliases=tool_aliases,
        reserved_keys=reserved_keys,
        execution_keys=execution_keys,
        enforce_keys=enforce_keys,
    )


def known_config_tool_names() -> frozenset[str]:
    """Return tool names the loader accepts in configuration.

    Includes ``ToolName`` values (underscore and hyphen forms), legacy
    section aliases such as ``markdownlint-cli2``, and installed plugin
    names. Does not trigger plugin discovery.

    Returns:
        frozenset[str]: Recognized tool identifiers.
    """
    catalog = _pyproject_lintro_catalog()
    return frozenset(catalog.known_tools)


def _convert_pyproject_to_config(data: dict[str, Any]) -> dict[str, Any]:
    """Convert pyproject.toml [tool.lintro] format to .lintro-config.yaml format.

    The pyproject format uses flat tool sections like [tool.lintro.ruff],
    while .lintro-config.yaml uses nested tools: section.

    Args:
        data: Raw [tool.lintro] section from pyproject.toml.

    Returns:
        dict[str, Any]: Converted configuration in .lintro-config.yaml format.

    Raises:
        ValueError: If a nested ``execution`` or ``enforce`` value is not a
            mapping.
    """
    result: dict[str, Any] = {
        "enforce": {},
        "execution": {},
        "defaults": {},
        "tools": {},
        "ai": {},
        "review": {},
        "score": {},
        "output": {},
        "watch": {},
        "deps": {},
    }

    catalog = _pyproject_lintro_catalog()
    known_tools = catalog.known_tools
    tool_aliases = catalog.tool_aliases
    execution_keys = catalog.execution_keys
    enforce_keys = catalog.enforce_keys

    # Keys and sections that are valid under [tool.lintro] but are parsed by
    # other loaders, not by this converter. Listing them keeps the unknown-key
    # warning below from crying wolf about legitimate config.
    externally_handled_sections = set(EXTERNALLY_HANDLED_SECTIONS) | set(
        PYPROJECT_ORDERING_KEYS,
    )

    unknown_keys: list[str] = []

    for key, value in data.items():
        key_lower = key.lower()

        if key_lower in known_tools:
            # Tool-specific config - normalize aliases to canonical names
            canonical_name = tool_aliases.get(key_lower, key_lower)
            result["tools"][canonical_name] = value
        elif key_lower in ("tool", "tools") and isinstance(value, dict):
            # Nested per-tool table, mirroring the ``tools:`` section of
            # .lintro-config.yaml: ``[tool.lintro.tool.trufflehog]`` /
            # ``[tool.lintro.tools.trufflehog]``. Without this the whole table
            # was dropped, so ``enabled = false`` silently did nothing and the
            # tool kept running (#1716).
            for nested_key, nested_value in value.items():
                nested_lower = str(nested_key).lower()
                if nested_lower not in known_tools:
                    unknown_keys.append(f"{key_lower}.{nested_key}")
                    continue
                nested_canonical = tool_aliases.get(nested_lower, nested_lower)
                result["tools"].setdefault(nested_canonical, nested_value)
        elif key_lower == "execution":
            # Nested ``[tool.lintro.execution]`` is the structured form of
            # the same keys accepted flat under ``[tool.lintro]``.
            if not isinstance(value, dict):
                actual = "null" if value is None else type(value).__name__
                raise ValueError(
                    f"execution must be a mapping, got {actual}.",
                )
            result["execution"].update(
                {
                    nested_key.replace("-", "_"): nested_value
                    for nested_key, nested_value in value.items()
                },
            )
        elif key_lower == "enforce":
            if not isinstance(value, dict):
                actual = "null" if value is None else type(value).__name__
                raise ValueError(
                    f"enforce must be a mapping, got {actual}.",
                )
            result["enforce"].update(
                {
                    nested_key.replace("-", "_"): nested_value
                    for nested_key, nested_value in value.items()
                },
            )
        elif key in execution_keys or key.replace("-", "_") in execution_keys:
            # Execution config
            result["execution"][key.replace("-", "_")] = value
        elif key in enforce_keys or key.replace("-", "_") in enforce_keys:
            # Enforce config
            result["enforce"][key.replace("-", "_")] = value
        elif key_lower == ConfigKey.POST_CHECKS.value.lower():
            # Skip post_checks (handled separately)
            pass
        elif key_lower == ConfigKey.VERSIONS.value.lower():
            # Skip versions (handled separately)
            pass
        elif key_lower == ConfigKey.DEFAULTS.value.lower() and isinstance(value, dict):
            # Defaults section
            result["defaults"] = value
        elif key_lower == "ai" and isinstance(value, dict):
            # AI configuration section
            result["ai"] = value
        elif key_lower == "review":
            result["review"] = value
        elif key_lower == "score" and isinstance(value, dict):
            result["score"] = value
        elif key_lower == "output" and isinstance(value, dict):
            result["output"] = value
        elif key_lower == "watch":
            result["watch"] = value
        elif key_lower == "deps" and isinstance(value, dict):
            result["deps"] = value
        elif key_lower in externally_handled_sections:
            # Parsed elsewhere; nothing to convert here.
            pass
        else:
            unknown_keys.append(key)

    if unknown_keys:
        # Silent discard is what kept this bug and #1716 invisible.
        logger.warning(
            "Ignoring unrecognized [tool.lintro] config key(s): {}. They match "
            "no known tool, execution setting, or config section.",
            ", ".join(sorted(unknown_keys)),
        )

    return result


def load_config(
    config_path: Path | str | None = None,
    allow_pyproject_fallback: bool = True,
) -> LintroConfig:
    """Load Lintro configuration.

    Priority:
    1. Explicit config_path if provided
    2. .lintro-config.yaml found by searching upward
    3. [tool.lintro] in pyproject.toml fallback
    4. Default empty configuration

    Args:
        config_path: Explicit path to config file. If None, searches for
            .lintro-config.yaml.
        allow_pyproject_fallback: Whether to fall back to pyproject.toml
            if no .lintro-config.yaml is found.

    Returns:
        LintroConfig: Loaded configuration.

    Raises:
        ConfigurationError: When a parsed config value is invalid (for
            example a null ``tools.<name>`` entry or a non-mapping
            ``execution`` / ``enforce`` table).
    """
    data: dict[str, Any] = {}
    resolved_path: str | None = None

    try:
        # Try explicit path first
        if config_path:
            path = Path(config_path)
            if path.exists():
                data = _load_yaml_file(path)
                resolved_path = str(path.resolve())
                logger.debug(f"Loaded config from explicit path: {resolved_path}")
            else:
                logger.warning(f"Config file not found: {config_path}")

        # Try searching for .lintro-config.yaml
        if not data:
            found_path = _find_config_file()
            if found_path:
                data = _load_yaml_file(found_path)
                resolved_path = str(found_path.resolve())
                logger.debug(f"Loaded config from: {resolved_path}")

        # Fall back to pyproject.toml
        if not data and allow_pyproject_fallback:
            pyproject_data, pyproject_path = _load_pyproject_fallback()
            if pyproject_data:
                data = _convert_pyproject_to_config(pyproject_data)
                resolved_path = (
                    str(pyproject_path.resolve()) if pyproject_path else None
                )
                logger.debug(
                    "Using [tool.lintro] from pyproject.toml. "
                    "Consider migrating to .lintro-config.yaml",
                )

        return build_config_from_dict(data, resolved_path=resolved_path)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _require_mapping_section(
    data: dict[str, Any],
    section: str,
) -> dict[str, Any]:
    """Return ``section`` as a mapping, or ``{}`` when the key is absent.

    YAML spells an empty section as ``enforce:`` which deserializes to
    ``None``. ``dict.get(section, {})`` then returns that ``None``, and
    the typed parsers call ``.get`` / ``.items`` on it.

    Args:
        data: Normalized configuration mapping.
        section: Top-level key that must be a mapping when present.

    Returns:
        dict[str, Any]: The section mapping, or an empty mapping when
            the key is omitted.

    Raises:
        ValueError: When the key is present but is not a mapping.
    """
    if section not in data:
        return {}
    value = data[section]
    if isinstance(value, dict):
        return value
    actual = "null" if value is None else type(value).__name__
    raise ValueError(f"{section} must be a mapping, got {actual}")


def build_config_from_dict(
    data: dict[str, Any],
    resolved_path: str | None = None,
) -> LintroConfig:
    """Build a ``LintroConfig`` from an already-parsed configuration mapping.

    This runs the typed section parsers (which raise ``ValueError`` on invalid
    values) against a normalized config dict. It is shared by ``load_config``
    and by the config validator so that pyproject-derived data can be checked
    with the same typed logic without round-tripping through the YAML loader.

    Args:
        data: Normalized configuration mapping (post pyproject conversion).
        resolved_path: Resolved path recorded on the returned config, if any.

    Returns:
        LintroConfig: The fully parsed configuration.
    """
    enforce_config = _parse_enforce_config(
        _require_mapping_section(data=data, section="enforce"),
    )
    execution_config = _parse_execution_config(
        _require_mapping_section(data=data, section="execution"),
    )
    defaults = _parse_defaults(
        _require_mapping_section(data=data, section="defaults"),
    )
    tools_config = _parse_tools_config(
        _require_mapping_section(data=data, section="tools"),
    )
    # Stored verbatim: parsing belongs to the AI layer (issue #724).
    ai_config = data.get("ai") or {}
    review_config = _parse_review_config(data.get("review", {}))
    score_config = _parse_score_config(data.get("score", {}))
    output_config = _parse_output_config(data.get("output", {}))
    watch_config = _parse_watch_config(data.get("watch", {}))
    deps_config = _parse_deps_config(data.get("deps", {}))

    return LintroConfig(
        execution=execution_config,
        enforce=enforce_config,
        defaults=defaults,
        tools=tools_config,
        ai=ai_config,
        review=review_config,
        score=score_config,
        output=output_config,
        watch=watch_config,
        deps=deps_config,
        config_path=resolved_path,
    )


def get_default_config() -> LintroConfig:
    """Get a default configuration with sensible defaults.

    Returns:
        LintroConfig: Default configuration.
    """
    return LintroConfig(
        enforce=EnforceConfig(
            line_length=88,
            target_python=None,
        ),
        execution=ExecutionConfig(
            tool_order="priority",
        ),
    )


# Global singleton for loaded config
_loaded_config: LintroConfig | None = None


def get_config(reload: bool = False) -> LintroConfig:
    """Get the loaded configuration singleton.

    Args:
        reload: Force reload from disk.

    Returns:
        LintroConfig: Loaded configuration.
    """
    global _loaded_config

    if _loaded_config is None or reload:
        _loaded_config = load_config()

    return _loaded_config


def clear_config_cache() -> None:
    """Clear the configuration cache.

    Useful for testing or when config file has changed.
    """
    global _loaded_config
    _loaded_config = None
