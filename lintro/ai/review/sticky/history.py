"""Run-statistics and run-history renderers for the sticky board.

The *This run* badges, the collapsible run history, and the separate archive
comment history spills into once it would push the primary comment past its
soft limit.
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_badges import format_cost, format_int
from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    MAX_COMMENT_CHARS,
    STICKY_FOOTER,
)
from lintro.ai.review.github_notes import (
    format_synthesis_note_line,
    format_timings_note,
)
from lintro.ai.review.github_render import Section, assemble, sanitize_comment_text
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.run_outcome import NARRATIVE_LIMIT
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sticky.cells import (
    _cell,
    _fmt_compact,
    _plural,
    _severity_cell,
    _short_sha,
    _transport_label,
)
from lintro.ai.review.sticky.constants import (
    _DETAILS_TAG_RE,
    _TITLE_LIMIT,
    VERDICT_EMOJI,
)
from lintro.ai.review.sticky.sections import _tiles_section
from lintro.ai.review.verdict import verdict_label


def _this_run_section(
    *,
    result: ReviewResult,
    transport: str,
    auth_mode: str,
) -> str:
    """Render the single badge table describing the current run.

    One table, one row: the status board's own shape, which omits the
    per-review body's ``strictness`` and ``lintro`` version. Ordering is fixed
    across every surface (epic #1905): model and transport first, then est.
    cost and tokens in / out, then the mechanics. No figure is presented as
    billed — the ``transport`` badge and the ``~`` prefix carry that honesty.

    Args:
        result: Current review result.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.

    Returns:
        The ``This run`` section.
    """
    metadata = result.metadata
    usage = metadata.token_usage
    prefix = "~" if metadata.token_usage_estimated else ""
    model = sanitize_comment_text(metadata.model or "unknown", limit=80)
    source = metadata.model_source
    model_cell = f"`{model}`" + (f" ({source})" if source else "")
    return "\n".join(
        [
            "**This run**",
            "",
            "| model | transport | est. cost | tokens in / out | depth "
            "| files | checks | duration |",
            "| --- | --- | --- | --- |:-:|:-:|:-:|---|",
            (
                f"| {model_cell} "
                f"| {_transport_label(transport=transport, auth_mode=auth_mode)} "
                f"| {format_cost(metadata.cost_estimate_usd, estimated=True)} "
                f"| {prefix}{format_int(int(usage.get('prompt', 0)))} / "
                f"{prefix}{format_int(int(usage.get('completion', 0)))} "
                f"| {metadata.depth} "
                f"| {metadata.files_reviewed} "
                f"| {metadata.checklist_items} "
                f"| {metadata.duration_seconds:.0f}s |"
            ),
            *(
                ["", synthesis_note]
                if (synthesis_note := format_synthesis_note_line(metadata=metadata))
                else []
            ),
            *(
                ["", timings_note]
                if (timings_note := format_timings_note(metadata=metadata))
                else []
            ),
        ],
    )


def _history_section(
    *,
    runs: Sequence[RunRecord],
    limit: int | None,
    resolved_total: int,
    archive_only: bool = False,
    records: tuple[FindingRecord, ...] = (),
) -> str:
    """Render the single run-history collapsible.

    Everything historical lives here and nowhere else: cumulative badges, the
    per-run table, and one mini-summary line per prior round. It is the only
    *top-level* collapsible in the lower half of the comment, and it does nest
    one more layer: :func:`_round_expander` puts a per-round ``<details>``
    inside it, which is where the mini-summaries live.

    Args:
        runs: Every retained run record, oldest first, current run last.
        limit: Number of *prior* runs to include, newest first. ``None``
            includes them all.
        resolved_total: Number of findings resolved across every round.
        archive_only: When True, emit the archive heading without expanders.
        records: Finding records used for per-round severity tiles.

    Returns:
        The collapsible, or an empty string on the first round where there is
        no history to show.
    """
    if len(runs) < 2:
        return ""

    total_cost = sum(run.usage.cost for run in runs)
    total_tokens = sum(run.usage.total for run in runs)
    estimated = any(run.usage.estimated for run in runs)
    prefix = "~" if estimated else ""

    if limit is None:
        shown = runs
    else:
        # Clamp before slicing: a limit above the prior-run count would make the
        # start index negative and silently show the *newest* few instead of all.
        keep = min(max(limit, 0), len(runs) - 1)
        shown = [*runs[:-1][len(runs) - 1 - keep :], runs[-1]]
    dropped = len(runs) - len(shown)

    previous = max(len(runs) - 1, 0)
    heading = (
        f"### 🕘 History · {previous} previous "
        f"{_plural(count=previous, noun='run')} · "
        f"{resolved_total} fixed · "
        f"{format_cost(total_cost, estimated=estimated)} · "
        f"{prefix}{_fmt_compact(value=total_tokens)} tokens"
    )
    if archive_only:
        return (
            f"{heading}\n\n"
            "Per-round expanders live on the archive comment "
            f"({ARCHIVE_MARKER.replace('<!-- ', '').replace(' -->', '')})."
        )
    inner_sections = [
        Section(name="tiles", text=_tiles_section(records=records) if records else ""),
        *(
            Section(
                name=f"round_{run.identity.round}",
                text=_round_expander(run=run, records=records),
            )
            for run in reversed(shown[:-1])
        ),
    ]
    if dropped > 0:
        inner_sections.append(
            Section(
                name="dropped",
                text=f"> ✂️ **{dropped} older "
                f"{_plural(count=dropped, noun='run')} not listed** "
                "(history truncated to fit GitHub's size limit).",
            ),
        )
    inner = assemble(sections=inner_sections, budget=None)
    return (
        f"{heading}\n\n<details>"
        f"<summary>Run-by-run history</summary>\n\n{inner}\n\n</details>"
    )


def _open_after(*, run: RunRecord) -> int:
    """Return how many findings were still open after a round.

    Args:
        run: Run record to read.

    Returns:
        The stored ``open_after`` count, or the raised total on a record
        persisted before that count existed.
    """
    outcome = run.outcome
    if outcome.open_after is not None:
        return outcome.open_after
    return outcome.p1 + outcome.p2 + outcome.p3


def _history_row(*, run: RunRecord, latest: bool) -> str:
    """Render one row of the per-run history table.

    ``Open`` is what was still open *after* the round, not what the round
    raised: a round that reported three findings and fixed two of them left one
    open, and the raised count told that story backwards. A record persisted
    before those counts existed renders the raised total and ``—`` rather than
    a fabricated zero.

    Args:
        run: Run record to render.
        latest: True when this is the most recent run.

    Returns:
        A single Markdown table row.
    """
    identity = run.identity
    usage = run.usage
    outcome = run.outcome
    prefix = "~" if usage.estimated else ""
    short = _short_sha(sha=identity.sha)
    verdict = verdict_label(verdict=outcome.verdict).lower()
    fixed = "—" if outcome.resolved is None else str(outcome.resolved)
    return (
        f"| {identity.round}{' (latest)' if latest else ''} "
        f"| {f'`{short}`' if short else '—'} "
        f"| {VERDICT_EMOJI[outcome.verdict]} {verdict} "
        f"| `{_cell(text=identity.model or 'unknown', limit=60)}` "
        f"| {_open_after(run=run)} "
        f"| {fixed} "
        f"| {prefix}{format_int(usage.prompt)} / "
        f"{prefix}{format_int(usage.completion)} "
        f"| {format_cost(usage.cost, estimated=usage.estimated)} "
        f"| {usage.duration:.0f}s |"
    )


def _round_expander(
    *,
    run: RunRecord,
    records: tuple[FindingRecord, ...],
) -> str:
    """Render one prior-round expander (narrative, fixed table, this-run row).

    Args:
        run: Prior run record.
        records: All tracked findings, used for that round's fixes.

    Returns:
        A ``<details>`` block for the round.
    """
    identity = run.identity
    coverage = run.coverage
    usage = run.usage
    outcome = run.outcome
    short = _short_sha(sha=identity.sha)
    sha_bit = f" · <code>{short}</code>" if short else ""
    fixed = 0 if outcome.resolved is None else outcome.resolved
    prefix = "~" if usage.estimated else ""
    # A capped round stays marked in history (#2003) so a later reader can
    # tell a genuinely clean round from one that reported fewer findings.
    limited = " · ⚠️ coverage limited" if coverage.coverage_limited else ""
    verdict = verdict_label(verdict=outcome.verdict).lower()
    summary = (
        f"<b>Round {identity.round}</b>{sha_bit} · "
        f"{VERDICT_EMOJI[outcome.verdict]} {verdict} · "
        f"{fixed} fixed, {_open_after(run=run)} left open · "
        f"{format_cost(usage.cost, estimated=usage.estimated)} · "
        f"{usage.duration:.0f}s{limited}"
    )
    narrative = _DETAILS_TAG_RE.sub(
        r"&lt;\1\2",
        _cell(text=outcome.narrative, limit=NARRATIVE_LIMIT),
    )
    lines = [
        f"<details><summary>{summary}</summary>",
        "",
    ]
    if narrative:
        lines.extend([f"> {narrative}", ""])
    fixed_rows = [
        record
        for record in records
        if record.status is FindingStatus.RESOLVED
        and record.resolved_round == identity.round
        and not record.is_question
    ]
    if fixed_rows:
        lines.extend(
            [
                "**Fixed this round**",
                "",
                "| Sev | Finding |",
                "|:-:|---|",
            ],
        )
        for record in fixed_rows:
            lines.append(
                f"| {_severity_cell(record=record)} "
                f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ |",
            )
        lines.append("")
    transport = _transport_label(
        transport=identity.transport,
        auth_mode=identity.auth_mode,
    )
    lines.extend(
        [
            "| model | transport | est. cost | tokens in / out | depth "
            "| files | checks | duration |",
            "| --- | --- | --- | --- |:-:|:-:|:-:|---|",
            (
                f"| `{_cell(text=identity.model or 'unknown', limit=60)}` "
                f"| {transport} "
                f"| {format_cost(usage.cost, estimated=usage.estimated)} "
                f"| {prefix}{format_int(usage.prompt)} / "
                f"{prefix}{format_int(usage.completion)} "
                f"| {identity.depth} "
                f"| {coverage.files_reviewed} "
                f"| {coverage.checks} "
                f"| {usage.duration:.0f}s |"
            ),
            "",
            "</details>",
        ],
    )
    return "\n".join(lines)


def _archive_body(
    *,
    runs: Sequence[RunRecord],
    records: tuple[FindingRecord, ...],
) -> str:
    """Render the archive sticky that holds per-round expanders.

    Args:
        runs: Every retained run, oldest first.
        records: Tracked findings.

    Returns:
        Archive comment body, truncated if it exceeds the comment budget.
    """
    header = [
        Section(name="marker", text=ARCHIVE_MARKER),
        Section(name="heading", text="## 🔎 Lintro Review — history archive"),
    ]
    body = assemble(
        sections=[
            *header,
            Section(
                name="preamble",
                text="Older rounds moved here so the primary sticky can keep "
                "this-round content. The primary comment still carries heading "
                "aggregates.",
            ),
            *(
                Section(
                    name=f"round_{run.identity.round}",
                    text=_round_expander(run=run, records=records),
                )
                for run in reversed(runs[:-1])
            ),
            Section(name="footer", text=STICKY_FOOTER),
        ],
        budget=None,
    )
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    # Oldest expanders degrade to their summary line.
    trimmed: list[Section] = [
        *header,
        Section(
            name="preamble",
            text="Older rounds moved here so the primary sticky can keep "
            "this-round content.",
        ),
    ]
    footer = Section(name="footer", text=STICKY_FOOTER)
    for run in reversed(runs[:-1]):
        name = f"round_{run.identity.round}"
        candidate = Section(name=name, text=_round_expander(run=run, records=records))
        probe = assemble(sections=[*trimmed, candidate, footer], budget=None)
        if len(probe) > MAX_COMMENT_CHARS:
            short = _short_sha(sha=run.identity.sha)
            trimmed.append(
                Section(
                    name=name,
                    text=f"**Round {run.identity.round}**"
                    + (f" · `{short}`" if short else "")
                    + f" · {verdict_label(verdict=run.outcome.verdict).lower()}",
                ),
            )
            continue
        trimmed.append(candidate)
    trimmed.append(footer)
    return assemble(sections=trimmed)


def _history_mini_summary(*, run: RunRecord) -> str:
    """Render one prior round's recap under the history table.

    The round line names the verdict; the line under it is the model's own
    one-sentence account of that round when it wrote one, because "🔴 1 · 🟠 2"
    says how many things were wrong and never what they were. A record with no
    stored narrative — a legacy one, or a round whose model returned no summary
    — falls back to the severity counts.

    Args:
        run: Prior run record to summarize.

    Returns:
        Markdown for the recap, as a round line plus its detail line.
    """
    outcome = run.outcome
    short = _short_sha(sha=run.identity.sha)
    where = f" · `{short}`" if short else ""
    verdict = verdict_label(verdict=outcome.verdict).lower()
    head = (
        f"**Round {run.identity.round}**{where} · "
        f"{VERDICT_EMOJI[outcome.verdict]} {verdict}"
        + (" · ⚠️ partial" if run.coverage.partial else "")
    )
    # Table-safe *and* collapsible-safe: the recap sits inside the history
    # <details>, so a model-written closing tag would end it early.
    narrative = _DETAILS_TAG_RE.sub(
        r"&lt;\1\2",
        _cell(text=outcome.narrative, limit=NARRATIVE_LIMIT),
    )
    detail = narrative or f"🔴 {outcome.p1} · 🟠 {outcome.p2} · 🟡 {outcome.p3}"
    return f"{head}\n{detail}"
