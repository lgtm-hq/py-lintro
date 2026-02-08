"""Native configuration parsers for various tools.

Handles loading and parsing of tool-specific configuration files (JSON, YAML, etc.).
"""

import json
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.enums.tool_name import ToolName
from lintro.utils.jsonc import (
    strip_jsonc_comments as _strip_jsonc_comments,
)
from lintro.utils.jsonc import (
    strip_trailing_commas as _strip_trailing_commas,
)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Configuration file patterns for different tools
YAMLLINT_CONFIG_FILES = [".yamllint", ".yamllint.yaml", ".yamllint.yml"]
MARKDOWNLINT_CONFIG_FILES = [
    ".markdownlint.json",
    ".markdownlint.yaml",
    ".markdownlint.yml",
    ".markdownlint.jsonc",
]
TSC_CONFIG_FILES = ["tsconfig.json"]
MYPY_CONFIG_FILES = ["mypy.ini", ".mypy.ini"]
OXLINT_CONFIG_FILES = [".oxlintrc.json", "oxlint.json"]
OXFMT_CONFIG_FILES = [".oxfmtrc.json", ".oxfmtrc.jsonc"]


def _load_json_config(config_path: Path) -> dict[str, Any]:
    """Load and parse a JSON configuration file.

    Args:
        config_path: Path to the JSON configuration file.

    Returns:
        Parsed configuration as dict, or empty dict on error.
    """
    try:
        with config_path.open(encoding="utf-8") as f:
            loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError as e:
        logger.warning(
            f"Failed to parse JSON config {config_path}: {e.msg} "
            f"(line {e.lineno}, col {e.colno})",
        )
        return {}
    except FileNotFoundError:
        logger.debug(f"Config file not found: {config_path}")
        return {}
    except OSError as e:
        logger.debug(f"Could not read config file {config_path}: {e}")
        return {}


