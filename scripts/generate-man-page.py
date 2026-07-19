#!/usr/bin/env python3
"""Generate the lintro(1) man page from the Click CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

import click
from click_man.core import generate_man_page

from lintro import __version__
from lintro.cli import cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _REPO_ROOT / "lintro.1"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate lintro.1 from the Click command tree.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Output path for the generated man page.",
    )
    parser.add_argument(
        "--date",
        help="Optional date string for the man page header.",
    )
    return parser.parse_args()


def render_man_page(date: str | None = None) -> str:
    """Render the lintro man page text.

    Args:
        date: Optional date string for the man page header.

    Returns:
        The rendered man page text.
    """
    ctx = click.Context(cli, info_name="lintro")
    return str(generate_man_page(ctx, version=__version__, date=date))


def main() -> None:
    """Generate the man page file."""
    args = parse_args()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_man_page(date=args.date), encoding="utf-8")
    print(f"Generated {output}")


if __name__ == "__main__":
    main()
