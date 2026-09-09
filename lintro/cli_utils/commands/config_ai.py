"""The AI section of the ``lintro config`` report (#2309).

Split out of :mod:`lintro.cli_utils.commands.config` because it is the only
part of that report that reaches into :mod:`lintro.ai`, and because it renders
one provider's nested block rather than a flat table like every other section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table

if TYPE_CHECKING:
    from rich.console import Console

    from lintro.config import LintroConfig

__all__ = ["ai_config_json", "print_ai_config"]


def _describe_failure(exc: Exception) -> str:
    """Render a resolution failure as one line for the report row.

    A pydantic error's first line is a count ("1 validation error for
    AIConfig"), so its own message is used instead — that is the text the
    nested-block validator wrote, naming the ``ai.providers.<name>.<field>``
    path the user typed.

    Args:
        exc: The resolution failure to describe.

    Returns:
        A single line naming what is wrong.
    """
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            message = str(errors[0].get("msg", "")).strip()
            return message.removeprefix("Value error, ") or type(exc).__name__
    for line in str(exc).splitlines():
        if line.strip():
            return line.strip()
    return type(exc).__name__


def print_ai_config(
    *,
    console: Console,
    config: LintroConfig,
) -> None:
    """Print the effective AI settings, including the active provider block.

    Only the selected provider's ``ai.providers.<name>`` block is rendered;
    the rest are summarized as a count (#2309). Showing every vendor's block
    was the flat model's failure mode — a reader had to know which keys their
    provider actually reads.

    Resolution runs with diagnostics off: this is a display of values the
    execution path already reported on, and it must not repeat its warnings.

    Args:
        console: Rich console to print to.
        config: Loaded Lintro configuration.
    """
    from pydantic import ValidationError
    from rich.markup import escape

    from lintro.ai.effective_config import resolve_effective_ai_config
    from lintro.ai.exceptions import AIConfigOverrideError
    from lintro.ai.provider_blocks import nested_source_key
    from lintro.ai.resolved_ai_config import format_sourced_value

    try:
        resolved = resolve_effective_ai_config(config.ai, diagnostics=False)
    except (AIConfigOverrideError, ValidationError) as exc:
        # A bad ``ai:`` block degrades this section to one line rather than
        # killing the report: ``lintro config`` is the command a user runs to
        # diagnose a bad config, so it must still print the rest of it.
        # The failure text quotes the value the user wrote, so a value such
        # as ``[/]`` would otherwise make this line invalid Rich markup and
        # crash the report it exists to keep printable.
        console.print(
            f"[bold]AI Settings[/bold]  [red]{escape(_describe_failure(exc))}[/red]",
        )
        console.print()
        return

    ai_config = resolved.config
    table = Table(title="AI Settings", show_header=False, box=None)
    # Wider than the other sections: a nested key is
    # ``providers.<provider>.<field>`` and must not be elided to an ellipsis.
    table.add_column("Setting", style="cyan", width=40)
    table.add_column("Value", style="yellow")

    provider = ai_config.provider
    provider_text = provider.value if provider is not None else "[dim]unset[/dim]"
    table.add_row(
        "provider",
        format_sourced_value(provider_text, resolved.sources.get("provider")),
    )
    transport = ai_config.transport
    table.add_row(
        "transport",
        format_sourced_value(
            transport.value if transport is not None else "[dim]unset[/dim]",
            resolved.sources.get("transport"),
        ),
    )
    table.add_row(
        "model",
        format_sourced_value(
            (
                escape(ai_config.model)
                if ai_config.model
                else "[dim]provider default[/dim]"
            ),
            resolved.sources.get("model"),
        ),
    )

    if provider is not None:
        settings = ai_config.provider_settings(provider)
        fields = type(settings).model_fields
        if fields:
            for name in sorted(fields):
                value = getattr(settings, name)
                rendered = escape(str(getattr(value, "value", value)))
                key = nested_source_key(provider=provider, field=name)
                table.add_row(
                    key,
                    format_sourced_value(rendered, resolved.sources.get(key)),
                )
        else:
            table.add_row(
                f"providers.{provider.value}",
                "[dim]no provider-specific settings[/dim]",
            )

    console.print(table)
    others = ai_config.other_provider_block_count()
    if others:
        plural = "s" if others != 1 else ""
        console.print(
            f"[dim]  {others} other provider block{plural} configured; "
            f"not shown because ai.provider selects "
            f"{provider.value if provider is not None else 'none'}[/dim]",
        )
    console.print()


def ai_config_json(config: LintroConfig) -> dict[str, Any]:
    """Return the AI section of ``lintro config --json``.

    The same shape the rich section renders, so the two outputs cannot drift:
    the shared settings with provenance, only the selected provider's block,
    and a count of the others.

    Args:
        config: Loaded Lintro configuration.

    Returns:
        A JSON-serializable mapping. A configuration that fails to resolve
        yields ``{"error": ...}`` rather than raising, so ``--json`` degrades
        the same way the rich report does.
    """
    from pydantic import ValidationError

    from lintro.ai.effective_config import resolve_effective_ai_config
    from lintro.ai.exceptions import AIConfigOverrideError
    from lintro.ai.provider_blocks import nested_source_key

    try:
        resolved = resolve_effective_ai_config(config.ai, diagnostics=False)
    except (AIConfigOverrideError, ValidationError) as exc:
        return {"error": _describe_failure(exc)}

    ai_config = resolved.config
    provider = ai_config.provider
    output: dict[str, Any] = {
        "provider": provider.value if provider is not None else None,
        "transport": (
            ai_config.transport.value if ai_config.transport is not None else None
        ),
        "model": ai_config.model,
        "sources": {
            field: str(resolved.sources[field])
            for field in ("provider", "transport", "model")
            if field in resolved.sources
        },
        "provider_settings": {},
        "other_provider_blocks": ai_config.other_provider_block_count(),
    }
    if provider is None:
        return output

    settings = ai_config.provider_settings(provider)
    block: dict[str, Any] = {}
    for name in sorted(type(settings).model_fields):
        value = getattr(settings, name)
        key = nested_source_key(provider=provider, field=name)
        block[name] = {
            "value": getattr(value, "value", value),
            "source": str(resolved.sources[key]) if key in resolved.sources else None,
        }
    output["provider_settings"] = block
    return output