def _load_native_tool_config(tool_name: str) -> dict[str, Any]:
    """Load native configuration for a specific tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Native configuration dictionary
    """
    from lintro.utils.config import load_pyproject

    # Convert string to ToolName enum for consistent comparisons
    try:
        tool_enum = ToolName(tool_name)
    except ValueError:
        # Unknown tool, return empty config
        return {}

    pyproject = load_pyproject()
    tool_section_raw = pyproject.get("tool", {})
    tool_section = tool_section_raw if isinstance(tool_section_raw, dict) else {}

    # Tools with pyproject.toml config
    if tool_enum in (ToolName.RUFF, ToolName.BLACK, ToolName.BANDIT):
        config_value = tool_section.get(tool_name, {})
        return config_value if isinstance(config_value, dict) else {}

    # Yamllint: check native config files (not pyproject.toml)
    if tool_enum == ToolName.YAMLLINT:
        for config_file in YAMLLINT_CONFIG_FILES:
            config_path = Path(config_file)
            if config_path.exists():
                if yaml is None:
                    logger.debug(
                        f"[UnifiedConfig] Found {config_file} but yaml not installed",
                    )
                    return {}
                try:
                    with config_path.open(encoding="utf-8") as f:
                        content = yaml.safe_load(f)
                        return content if isinstance(content, dict) else {}
                except yaml.YAMLError as e:
                    logger.warning(
                        f"Failed to parse yamllint config {config_file}: {e}",
                    )
                except OSError as e:
                    logger.debug(f"Could not read yamllint config {config_file}: {e}")
        return {}

    # Markdownlint: check config files
    if tool_enum == ToolName.MARKDOWNLINT:
        for config_file in MARKDOWNLINT_CONFIG_FILES:
            config_path = Path(config_file)
            if not config_path.exists():
                continue

            # Handle JSON/JSONC files
            if config_file.endswith((".json", ".jsonc")):
                try:
                    with config_path.open(encoding="utf-8") as f:
                        content = f.read()
                        # Strip JSONC comments safely (preserves strings)
                        content = _strip_jsonc_comments(content)
                        loaded = json.loads(content)
                        return loaded if isinstance(loaded, dict) else {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to parse markdownlint config {config_file}: {e.msg} "
                        f"(line {e.lineno}, col {e.colno})",
                    )
                except FileNotFoundError:
                    logger.debug(f"Markdownlint config not found: {config_file}")
                except OSError as e:
                    logger.debug(f"Could not read markdownlint config: {e}")

            # Handle YAML files
            elif config_file.endswith((".yaml", ".yml")):
                if yaml is None:
                    logger.warning(
                        "PyYAML not available; cannot parse .markdownlint.yaml",
                    )
                    continue
                try:
                    with config_path.open(encoding="utf-8") as f:
                        content = yaml.safe_load(f)
                        # Handle multi-document YAML (coerce to dict)
                        if isinstance(content, list) and len(content) > 0:
                            logger.debug(
                                "[UnifiedConfig] Markdownlint YAML config "
                                "contains multiple documents, using first "
                                "document",
                            )
                            content = content[0]
                        if isinstance(content, dict):
                            return content
                except yaml.YAMLError as e:
                    logger.warning(
                        f"Failed to parse markdownlint config {config_path}: {e}",
                    )
                except (OSError, UnicodeDecodeError) as e:
                    logger.debug(
                        f"Could not read markdownlint config {config_path}: "
                        f"{type(e).__name__}: {e}",
                    )
        return {}

    # TSC (TypeScript Compiler): check tsconfig.json
    if tool_enum == ToolName.TSC:
        for config_file in TSC_CONFIG_FILES:
            config_path = Path(config_file)
            if config_path.exists():
                try:
                    content = config_path.read_text(encoding="utf-8")
                    # tsconfig.json may have comments and trailing commas (JSONC format)
                    content = _strip_jsonc_comments(content)
                    content = _strip_trailing_commas(content)
                    loaded = json.loads(content)
                    if isinstance(loaded, dict):
                        # Return a summary of the most relevant options
                        result: dict[str, Any] = {}
                        if "extends" in loaded:
                            result["extends"] = loaded["extends"]
                        if "compilerOptions" in loaded:
                            result["compilerOptions"] = loaded["compilerOptions"]
                        if "include" in loaded:
                            result["include"] = loaded["include"]
                        if "exclude" in loaded:
                            result["exclude"] = loaded["exclude"]
                        return result if result else loaded
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Failed to parse tsconfig.json: {e.msg} "
                        f"(line {e.lineno}, col {e.colno})",
                    )
                except OSError as e:
                    logger.debug(f"Could not read tsconfig.json: {e}")
        return {}

    # Mypy: check mypy.ini or pyproject.toml [tool.mypy]
    if tool_enum == ToolName.MYPY:
        # First check pyproject.toml [tool.mypy]
        mypy_config = tool_section.get("mypy", {})
        if isinstance(mypy_config, dict) and mypy_config:
            return mypy_config

        # Then check mypy.ini / .mypy.ini
        for config_file in MYPY_CONFIG_FILES:
            config_path = Path(config_file)
            if config_path.exists():
                try:
                    import configparser

                    parser = configparser.ConfigParser(interpolation=None)
                    parser.read(config_path, encoding="utf-8")
                    # Convert to dict, focusing on [mypy] section
                    result = {}
                    if "mypy" in parser:
                        result = dict(parser["mypy"])
                    return result
                except (configparser.Error, OSError, UnicodeDecodeError) as e:
                    logger.debug(f"Could not read mypy config {config_file}: {e}")
        return {}

    # Oxlint: check native config files
    if tool_enum == ToolName.OXLINT:
        for config_file in OXLINT_CONFIG_FILES:
            config_path = Path(config_file)
            if config_path.exists():
                return _load_json_config(config_path)
        return {}

    # Oxfmt: check native config files (supports JSONC comments)
    if tool_enum == ToolName.OXFMT:
        for config_file in OXFMT_CONFIG_FILES:
            config_path = Path(config_file)
            if not config_path.exists():
                continue
            try:
                with config_path.open(encoding="utf-8") as f:
                    content = f.read()
                    # Strip JSONC comments and trailing commas safely
                    if config_file.endswith(".jsonc"):
                        content = _strip_jsonc_comments(content)
                        content = _strip_trailing_commas(content)
                    loaded = json.loads(content)
                    return loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Failed to parse oxfmt config {config_file}: {e.msg} "
                    f"(line {e.lineno}, col {e.colno})",
                )
            except FileNotFoundError:
                logger.debug(f"Oxfmt config not found: {config_file}")
            except OSError as e:
                logger.debug(f"Could not read oxfmt config {config_file}: {e}")
        return {}

    return {}
