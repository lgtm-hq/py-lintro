"""Matrix execution: the spend gate, the run loop, and run persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import ReviewFinding
from review_matrix.enums.run_status import RunStatus
from review_matrix.findings import findings_from_payload, verdict_for
from review_matrix.invoker import InvocationResult, ReviewInvoker, run_review_cli
from review_matrix.models.corpus import Corpus, CorpusItem
from review_matrix.models.matrix import MatrixConfig, MatrixSpec
from review_matrix.models.run import EvalRun
from review_matrix.spec_loader import SAFE_ID_PATTERN

__all__ = [
    "RUNS_JSONL_NAME",
    "count_unknown_costs",
    "ConfigSpend",
    "SpendPlan",
    "execute_matrix",
    "plan_spend",
    "render_spend_plan",
    "run_to_dict",
    "summarize_runs",
]


#: Append-only journal of run records, written as each cell completes so an
#: aborted matrix still has every result it already paid for.
RUNS_JSONL_NAME = "runs.jsonl"


@dataclass(frozen=True, slots=True)
class ConfigSpend:
    """Projected spend for one matrix config.

    Attributes:
        config_id: Config the projection is for.
        runs: Number of invocations the config will make.
        projected_usd: ``runs * projected_cost_usd``.
        ceiling_usd: ``runs * max_cost_usd`` — the most the config can spend
            if every run hits its cap.
    """

    config_id: str
    runs: int
    projected_usd: float
    ceiling_usd: float


@dataclass(frozen=True, slots=True)
class SpendPlan:
    """What a full matrix run is expected to cost before anything executes.

    Attributes:
        total_runs: Invocations across the whole matrix.
        projected_usd: Sum of the per-config projections.
        ceiling_usd: Sum of the per-config ceilings.
        per_config: Per-config breakdown, in matrix order.
    """

    total_runs: int
    projected_usd: float
    ceiling_usd: float
    per_config: tuple[ConfigSpend, ...] = field(default_factory=tuple)


def plan_spend(*, spec: MatrixSpec, corpus: Corpus) -> SpendPlan:
    """Project the cost of a whole matrix run.

    Args:
        spec: Matrix specification.
        corpus: Corpus the matrix will be run over.

    Returns:
        The projected spend, per config and in total.
    """
    runs_per_config = spec.repeats * len(corpus.items)
    per_config = tuple(
        ConfigSpend(
            config_id=config.config_id,
            runs=runs_per_config,
            projected_usd=runs_per_config * config.projected_cost_usd,
            ceiling_usd=runs_per_config * config.max_cost_usd,
        )
        for config in spec.configs
    )
    return SpendPlan(
        total_runs=runs_per_config * len(spec.configs),
        projected_usd=sum(entry.projected_usd for entry in per_config),
        ceiling_usd=sum(entry.ceiling_usd for entry in per_config),
        per_config=per_config,
    )


def render_spend_plan(*, plan: SpendPlan, confirmed: bool) -> str:
    """Render the spend plan for the terminal.

    Args:
        plan: Projection to render.
        confirmed: Whether the caller passed the spend-confirmation flag.

    Returns:
        A multi-line block ending in either the go-ahead or the dry-run notice
        naming the flag that would execute the matrix.
    """
    lines = [
        "Projected spend for this matrix run:",
        "",
        f"  {'config':<28} {'runs':>5} {'projected':>12} {'ceiling':>12}",
    ]
    for entry in plan.per_config:
        lines.append(
            f"  {entry.config_id:<28} {entry.runs:>5} "
            f"{'$' + format(entry.projected_usd, '.2f'):>12} "
            f"{'$' + format(entry.ceiling_usd, '.2f'):>12}",
        )
    lines.extend(
        [
            "",
            f"  {'TOTAL':<28} {plan.total_runs:>5} "
            f"{'$' + format(plan.projected_usd, '.2f'):>12} "
            f"{'$' + format(plan.ceiling_usd, '.2f'):>12}",
            "",
        ],
    )
    if confirmed:
        lines.append("--confirm-spend given; executing the matrix.")
    else:
        lines.append(
            "Dry run: nothing was executed. Re-run with --confirm-spend to "
            "spend the amount above.",
        )
    return "\n".join(lines)


def _decode_payload(text: str) -> Mapping[str, Any] | None:
    """Decode a review payload from captured stdout.

    Args:
        text: Captured standard output.

    Returns:
        The decoded mapping, or ``None`` when stdout is not a review payload.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _error_kind_from_payload(payload: Mapping[str, Any]) -> str | None:
    """Return the machine-readable ``kind`` of a payload's error envelope.

    Kept beside the prose diagnostic so ``runs.jsonl`` can be filtered by
    failure class (auth, quota, provider unavailable) without parsing the
    message.

    Args:
        payload: Decoded review payload.

    Returns:
        The envelope's ``kind``, or ``None`` when the payload carries no
        error object.
    """
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    kind = str(error.get("kind") or "").strip()
    return kind or None


