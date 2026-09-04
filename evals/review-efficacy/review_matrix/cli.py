"""Command-line entry point for the review agreement matrix."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from review_matrix.invoker import ReviewInvoker
from review_matrix.models.matrix import MatrixSpec
from review_matrix.report import build_report, render_markdown, report_to_dict
from review_matrix.runner import (
    RUNS_JSONL_NAME,
    execute_matrix,
    plan_spend,
    render_spend_plan,
)
from review_matrix.spec_loader import SpecError, load_corpus, load_matrix

__all__ = ["build_parser", "main", "prepare_output_dir", "validate_stamp"]

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
        "--overwrite",
        action="store_true",
        help=(
            "Reuse an existing run directory, clearing the reports and any "
            "run payloads a previous execution left in it."
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


def validate_stamp(stamp: str) -> bool:
    """Return whether a stamp is usable as a single run-directory name.

    The stamp is joined onto ``--runs-root``, so anything but a plain
    directory name could redirect the whole run outside the runs root.

    Args:
        stamp: Candidate directory name.

    Returns:
        ``True`` when the stamp is a single, non-traversing directory name.
    """
    if not stamp or stamp in {".", ".."}:
        return False
    candidate = Path(stamp)
    return not candidate.is_absolute() and candidate.name == stamp


def prepare_output_dir(
    *,
    output_dir: Path,
    spec: MatrixSpec,
    overwrite: bool,
) -> bool:
    """Create the run directory, refusing to reuse a populated one.

    A second run into an existing directory would leave the reports describing
    the new run beside payloads from the old one, so reuse is opt-in. With
    ``overwrite`` the stale reports, the run journal, and this matrix's own
    run payloads are removed; nothing outside ``output_dir`` is touched, and
    unrelated files inside it are left alone.

    Args:
        output_dir: Directory this run will write to.
        spec: Matrix specification, naming the config directories to clear.
        overwrite: Whether reuse was explicitly requested.

    Returns:
        ``True`` when the directory is ready; ``False`` when it already exists
        and ``overwrite`` was not passed.
    """
    if output_dir.exists() and not overwrite:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        return True
    for name in ("report.json", "report.md", RUNS_JSONL_NAME):
        (output_dir / name).unlink(missing_ok=True)
    for config in spec.configs:
        config_dir = output_dir / config.config_id
        if not config_dir.is_dir():
            continue
        for stale in sorted(config_dir.glob("*/run-*.json")):
            stale.unlink(missing_ok=True)
        for stale in sorted(config_dir.glob("*/run-*.stderr.txt")):
            stale.unlink(missing_ok=True)
    return True


def main(
    argv: list[str] | None = None,
    *,
    invoker: ReviewInvoker | None = None,
) -> int:
    """Run the matrix, or print its projected spend.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.
        invoker: Invocation callable handed to the runner; defaults to the
            real CLI subprocess. Tests inject a fake so no invocation reaches
            a provider.

    Returns:
        Process exit code: ``0`` on success (dry run included), ``1`` when the
        matrix executed but produced no comparable run, ``2`` when the matrix
        or corpus file is missing or malformed, the stamp is not a plain
        directory name, or the run directory would be reused without
        ``--overwrite``.
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

    # Defaulted only when the flag was omitted: ``--stamp ""`` is an
    # authoring error, not a request for a timestamp.
    stamp = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if args.stamp is None
        else args.stamp
    )
    if not validate_stamp(stamp):
        print(
            f"error: --stamp must be a single directory name (got {stamp!r})",
            file=sys.stderr,
        )
        return 2
    output_dir = Path(args.runs_root) / stamp
    if not prepare_output_dir(
        output_dir=output_dir,
        spec=spec,
        overwrite=args.overwrite,
    ):
        print(
            f"error: run directory {output_dir} already exists; pass "
            "--overwrite to reuse it or --stamp to name a new one",
            file=sys.stderr,
        )
        return 2
    runs = execute_matrix(
        spec=spec,
        corpus=corpus,
        output_dir=output_dir,
        invoker=invoker,
    )
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
    if not any(run.is_comparable for run in runs):
        print(
            f"error: none of the {len(runs)} runs produced comparable "
            "findings; the report has no metrics",
            file=sys.stderr,
        )
        return 1
    return 0
