"""Inspect a frozen Click tree without invoking any command callbacks."""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import click

TS_FILE_SELECTION_NOTE = (
    "When true, request native project file selection. When false, explicit "
    "projects and native include/files/exclude scoping still take precedence; "
    "otherwise applicable branches can target the selected files."
)


def declared_default(parameter: click.Parameter, context: click.Context) -> str:
    """Describe a declared default without resolving config or calling factories.

    Args:
        parameter: Public Click parameter.
        context: Non-invoked help context.

    Returns:
        Human-readable declared value, or an explicit unresolved label.
    """
    value = parameter.get_default(context, call=False)
    if callable(value):
        return "Computed at invocation; see help"
    if value is None or type(value).__name__ == "Sentinel":
        return "Not set; runtime/config may apply"
    if isinstance(value, str) and value.startswith("__"):
        return "Invocation-dependent; see help"
    return json.dumps(value, ensure_ascii=False)


def parameter_record(
    parameter: click.Parameter,
    context: click.Context,
) -> dict[str, Any]:
    """Serialize a public parameter, including choices and paired options.

    Args:
        parameter: Parameter from the real command object.
        context: Help context used for Click's own default handling.

    Returns:
        JSON-compatible parameter reference.
    """
    return {
        "flags": [*parameter.opts, *parameter.secondary_opts],
        "name": parameter.name,
        "kind": "option" if isinstance(parameter, click.Option) else "argument",
        "help": getattr(parameter, "help", None)
        or "Positional argument; see command help.",
        "default": declared_default(parameter, context),
        "choices": (
            list(parameter.type.choices)
            if isinstance(parameter.type, click.Choice)
            else []
        ),
        "type": parameter.type.name,
        "required": parameter.required,
        "multiple": parameter.nargs == -1 or getattr(parameter, "multiple", False),
    }


def command_tree(command: click.Command, source_root: Path) -> list[dict[str, Any]]:
    """Expand actual public commands, shared decorators, aliases and factories.

    Args:
        command: Root Click command loaded from the frozen archive.
        source_root: Archive root for source-link provenance.

    Returns:
        Flat reference records with canonical full names and child names.
    """
    records: list[dict[str, Any]] = []

    def visit(current: click.Command, name: str, parent: click.Context | None) -> None:
        """Walk one public command without invoking it."""
        context = click.Context(
            current,
            info_name=name.split()[-1] if name else "lintro",
            parent=parent,
            **current.context_settings,
        )
        callback = inspect.unwrap(current.callback) if current.callback else None
        source = inspect.getsourcefile(callback) if callback else None
        children: dict[str, tuple[click.Command, list[str]]] = {}
        if isinstance(current, click.Group):
            for child_name in current.list_commands(context):
                child = current.get_command(context, child_name)
                if child is None or child.hidden:
                    continue
                canonical = getattr(child, "_canonical_name", child.name) or child_name
                children.setdefault(canonical, (child, []))
                if child_name != canonical:
                    children[canonical][1].append(child_name)
        with contextlib.redirect_stdout(io.StringIO()) as printed_help:
            help_text = current.get_help(context)
        help_text = printed_help.getvalue() + help_text
        records.append(
            {
                "name": name,
                "summary": inspect.cleandoc(current.help or ""),
                "help": help_text,
                "usage": current.get_usage(context),
                "options": [
                    parameter_record(p, context)
                    for p in current.get_params(context)
                    if not getattr(p, "hidden", False)
                ],
                "children": [f"{name} {child}".strip() for child in sorted(children)],
                "aliases": [],
                "source": (
                    str(Path(source).relative_to(source_root))
                    if source
                    else "lintro/cli.py"
                ),
                "line": inspect.getsourcelines(callback)[1] if callback else 1,
            },
        )
        for canonical, (child, aliases) in sorted(children.items()):
            index = len(records)
            visit(child, f"{name} {canonical}".strip(), context)
            records[index]["aliases"] = aliases

    visit(command, "", None)
    return records


