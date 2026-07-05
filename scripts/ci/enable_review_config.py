#!/usr/bin/env python3
"""Enable AI review in ``.lintro-config.yaml`` for a single CI invocation.

``lintro review`` reads its configuration from ``.lintro-config.yaml`` and
exposes no CLI flag or environment override for ``ai.enabled`` or
``ai.max_cost_usd``. The dogfood workflow therefore patches the checked-out
(ephemeral) config in place before invoking the review command: it turns AI on,
pins the API transport and the Anthropic provider, and bounds spend with
``ai.max_cost_usd``.

Only ``ai.enabled``, ``ai.transport``, ``ai.provider``, and ``ai.max_cost_usd``
are touched; every other configured value is preserved. This script never edits
a committed file in normal use — it runs against the throwaway CI checkout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILENAME = ".lintro-config.yaml"
DEFAULT_MAX_COST_USD = 0.50
MAX_COST_ENV_VAR = "AI_REVIEW_MAX_COST_USD"


def resolve_max_cost_usd(*, raw_value: str | None) -> float:
    """Resolve the review cost cap from a raw string value.

    Args:
        raw_value: Raw value from the environment, or ``None`` when unset.

    Returns:
        The resolved cost cap in USD.

    Raises:
        ValueError: When ``raw_value`` is set but not a non-negative float.
    """
    if raw_value is None or raw_value.strip() == "":
        return DEFAULT_MAX_COST_USD
    parsed = float(raw_value)
    if parsed < 0:
        msg = f"{MAX_COST_ENV_VAR} must be non-negative, got {parsed}"
        raise ValueError(msg)
    return parsed


def patch_config(*, data: dict[str, Any], max_cost_usd: float) -> dict[str, Any]:
    """Return config data with AI review enabled and cost-bounded.

    Args:
        data: Parsed ``.lintro-config.yaml`` contents.
        max_cost_usd: Maximum total spend in USD for the review session.

    Returns:
        The mutated configuration mapping (mutated in place and returned).
    """
    ai_section = data.get("ai")
    if not isinstance(ai_section, dict):
        ai_section = {}
        data["ai"] = ai_section

    ai_section["enabled"] = True
    ai_section["transport"] = "api"
    ai_section["provider"] = "anthropic"
    ai_section["max_cost_usd"] = max_cost_usd
    return data


def _load_config(*, config_path: Path) -> dict[str, Any]:
    """Load a YAML config file into a mapping.

    Args:
        config_path: Path to the ``.lintro-config.yaml`` file.

    Returns:
        Parsed configuration mapping (empty when the file has no content).
    """
    with config_path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def _write_config(*, config_path: Path, data: dict[str, Any]) -> None:
    """Write a config mapping back to disk as YAML.

    Args:
        config_path: Destination path for the config file.
        data: Configuration mapping to serialize.
    """
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)


def main(*, argv: list[str] | None = None) -> int:
    """Patch the config file and report the effective settings.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILENAME,
        help="Path to the .lintro-config.yaml file to patch.",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    max_cost_usd = resolve_max_cost_usd(raw_value=os.environ.get(MAX_COST_ENV_VAR))
    data = _load_config(config_path=config_path)
    patch_config(data=data, max_cost_usd=max_cost_usd)
    _write_config(config_path=config_path, data=data)

    print(
        "Enabled AI review: ai.enabled=true, ai.transport=api, "
        f"ai.provider=anthropic, ai.max_cost_usd={max_cost_usd}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
