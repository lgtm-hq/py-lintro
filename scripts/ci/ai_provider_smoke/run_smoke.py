#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Weekly live API smoke for every funded provider (#2600).

The repo's only live provider test used to be the CLI invocation smoke, which
is session- and subscription-bound, drifts with every vendor release, and — as
a scheduled job nobody watches — failed silently every Monday since 2026-08-10
on an exhausted prepaid balance. This script replaces it with the thing that is
actually cheap to keep green: a base URL, a key, a model, one trivial prompt,
sent through *lintro's own* provider code path so a green run proves the code a
review will run on, not a hand-rolled HTTP call.

The provider list is data, not code: ``providers.json`` next to this file
declares one row per provider, and the workflow expands it into one job per
row. Adding a provider is a row plus a secret.

Two subcommands:

``--emit-matrix``
    Print (and, under Actions, publish) the GitHub matrix built from the table,
    including the ``host:443`` each row needs on the harden-runner allowlist.

``--provider NAME``
    Run the smoke for one row. The credential arrives in the environment named
    by ``--credential-env`` (default ``LINTRO_SMOKE_CREDENTIAL``), never on
    argv. It is read where it is used and is never stored on a row, held on a
    config object that is rendered, or interpolated into any message: the
    table's ``key_env`` is the *name* of that variable, never a value.
    Provider error text is dropped whole when it echoes the live credential and
    then run through lintro's ``redact_secrets`` for key-shaped literals, before
    anything is printed, summarised or written to the error file.

Outcomes are reported twice over: as an exit code (0 answered, 1 failed, with
the error text written to the job summary and to ``--error-file`` so the
failure notifier can quote it on the tracker issue) and as an ``outcome`` step
output of ``success`` / ``failure`` / ``skipped``. A row whose credential
variable is empty exits 0 but reports ``skipped`` with a workflow notice, and
the workflow turns that into a *pending* commit status — a call that was never
made must never show a green tick.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

#: The table that drives both the matrix and this script.
TABLE_PATH: Final[Path] = Path(__file__).with_name("providers.json")

#: Wire protocols lintro implements. A gateway (kimi, z.ai) is one of these
#: plus a base URL, which is exactly why the table has no gateway column.
SUPPORTED_PROTOCOLS: Final[frozenset[str]] = frozenset({"anthropic", "openai"})

#: Row names double as job names and as the ``ai-provider-smoke/<name>`` commit
#: status context, so they are constrained to what both accept.
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Credential variable names are uppercase environment identifiers.
_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: The smoke prompt. Trivially cheap, and the answer is checkable — a provider
#: that returns an empty envelope must not be counted as a pass.
SMOKE_PROMPT: Final[str] = "Reply with the single word: pong"

#: Cap on the smoke response: enough for a word, small enough that a runaway
#: generation cannot turn a smoke test into a bill.
SMOKE_MAX_TOKENS: Final[int] = 16

#: Per-call timeout. A provider that cannot answer a one-word prompt inside a
#: minute is a failure worth seeing, not a job worth waiting on.
SMOKE_TIMEOUT: Final[float] = 60.0


@dataclass(frozen=True)
class ProviderRow:
    """One row of the provider smoke table.

    Attributes:
        name: Job name and commit-status suffix, e.g. ``anthropic-api``.
        protocol: Wire protocol, one of :data:`SUPPORTED_PROTOCOLS`.
        base_url: API base URL the call is pointed at.
        key_env: NAME of the environment variable (fed by the repository
            secret of the same name) the credential arrives in. Never a
            credential value — nothing on this row is sensitive.
        model: Model slug sent with the smoke prompt.
    """

    name: str
    protocol: str
    base_url: str
    key_env: str
    model: str

    @property
    def egress(self) -> str:
        """Return the ``host:443`` this row needs on the egress allowlist.

        Returns:
            The base URL's host with the HTTPS port.
        """
        return f"{urlparse(self.base_url).hostname}:443"

    def as_matrix_entry(self) -> dict[str, str]:
        """Return the row as a GitHub Actions matrix include entry.

        Returns:
            A mapping of matrix keys to values, egress included.
        """
        return {
            "name": self.name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "key_env": self.key_env,
            "model": self.model,
            "egress": self.egress,
        }


def _fail_table(message: str) -> None:
    """Raise a table validation error.

    Args:
        message: What is wrong with the table.

    Raises:
        ValueError: Always; the caller validates and this reports.
    """
    raise ValueError(f"{TABLE_PATH.name}: {message}")