def _error_from_payload(payload: Mapping[str, Any]) -> str | None:
    """Return the failure reason a payload carries, if it is not a review.

    Args:
        payload: Decoded review payload.

    Returns:
        ``None`` for a payload with a ``findings`` list; otherwise the error
        envelope's ``kind`` and ``message`` (see
        :mod:`lintro.ai.review.error_contract`), or a note that no findings
        block was present.
    """
    if isinstance(payload.get("findings"), list):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        kind = str(error.get("kind") or "unknown")
        message = str(error.get("message") or "").strip()
        return f"{kind}: {message}"[:500] if message else kind
    return "payload carried no findings list"


def _cost_from_payload(payload: Mapping[str, Any]) -> float | None:
    """Read the run's cost estimate from a review payload.

    Args:
        payload: Decoded review payload.

    Returns:
        ``metadata.cost_estimate_usd``, or ``None`` when it is absent or
        unreadable. ``None`` rather than ``0.0``: an unknown cost summed as
        zero would silently understate what the matrix spent, which is the
        one number an operator checks against their bill.
    """
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("cost_estimate_usd")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _incomplete_reason(payload: Mapping[str, Any]) -> str | None:
    """Return why a review payload does not describe a complete review.

    ``lintro review`` reports incompleteness on three separate axes (see
    :func:`lintro.ai.review.output.review_result_to_dict` and
    :class:`lintro.ai.review.models.review_metadata.ReviewMetadata`): the
    ``partial`` flag for a run that stopped before every chunk was reviewed,
    ``findings_coverage_complete`` for a run whose findings were capped or
    retried, and an ``incomplete`` readiness verdict for coverage below 100%
    of the review-eligible files.

    Args:
        payload: Decoded review payload.

    Returns:
        A human-readable reason, naming ``stopped_reason`` where the payload
        carries one, or ``None`` when the review is complete.
    """
    metadata = payload.get("metadata")
    metadata_map: Mapping[str, Any] = metadata if isinstance(metadata, dict) else {}
    stopped = str(
        payload.get("stopped_reason") or metadata_map.get("stopped_reason") or "",
    ).strip()
    partial = bool(payload.get("partial") or metadata_map.get("partial"))
    if partial:
        return f"review was partial: {stopped}" if stopped else "review was partial"
    coverage_complete = payload.get("findings_coverage_complete")
    if coverage_complete is None:
        coverage_complete = metadata_map.get("findings_coverage_complete")
    if coverage_complete is False:
        return "findings coverage was incomplete"
    verdict = str(payload.get("readiness_verdict", "")).strip().lower()
    if verdict == ReviewVerdict.INCOMPLETE.value:
        return "readiness verdict was incomplete"
    return None


def _has_usable_findings(
    payload: Mapping[str, Any],
    *,
    parsed: Sequence[ReviewFinding],
) -> bool:
    """Return whether a payload's findings list survived parsing.

    An empty ``findings`` list is a clean review, not junk: a config that
    reports nothing is a real, comparable result. A *non-empty* list that
    parses to nothing is the junk case — the run claimed findings the harness
    cannot read, so it must never be scored as a zero-finding review.

    Args:
        payload: Decoded review payload, already known by
            :func:`_error_from_payload` to carry a ``findings`` list.
        parsed: Findings :func:`findings_from_payload` recovered from it.

    Returns:
        ``True`` when the list is empty or at least one entry parsed.
    """
    # ``_error_from_payload`` has already returned for anything but a list,
    # so the only question left is whether a non-empty list parsed.
    raw = payload.get("findings")
    if not raw:
        return True
    return bool(parsed)


