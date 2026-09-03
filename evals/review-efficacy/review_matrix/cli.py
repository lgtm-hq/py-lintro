"""Command-line entry point for the review agreement matrix."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from review_matrix.report import build_report, render_markdown, report_to_dict
from review_matrix.runner import execute_matrix, plan_spend, render_spend_plan
from review_matrix.spec_loader import SpecError, load_corpus, load_matrix

__all__ = ["build_parser", "main"]

HARNESS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = HARNESS_ROOT / "matrix.yaml"
DEFAULT_CORPUS = HARNESS_ROOT / "corpus" / "corpus.yaml"
DEFAULT_RUNS_ROOT = HARNESS_ROOT / "runs"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="run_matrix",
        description=(
            "Run the cross-provider review agreement matrix. Without "
            "--confirm-spend this only prints the projected spend."
        ),
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help="Directory that run directories are created under.",
    )
    parser.add_argument(
        "--stamp",
        default=None,
        help=(
            "Name of this run's directory. Defaults to a UTC timestamp; pass "
            "an explicit value to make a run directory reproducible."
        ),
    )
    parser.add_argument(
        "--confirm-spend",
        action="store_true",
        help=(
            "Actually execute the matrix. Real inference spend: without this "
            "flag the command is a dry run that only prints the projection."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the matrix, or print its projected spend.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` on success (dry run included), ``2`` when the
        matrix or corpus file is malformed.
    """
    args = build_parser().parse_args(argv)
    try:
        spec = load_matrix(args.matrix)
        corpus = load_corpus(args.corpus)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    plan = plan_spend(spec=spec, corpus=corpus)
    print(render_spend_plan(plan=plan, confirmed=args.confirm_spend))
    if not args.confirm_spend:
        return 0

    stamp = args.stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.runs_root) / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = execute_matrix(spec=spec, corpus=corpus, output_dir=output_dir)
    report = build_report(spec=spec, corpus=corpus, runs=runs)

    json_path = output_dir / "report.json"
    json_path.write_text(
        json.dumps(report_to_dict(report=report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = output_dir / "report.md"
    markdown_path.write_text(render_markdown(report=report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0
