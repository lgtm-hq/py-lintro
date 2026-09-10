"""Declared CLI contract for the Anthropic ``claude`` binary.

Lives in the provider package so a vendor's CLI identity (binary name, install
hint, auth surface) and the flag contract it is checked against are declared
side by side; :mod:`lintro.ai.providers.cli_contracts` holds only the shared
vocabulary and resolves contracts through the plugin registry (#2308).

See that module's docstring for what ``required_flags``, ``optional_flags`` and
``version_floor`` each mean.
"""

from __future__ import annotations

from lintro.ai.providers.cli_contracts import CliContract, OptionalCliFlag

__all__ = ["ANTHROPIC_CLI_CONTRACT"]

#: What lintro sends to, and requires of, the ``claude`` binary.
ANTHROPIC_CLI_CONTRACT = CliContract(
    binary="claude",
    display_name="Claude",
    upgrade_hint=(
        "Upgrade Claude Code: npm install -g @anthropic-ai/claude-code@latest"
    ),
    # Claude Code 2.x introduced the --bare / --json-schema surface lintro
    # drives; 1.x cannot serve a structured CLI review at all.
    version_floor=(2, 0, 0),
    # `--bare` is sent conditionally (see lintro.ai.providers.claude_auth): it
    # disables OAuth session login, so it is only safe when the binary can
    # reach an API key. It stays *required* rather than optional because the
    # API-key path cannot degrade without it -- silently dropping it there
    # would hand the agentic tool surface a review prompt -- so its
    # disappearance from the flag surface must break CI, not a user's review.
    required_flags=(
        "--bare",
        "--print",
        "--output-format",
        "--permission-mode",
        "--model",
        "--append-system-prompt",
        "--json-schema",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--json-schema-name",
            purpose="names the structured-output schema",
        ),
        OptionalCliFlag(
            flag="--resume",
            purpose="reuses one CLI session across review turns",
        ),
    ),
)