def _require_safe_ids(*, config: MatrixConfig, item: CorpusItem) -> None:
    """Reject ids that could not be a single output path segment.

    The loaders already reject these (the pattern is
    :data:`review_matrix.spec_loader.SAFE_ID_PATTERN`, imported rather than
    restated so the two layers cannot drift), but a directly constructed
    dataclass must not be able to write outside the run directory — and must
    not be able to spend first and fail afterwards.

    Args:
        config: Config whose id is used as a directory name.
        item: Corpus item whose id is used as a directory name.

    Raises:
        ValueError: When either id is not a single safe path segment.
    """
    for label, value in (("config id", config.config_id), ("item id", item.item_id)):
        if not SAFE_ID_PATTERN.fullmatch(value):
            msg = f"unsafe {label} for an output path: {value!r}"
            raise ValueError(msg)


def _persist(
    *,
    output_dir: Path,
    config: MatrixConfig,
    item: CorpusItem,
    repeat: int,
    result: InvocationResult,
) -> str:
    """Write one run's raw payload and stderr to disk.

    Args:
        output_dir: Root run directory.
        config: Config that produced the run.
        item: Corpus item that was reviewed.
        repeat: 1-based repeat index.
        result: Raw invocation result.

    Returns:
        The payload path, relative to ``output_dir``. Ids are re-validated
        through :func:`_require_safe_ids` so a direct call cannot write
        outside ``output_dir``.
    """
    _require_safe_ids(config=config, item=item)
    relative = Path(config.config_id) / item.item_id / f"run-{repeat}.json"
    payload_path = output_dir / relative
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        payload_path.with_suffix(".stderr.txt").write_text(
            result.stderr,
            encoding="utf-8",
        )
    return relative.as_posix()


def _run_to_record(
    *,
    config: MatrixConfig,
    item: CorpusItem,
    repeat: int,
    result: InvocationResult,
    output_path: str,
) -> EvalRun:
    """Turn a raw invocation result into a persisted run record.

    Args:
        config: Config that produced the run.
        item: Corpus item that was reviewed.
        repeat: 1-based repeat index.
        result: Raw invocation result.
        output_path: Where the raw payload was persisted.

    Returns:
        The run record, with its verdict derived in code from its findings.
        Only a complete review with a usable findings list becomes ``OK``; a
        partial or coverage-incomplete review becomes ``INCOMPLETE`` and is
        never comparable.
    """
    payload = _decode_payload(result.stdout)
    if result.exit_code != 0 and payload is None:
        return EvalRun(
            config_id=config.config_id,
            item_id=item.item_id,
            repeat=repeat,
            status=RunStatus.FAILED,
            elapsed_seconds=result.elapsed_seconds,
            exit_code=result.exit_code,
            error=result.stderr.strip()[:500] or "invocation failed",
            output_path=output_path,
        )
    if payload is None:
        return EvalRun(
            config_id=config.config_id,
            item_id=item.item_id,
            repeat=repeat,
            status=RunStatus.INVALID_OUTPUT,
            elapsed_seconds=result.elapsed_seconds,
            exit_code=result.exit_code,
            error="stdout was not a review JSON payload",
            output_path=output_path,
        )
    error = _error_from_payload(payload)
    if error is not None:
        # ``lintro review`` exits 2 with an error envelope when no review was
        # produced (missing credential, provider unreachable, quota). That is
        # a failed cell, never a clean run with zero findings.
        return EvalRun(
            config_id=config.config_id,
            item_id=item.item_id,
            repeat=repeat,
            status=RunStatus.FAILED,
            elapsed_seconds=result.elapsed_seconds,
            exit_code=result.exit_code,
            error=error,
            error_kind=_error_kind_from_payload(payload),
            output_path=output_path,
        )
    findings = findings_from_payload(payload)
    if not _has_usable_findings(payload, parsed=findings):
        return EvalRun(
            config_id=config.config_id,
            item_id=item.item_id,
            repeat=repeat,
            status=RunStatus.INVALID_OUTPUT,
            elapsed_seconds=result.elapsed_seconds,
            cost_usd=_cost_from_payload(payload),
            exit_code=result.exit_code,
            error="findings list had no usable entries",
            output_path=output_path,
        )
    incomplete = _incomplete_reason(payload)
    if incomplete is not None:
        # A truncated review is not a config that found less. Its findings are
        # kept for inspection, but the run never enters a metric.
        return EvalRun(
            config_id=config.config_id,
            item_id=item.item_id,
            repeat=repeat,
            status=RunStatus.INCOMPLETE,
            findings=findings,
            elapsed_seconds=result.elapsed_seconds,
            cost_usd=_cost_from_payload(payload),
            exit_code=result.exit_code,
            error=incomplete,
            output_path=output_path,
        )
    return EvalRun(
        config_id=config.config_id,
        item_id=item.item_id,
        repeat=repeat,
        status=RunStatus.OK,
        verdict=verdict_for(findings=findings),
        findings=findings,
        elapsed_seconds=result.elapsed_seconds,
        cost_usd=_cost_from_payload(payload),
        exit_code=result.exit_code,
        output_path=output_path,
    )