def _validate_row(*, entry: Any, index: int) -> ProviderRow:
    """Validate one raw table entry.

    Args:
        entry: The decoded JSON entry.
        index: Position in the table, used in error messages.

    Returns:
        The validated row.
    """
    if not isinstance(entry, dict):
        _fail_table(f"row {index} is not an object")
    missing = {"name", "protocol", "base_url", "key_env", "model"} - set(entry)
    if missing:
        _fail_table(f"row {index} is missing {sorted(missing)}")
    unknown = set(entry) - {"name", "protocol", "base_url", "key_env", "model"}
    if unknown:
        _fail_table(f"row {index} has unknown keys {sorted(unknown)}")
    row = ProviderRow(
        name=str(entry["name"]),
        protocol=str(entry["protocol"]),
        base_url=str(entry["base_url"]),
        key_env=str(entry["key_env"]),
        model=str(entry["model"]),
    )
    if not _NAME_RE.match(row.name):
        _fail_table(f"row {index} name {row.name!r} is not a valid job name")
    if row.protocol not in SUPPORTED_PROTOCOLS:
        _fail_table(
            f"row {row.name} protocol {row.protocol!r} is not one of "
            f"{sorted(SUPPORTED_PROTOCOLS)}",
        )
    parsed = urlparse(row.base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        _fail_table(f"row {row.name} base_url {row.base_url!r} is not an https URL")
    if not _ENV_NAME_RE.match(row.key_env):
        _fail_table(
            f"row {row.name} key_env {row.key_env!r} is not an env variable name",
        )
    if not row.model.strip():
        _fail_table(f"row {row.name} has an empty model")
    return row


def load_table(*, path: Path = TABLE_PATH) -> list[ProviderRow]:
    """Load and validate the provider smoke table.

    Args:
        path: Table location; defaults to the committed table.

    Returns:
        The validated rows, in table order.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "providers" not in data:
        _fail_table("top level must be an object with a 'providers' key")
    raw_rows = data["providers"]
    if not isinstance(raw_rows, list) or not raw_rows:
        _fail_table("'providers' must be a non-empty list")
    rows = [_validate_row(entry=entry, index=i) for i, entry in enumerate(raw_rows)]
    names = [row.name for row in rows]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        _fail_table(f"duplicate row names {duplicates}")
    return rows


def build_matrix(*, rows: list[ProviderRow]) -> dict[str, list[dict[str, str]]]:
    """Build the GitHub Actions matrix for the table.

    Args:
        rows: The validated table rows.

    Returns:
        A matrix mapping with a single ``include`` list.
    """
    return {"include": [row.as_matrix_entry() for row in rows]}


def _write_github_output(*, name: str, value: str) -> None:
    """Append a value to ``$GITHUB_OUTPUT`` when running under Actions.

    Args:
        name: Output name.
        value: Single-line output value.
    """
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _write_summary(text: str) -> None:
    """Append a block to the job summary when running under Actions.

    Args:
        text: Markdown to append.
    """
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with Path(summary).open("a", encoding="utf-8") as handle:
        handle.write(f"{text}\n")


def row_for(*, name: str, rows: list[ProviderRow]) -> ProviderRow:
    """Return the row with the given name.

    Args:
        name: Row name from the table.
        rows: The validated table rows.

    Returns:
        The matching row.

    Raises:
        SystemExit: When no row carries that name.
    """
    for row in rows:
        if row.name == name:
            return row
    known = ", ".join(sorted(candidate.name for candidate in rows))
    print(f"::error::unknown provider row {name!r}; table has: {known}")
    raise SystemExit(2)


async def _complete(*, row: ProviderRow, credential_env: str) -> str:
    """Send the smoke prompt through lintro's own provider code path.

    Args:
        row: The provider row under test.
        credential_env: Environment variable holding the credential.

    Returns:
        The provider's response content.
    """
    from lintro.ai.config import AIConfig
    from lintro.ai.enums import AITransport
    from lintro.ai.provider_enum import AIProvider
    from lintro.ai.providers import get_provider

    config = AIConfig(
        enabled=True,
        provider=AIProvider(row.protocol),
        transport=AITransport.API,
        model=row.model,
        api_key_env=credential_env,
        api_base_url=row.base_url,
        max_tokens=SMOKE_MAX_TOKENS,
        transcript_logging=False,
    )
    provider = get_provider(config)
    try:
        response = await provider.complete(
            SMOKE_PROMPT,
            max_tokens=SMOKE_MAX_TOKENS,
            timeout=SMOKE_TIMEOUT,
        )
    finally:
        await provider.aclose()
    return response.content


def _safe_detail(text: str, *, env_name: str) -> str:
    """Return provider error text with no credential left in it.

    A gateway that 401s likes to quote what it was sent, so the provider's own
    error is the one string in this script that can carry the live credential —
    and it is written to three places that outlive the job (the log, the step
    summary, the error file the tracker issue quotes). Two defences, in order:

    1. If the text contains the credential at all, the whole text is dropped.
       Nothing derived from the credential is kept, not even a masked remnant.
    2. Whatever survives still goes through lintro's own ``redact_secrets``,
       which replaces key-shaped literals (``sk-...``, ``ghp_...``, bearer
       tokens) that some *other* account's key could hide in.

    Args:
        text: The provider's error text.
        env_name: Name of the environment variable holding the credential.

    Returns:
        Text that is safe to print, summarise and write to a file.
    """
    from lintro.ai.secrets import redact_secrets

    if _credential_appears_in(text, env_name=env_name):
        return (
            "RedactedProviderError: the provider error echoed the credential, "
            "so it was dropped in full. See the job log's step for the "
            "provider's HTTP status."
        )
    return redact_secrets(text)


def _credential_appears_in(text: str, *, env_name: str) -> bool:
    """Return whether the live credential occurs in the given text.

    The credential is read here and compared, never returned, stored or
    interpolated: the only thing that leaves this function is a boolean.

    Args:
        text: The text to inspect.
        env_name: Name of the environment variable holding the credential.

    Returns:
        True when the environment variable's value occurs in the text.
    """
    value = os.environ.get(env_name, "").strip()
    return bool(value) and value in text


def run_smoke(*, row: ProviderRow, credential_env: str, error_file: Path | None) -> int:
    """Run the smoke for one row and report it the way CI reads it.

    Args:
        row: The provider row under test.
        credential_env: Environment variable holding the credential.
        error_file: Where to write the failure text for the issue filer.

    Returns:
        The process exit code.
    """
    if not os.environ.get(credential_env, "").strip():
        # A missing credential is not a pass and not a failure of the provider.
        # It is announced, and the workflow turns it into a pending commit
        # status so the checks tab never shows a green tick for a call that was
        # never made.
        notice = (
            f"{row.name}: no credential in {row.key_env} — "
            "smoke skipped, no call was made"
        )
        print(f"::notice title=Provider smoke skipped::{notice}")
        _write_summary(f"### `{row.name}` skipped\n\n{notice}\n")
        _write_github_output(name="outcome", value="skipped")
        return 0

    content = ""
    detail: str | None = None
    try:
        content = asyncio.run(_complete(row=row, credential_env=credential_env))
    except Exception as exc:  # every failure is news here, none is fatal
        detail = _safe_detail(f"{type(exc).__name__}: {exc}", env_name=credential_env)
    if detail is None and not content.strip():
        # An empty envelope is a failure of the provider, not a pass: the
        # whole point of a prompt with a checkable answer.
        detail = "EmptyResponse: provider returned an empty response body"

    if detail is not None:
        print(f"::error title={row.name} smoke failed::{detail}")
        _write_summary(
            f"### `{row.name}` failed\n\n"
            f"- **Model:** `{row.model}`\n"
            f"- **Base URL:** `{row.base_url}`\n\n"
            f"```text\n{detail}\n```\n",
        )
        if error_file is not None:
            error_file.write_text(
                f"### `{row.name}`\n\n"
                f"- **Model:** `{row.model}`\n"
                f"- **Base URL:** `{row.base_url}`\n\n"
                f"```text\n{detail}\n```\n",
                encoding="utf-8",
            )
        _write_github_output(name="outcome", value="failure")
        return 1

    _write_summary(
        f"### `{row.name}` ok\n\nResponded with {len(content)} characters.\n",
    )
    _write_github_output(name="outcome", value="success")
    print(f"{row.name}: ok ({len(content)} characters)")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit-matrix",
        action="store_true",
        help="print the Actions matrix built from the provider table",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="name of the table row to smoke",
    )
    parser.add_argument(
        "--credential-env",
        default="LINTRO_SMOKE_CREDENTIAL",
        help="environment variable holding the credential",
    )
    parser.add_argument(
        "--error-file",
        default=None,
        help="file to write the failure text to, for the issue filer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = _build_parser().parse_args(argv)
    rows = load_table()

    if args.emit_matrix:
        matrix = json.dumps(build_matrix(rows=rows), separators=(",", ":"))
        _write_github_output(name="matrix", value=matrix)
        print(matrix)
        return 0

    if not args.provider:
        print("::error::either --emit-matrix or --provider is required")
        return 2

    return run_smoke(
        row=row_for(name=args.provider, rows=rows),
        credential_env=args.credential_env,
        error_file=Path(args.error_file) if args.error_file else None,
    )


if __name__ == "__main__":
    sys.exit(main())
