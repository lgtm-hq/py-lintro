"""Declared CLI contract for the Cursor ``agent`` binary.

Lives in the provider package so a vendor's CLI identity (binary name, install
hint, auth surface) and the flag contract it is checked against are declared
side by side; :mod:`lintro.ai.providers.cli_contracts` holds only the shared
vocabulary and resolves contracts through the plugin registry (#2308).

See that module's docstring for what ``required_flags``, ``optional_flags`` and
``version_floor`` each mean.
"""

from __future__ import annotations

from lintro.ai.providers.cli_contracts import CliContract, OptionalCliFlag

__all__ = ["CURSOR_CLI_CONTRACT"]

#: What lintro sends to, and requires of, the Cursor ``agent`` binary.
CURSOR_CLI_CONTRACT = CliContract(
    binary="agent",
    display_name="Cursor agent",
    upgrade_hint=(
        "Upgrade the Cursor agent CLI: curl https://cursor.com/install -fsS | bash"
    ),
    # The agent CLI uses calendar versioning; every release carrying the
    # --print/--output-format surface lintro drives is 2025 or later.
    version_floor=(2025, 1, 1),
    required_flags=(
        "--print",
        "--output-format",
        "--mode",
        "--model",
        "--workspace",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--trust",
            purpose="grants the agent workspace trust",
        ),
        OptionalCliFlag(
            flag="--resume",
            purpose="reuses one CLI session across review turns",
        ),
    ),
)