def run_to_dict(*, run: EvalRun) -> dict[str, Any]:
    """Serialize one run record to a JSON-ready mapping.

    Findings are reduced to their identity fields: this is the metric record,
    and the full payloads already live beside it on disk. The same shape is
    used by the incremental journal and by
    :func:`review_matrix.report.report_to_dict`, so a report and a journal can
    never describe the same run differently.

    Args:
        run: Run record to serialize.

    Returns:
        A mapping whose every value is JSON-encodable.
    """
    return {
        "config_id": run.config_id,
        "item_id": run.item_id,
        "repeat": run.repeat,
        "status": str(run.status),
        "verdict": str(run.verdict) if run.verdict is not None else None,
        "finding_count": len(run.findings),
        "findings": [
            {
                "severity": str(finding.severity),
                "category": finding.category,
                "file": finding.file,
                "line": finding.line,
                "title": finding.title,
                "kind": str(finding.kind),
            }
            for finding in run.findings
        ],
        "elapsed_seconds": run.elapsed_seconds,
        "cost_usd": run.cost_usd,
        "exit_code": run.exit_code,
        "error": run.error,
        "error_kind": run.error_kind,
        "output_path": run.output_path,
    }


def _append_run(*, output_dir: Path, run: EvalRun) -> None:
    """Append one run record to the run journal.

    Args:
        output_dir: Root run directory.
        run: Run record to journal.
    """
    line = json.dumps(run_to_dict(run=run), sort_keys=True)
    with (output_dir / RUNS_JSONL_NAME).open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def execute_matrix(
    *,
    spec: MatrixSpec,
    corpus: Corpus,
    output_dir: Path,
    invoker: ReviewInvoker | None = None,
) -> tuple[EvalRun, ...]:
    """Run every (config, item, repeat) cell and persist each result.

    Every config and item id is validated before the first invocation, so an
    unsafe id can never follow spend on an earlier cell. Cells are then
    executed config-major so a matrix aborted part-way still has complete
    repeat sets for the configs it reached, and each record is appended to
    ``runs.jsonl`` as it is produced, so an abort keeps every result the
    matrix already paid for.

    Args:
        spec: Matrix specification.
        corpus: Corpus to review.
        output_dir: Root directory for persisted payloads.
        invoker: Invocation callable; defaults to the real CLI subprocess.

    Returns:
        Every run record, in execution order.
    """
    invoke: ReviewInvoker = run_review_cli if invoker is None else invoker
    # One pass over the whole matrix before the first paid invocation: an
    # unsafe id in the last config must not be discovered after the first
    # config has already spent.
    for config in spec.configs:
        for item in corpus.items:
            _require_safe_ids(config=config, item=item)
    runs: list[EvalRun] = []
    for config in spec.configs:
        for item in corpus.items:
            for repeat in range(1, spec.repeats + 1):
                result = invoke(config=config, item=item, spec=spec)
                output_path = _persist(
                    output_dir=output_dir,
                    config=config,
                    item=item,
                    repeat=repeat,
                    result=result,
                )
                run = _run_to_record(
                    config=config,
                    item=item,
                    repeat=repeat,
                    result=result,
                    output_path=output_path,
                )
                _append_run(output_dir=output_dir, run=run)
                runs.append(run)
    return tuple(runs)


def summarize_runs(runs: Sequence[EvalRun]) -> float:
    """Return the total *known* cost of a set of runs.

    Runs whose cost could not be read are skipped rather than counted as
    zero; :func:`count_unknown_costs` reports how many, so a total is never
    read as complete when part of the spend is unknown.

    Args:
        runs: Runs to total.

    Returns:
        Sum of every run's ``cost_usd`` that is not ``None``.
    """
    return sum(run.cost_usd for run in runs if run.cost_usd is not None)


def count_unknown_costs(runs: Sequence[EvalRun]) -> int:
    """Return how many runs recorded no readable cost.

    Args:
        runs: Runs to inspect.

    Returns:
        Number of runs whose ``cost_usd`` is ``None``.
    """
    return sum(1 for run in runs if run.cost_usd is None)