def tool_details(source_root: Path) -> dict[str, Any]:
    """Read built-in definitions and option signatures without running tools.

    Args:
        source_root: Extracted frozen package copied into an isolated directory.

    Returns:
        Resolved native configuration, versions, options and source locations.

    Raises:
        RuntimeError: If a definition path does not map to a built-in tool module
            name, a definition module did not load, or an option method has no
            source file.
    """
    from lintro.plugins.discovery import discover_builtin_tools
    from lintro.plugins.registry import ToolRegistry

    expected = set()
    for path in sorted((source_root / "lintro/tools").glob("*/definition.py")):
        module = ".".join(path.relative_to(source_root).with_suffix("").parts)
        # Accept only names shaped like a built-in tool definition, so a stray
        # directory in the extracted source cannot widen what is documented.
        if not re.fullmatch(r"lintro\.tools\.[a-z][a-z0-9_]*\.definition", module):
            raise RuntimeError(f"Unexpected tool definition module: {module}")
        expected.add(module)
    # Load definitions through the product's own built-in discovery rather than
    # importing computed names here. It logs and skips a module that fails to
    # import, so confirm every expected definition actually loaded.
    discover_builtin_tools()
    missing = sorted(expected - sys.modules.keys())
    if missing:
        raise RuntimeError(f"Tool definition modules did not load: {missing}")
    records = {}
    for name, cls in ToolRegistry._tools.items():
        plugin = ToolRegistry.get(name)
        definition = plugin.definition
        method = cls.set_options
        docs = inspect.getdoc(method) or ""
        parameters = []
        for key, parameter in inspect.signature(method).parameters.items():
            if key == "self" or parameter.kind in (
                parameter.VAR_KEYWORD,
                parameter.VAR_POSITIONAL,
            ):
                continue
            match = re.search(
                r"^    " + re.escape(key) + r": (.*?)(?=\n    [\w*]|\n\S|\Z)",
                docs,
                re.M | re.S,
            )
            parameters.append(
                {
                    "name": key,
                    "description": (
                        TS_FILE_SELECTION_NOTE
                        if name in {"tsc", "vue-tsc"} and key == "use_project_files"
                        else (
                            " ".join(match[1].split())
                            if match
                            else "See the integration source for types and behavior."
                        )
                    ),
                    "default": definition.default_options.get(
                        key,
                        (
                            parameter.default
                            if parameter.default is not inspect.Parameter.empty
                            else None
                        ),
                    ),
                },
            )
        option_file = inspect.getsourcefile(method)
        if option_file is None:
            raise RuntimeError(f"No source for {name} option validation")
        records[name] = {
            "patterns": definition.file_patterns,
            "nativeConfigs": definition.native_configs,
            "minimumVersion": definition.min_version,
            "defaults": definition.default_options,
            "options": parameters,
            "optionLine": inspect.getsourcelines(method)[1],
            "optionSource": str(
                Path(option_file).relative_to(source_root),
            ),
        }
    return records


def main() -> None:
    """Load only the requested archive and print public help metadata."""
    source_root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(source_root))
    from lintro.ai.config import AIConfig
    from lintro.ai.provider_config import legacy_key_field_paths
    from lintro.ai.registry import provider_config_models
    from lintro.cli import cli
    from lintro.enums.review_category import ReviewCategory

    with contextlib.redirect_stdout(io.StringIO()):
        records = command_tree(cli, source_root)
    print(
        json.dumps(
            {
                "commands": records,
                "toolDetails": tool_details(source_root),
                "aiConfig": {
                    "schema": AIConfig.model_json_schema(),
                    "defaults": AIConfig().model_dump(mode="json"),
                    "providers": {
                        str(provider): model.model_json_schema()
                        for provider, model in provider_config_models().items()
                    },
                    "legacyAliases": legacy_key_field_paths(),
                    "reviewCategories": [str(category) for category in ReviewCategory],
                },
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
