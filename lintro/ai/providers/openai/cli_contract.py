"""Declared CLI contract for the OpenAI ``codex`` binary.

Lives in the provider package so a vendor's CLI identity (binary name, install
hint, auth surface) and the flag contract it is checked against are declared
side by side; :mod:`lintro.ai.providers.cli_contracts` holds only the shared
vocabulary and resolves contracts through the plugin registry (#2308).

See that module's docstring for what ``required_flags``, ``optional_flags`` and
``version_floor`` each mean.
"""

from __future__ import annotations

from lintro.ai.providers.cli_contracts import CliContract, OptionalCliFlag

__all__ = ["OPENAI_CLI_CONTRACT"]

#: What lintro sends to, and requires of, the ``codex`` binary.
OPENAI_CLI_CONTRACT = CliContract(
    binary="codex",
    display_name="Codex",
    upgrade_hint="Upgrade Codex CLI: npm install -g @openai/codex@latest",
    # `codex exec --json` with structured output stabilised during 0.20.x.
    version_floor=(0, 20, 0),
    help_args=("exec", "--help"),
    required_flags=(
        "--json",
        "--sandbox",
        "--model",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--output-schema",
            purpose="requests native structured output",
        ),
    ),
)
