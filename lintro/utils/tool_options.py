"""Tool options parsing and coercion utilities.

Handles parsing of CLI tool options and coercing string values to typed Python values.
"""

from collections.abc import Mapping

from lintro.enums.boolean_string import BooleanString


def _coerce_value(raw: str) -> object:
    """Coerce a raw CLI value into a typed Python value.

    Rules:
    - "True"/"False" (case-insensitive) -> bool
    - "None"/"null" (case-insensitive) -> None
    - integer (e.g., 88) -> int
    - float (e.g., 0.75) -> float
    - list via pipe-delimited values (e.g., "E|F|W") -> list[str]
      Pipe is chosen to avoid conflict with the top-level comma separator.
    - otherwise -> original string

    Args:
        raw: str: Raw CLI value to coerce.

    Returns:
        object: Coerced value.
    """
    s = raw.strip()
    # Lists via pipe (e.g., select=E|F)
    if "|" in s:
        return [part.strip() for part in s.split("|") if part.strip()]

    low = s.lower()
    if low == BooleanString.TRUE:
        return True
    if low == BooleanString.FALSE:
        return False
    if low in {BooleanString.NONE, BooleanString.NULL}:
        return None

    # Try int
    try:
        return int(s)
    except ValueError:
        pass

    # Try float
    try:
        return float(s)
    except ValueError:
        pass

    return s


def parse_tool_options(tool_options: str | None) -> dict[str, dict[str, object]]:
    """Parse tool options string into a typed dictionary.

    Args:
        tool_options: str | None: String in format
            "tool:option=value,tool2:option=value". Multiple values for a single
            option can be provided using pipe separators (e.g., select=E|F).

    Returns:
        dict[str, dict[str, object]]: Mapping tool names to typed options.
    """
    if not tool_options:
        return {}

    tool_option_dict: dict[str, dict[str, object]] = {}
    for opt in tool_options.split(","):
        opt = opt.strip()
        if not opt:
            continue
        if ":" not in opt:
            # Skip malformed fragment
            continue
        tool_name, tool_opt = opt.split(":", 1)
        if "=" not in tool_opt:
            # Skip malformed fragment
            continue
        opt_name, opt_value = tool_opt.split("=", 1)
        tool_name = tool_name.strip().lower()
        opt_name = opt_name.strip()
        opt_value = opt_value.strip()
        if not tool_name or not opt_name:
            continue
        if tool_name not in tool_option_dict:
            tool_option_dict[tool_name] = {}
        tool_option_dict[tool_name][opt_name] = _coerce_value(opt_value)

    return tool_option_dict


#: Option names lintro sets internally. ``--tool-options`` may not supply them.
RESERVED_TOOL_OPTIONS = frozenset({"_baseline_coverage"})


def merge_private_tool_options(
    user_options: dict[str, dict[str, object]],
    private_options: Mapping[str, Mapping[str, object]] | None,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Drop reserved keys from user options, then add internal options.

    Args:
        user_options: Parsed ``--tool-options`` overrides.
        private_options: Internal per-tool options set by lintro itself.

    Returns:
        The merged options, and each dropped ``tool:option`` pair.
    """
    merged: dict[str, dict[str, object]] = {}
    dropped: list[str] = []
    for tool_name, options in user_options.items():
        kept: dict[str, object] = {}
        for name, value in options.items():
            if name in RESERVED_TOOL_OPTIONS:
                dropped.append(f"{tool_name}:{name}")
            else:
                kept[name] = value
        if kept:
            merged[tool_name] = kept
    for tool_name, internal in (private_options or {}).items():
        merged.setdefault(tool_name.lower(), {}).update(internal)
    return merged, dropped
