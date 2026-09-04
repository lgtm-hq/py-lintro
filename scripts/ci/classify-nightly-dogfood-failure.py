#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
r"""Decide whether a nightly dogfood failure should ping the failure tracker.

``dogfood-nightly.yml`` opens or pings a deduplicated tracker issue on any
failure. GitHub-side runner loss ("The runner has received a shutdown signal",
SIGTERM/exit 143) kills the nightly lint and skip-gate jobs repeatedly, and
every kill used to arrive on the tracker indistinguishable from a real nightly
regression (#2246, epic #2245). This script applies the binding state table
from #2246 to the *same-run* job results, so a kill that a bounded retry
already answered stops reaching the tracker:

===================================  ==============  ==========================
Primary outcome                      Retry outcome   Tracker
===================================  ==============  ==========================
Genuine lint/skip failure            not run         ping, exactly as today
Infra kill (classified)              success         no ping
Infra kill (classified)              genuine failure ping — real regression
Infra kill (classified)              infra kill      ping, annotated "no verdict
                                                     tonight", no action-required
Unclassifiable failure               any             ping (fail closed)
===================================  ==============  ==========================

Classification is deliberately same-run: it reads job results and job outputs
that the jobs themselves published, never Actions API logs, whose ingestion
race is the very problem #2238 exists to fix. The structural infra signatures
are not re-implemented here — each attempt is handed to
``scripts/ci/is-infra-flake-failure.sh``, the single source of truth the PR
path already uses (exit 143, cancelled/timed_out, lint-passed-but-job-failed,
tool-execution timeout on the attempt's own report).

An attempt that failed while publishing no lint verdict at all (empty
``status``/``exit-code``, the signature of a runner killed mid-lint) is not
proof of infra noise, so it never clears the tracker on its own. It only
becomes harmless when the bounded retry publishes a passing verdict of its
own — positive evidence about the same repository state on the same night.

Usage:
    LINT_RESULT=failure LINT_EXIT_CODE=143 LINT_RETRY_RESULT=success \\
        python3 scripts/ci/classify-nightly-dogfood-failure.py

Environment (all optional except where a job always reports a result; every
value is the workflow's ``needs.<job>.result`` / ``needs.<job>.outputs.<name>``):
    LINT_RESULT, LINT_CONCLUSION, LINT_STATUS, LINT_EXIT_CODE,
    LINT_TIMEOUT_FLAKE, LINT_TIMED_OUT_TOOLS
    LINT_RETRY_RESULT, LINT_RETRY_CONCLUSION, LINT_RETRY_STATUS,
    LINT_RETRY_EXIT_CODE, LINT_RETRY_TIMEOUT_FLAKE, LINT_RETRY_TIMED_OUT_TOOLS
    SKIP_GATE_RESULT, SKIP_GATE_CONCLUSION, SKIP_GATE_STATUS,
    SKIP_GATE_EXIT_CODE
    SKIP_GATE_RETRY_RESULT, SKIP_GATE_RETRY_CONCLUSION,
    SKIP_GATE_RETRY_STATUS, SKIP_GATE_RETRY_EXIT_CODE
    VERIFY_RESULT, VERIFY_CONCLUSION
    INFRA_FLAKE_SCRIPT  Override for is-infra-flake-failure.sh (tests only).

Outputs (stdout, and appended to ``GITHUB_OUTPUT`` when set):
    notify=true|false           whether to ping the deduplicated tracker
    action-required=true|false  false when every ping is a no-verdict night
    annotation=<one line>       empty unless a coverage gap must stay visible
    reason=<one line>           per-job explanation, for the run log

Exit codes:
    0 — decision written (read the ``notify`` output)
    2 — the shared infra classifier could not be run
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404 - fixed argv, repo-local classifier script
import sys
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

_INFRA_FLAKE_SCRIPT = Path(__file__).resolve().parent / "is-infra-flake-failure.sh"

# Results that mean the job never produced an outcome to classify.
_NOT_RUN_RESULTS: frozenset[str] = frozenset({"", "skipped"})

# Annotation for the one state that must stay visible without demanding
# action: two runner kills in a row leave the night without a lint verdict.
_NO_VERDICT_ANNOTATION = (
    "Infra kill x2: no lint verdict tonight, superseded by the next scheduled "
    "run. No action required — this ping records the coverage gap."
)


class AttemptState(StrEnum):
    """Outcome of one lint/gate attempt.

    Attributes:
        NOT_RUN: The job was skipped or never reported a result.
        PASSED: The job succeeded.
        INFRA: The shared classifier recognised runner/infra noise.
        NO_VERDICT: The job failed without publishing any lint verdict.
        GENUINE: The job published a real failing verdict.
        UNCLASSIFIABLE: A failure that fits none of the above (fail closed).
    """

    NOT_RUN = auto()
    PASSED = auto()
    INFRA = auto()
    NO_VERDICT = auto()
    GENUINE = auto()
    UNCLASSIFIABLE = auto()


class UnitVerdict(StrEnum):
    """Tracker decision for one primary/retry pair.

    Attributes:
        CLEAR: Nothing to report to the tracker.
        PING: Ping the tracker exactly as before (action required).
        PING_NO_VERDICT: Ping, annotated as a coverage gap, no action required.
    """

    CLEAR = auto()
    PING = auto()
    PING_NO_VERDICT = auto()


@dataclass(frozen=True)
class Attempt:
    """One job attempt as the workflow reports it.

    Attributes:
        result: The job's ``needs.<job>.result``.
        conclusion: The job's conclusion when distinct from ``result``.
        status: The lint ``status`` output (``passed``/``failed``/empty).
        exit_code: The lint ``exit-code`` output (``0``/``1``/``143``/empty).
        timeout_flake: ``true`` when this attempt's own report proves its only
            failures were tool-execution timeouts with zero findings (#1653).
        timed_out_tools: Comma-separated tool names, for the log message.
    """

    result: str = ""
    conclusion: str = ""
    status: str = ""
    exit_code: str = ""
    timeout_flake: str = ""
    timed_out_tools: str = ""


@dataclass(frozen=True)
class Unit:
    """A primary attempt and the bounded retry that may answer it.

    Attributes:
        name: The workflow job id the pair belongs to.
        primary: The primary attempt.
        retry: The retry attempt (``NOT_RUN`` when the workflow skipped it).
    """

    name: str
    primary: Attempt
    retry: Attempt


@dataclass(frozen=True)
class Decision:
    """The tracker decision for a whole nightly run.

    Attributes:
        notify: True when the deduplicated tracker must be pinged.
        action_required: False when every ping only records a coverage gap.
        annotation: One-line annotation, empty when nothing to annotate.
        reason: One-line, per-job explanation for the run log.
    """

    notify: bool
    action_required: bool
    annotation: str
    reason: str


def _attempt_from_env(prefix: str, *, environ: dict[str, str]) -> Attempt:
    """Build an attempt from the ``<PREFIX>_*`` environment variables.

    Args:
        prefix: Environment variable prefix, e.g. ``LINT`` or ``LINT_RETRY``.
        environ: The environment mapping to read.

    Returns:
        The populated :class:`Attempt`.
    """

    def value(suffix: str) -> str:
        return environ.get(f"{prefix}_{suffix}", "").strip()

    return Attempt(
        result=value("RESULT"),
        conclusion=value("CONCLUSION"),
        status=value("STATUS"),
        exit_code=value("EXIT_CODE"),
        timeout_flake=value("TIMEOUT_FLAKE"),
        timed_out_tools=value("TIMED_OUT_TOOLS"),
    )


def is_infra_flake(attempt: Attempt, *, script: Path) -> bool:
    """Ask the shared classifier whether an attempt is runner/infra noise.

    The signature set lives in ``scripts/ci/is-infra-flake-failure.sh`` and is
    reused rather than duplicated, so the nightly and the PR gate can never
    drift apart on what "infra" means.

    Args:
        attempt: The attempt to classify.
        script: Path to ``is-infra-flake-failure.sh``.

    Raises:
        RuntimeError: If the classifier exits with anything other than its
            two verdict codes (0 = infra, 1 = not infra).

    Returns:
        True when the shared classifier accepts the failure as infra noise.
    """
    env = dict(os.environ)
    env.update(
        {
            "UPSTREAM_RESULT": attempt.result,
            "UPSTREAM_CONCLUSION": attempt.conclusion,
            "STATUS_OUTPUT": attempt.status,
            "EXIT_CODE_OUTPUT": attempt.exit_code,
            "TIMEOUT_FLAKE": attempt.timeout_flake,
            "TIMED_OUT_TOOLS": attempt.timed_out_tools,
        },
    )
    completed = subprocess.run(  # nosec B603 - fixed argv, repo-local script
        ["/usr/bin/env", "bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout.strip():
        print(f"[INFO] {completed.stdout.strip()}")
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    # Only 0 (infra) and 1 (not infra) are verdicts. Anything else means the
    # classifier itself broke, and a broken classifier must never be read as
    # "not infra": that would let an empty-output kill drift to NO_VERDICT and
    # a passing retry silence a night the tracker should have heard about.
    raise RuntimeError(
        f"{script} exited {completed.returncode}: "
        f"{completed.stderr.strip() or 'no diagnostic output'}",
    )


def classify_attempt(attempt: Attempt, *, script: Path) -> AttemptState:
    """Classify one attempt from its same-run result and outputs.

    Args:
        attempt: The attempt to classify.
        script: Path to ``is-infra-flake-failure.sh``.

    Returns:
        The :class:`AttemptState` for the attempt.
    """
    if attempt.result in _NOT_RUN_RESULTS:
        return AttemptState.NOT_RUN
    if attempt.result == "success":
        return AttemptState.PASSED
    if is_infra_flake(attempt, script=script):
        return AttemptState.INFRA
    if attempt.status == "failed" or attempt.exit_code == "1":
        return AttemptState.GENUINE
    if not attempt.status and not attempt.exit_code:
        return AttemptState.NO_VERDICT
    return AttemptState.UNCLASSIFIABLE


def classify_unit(
    unit: Unit,
    *,
    script: Path,
) -> tuple[UnitVerdict, str]:
    """Apply the #2246 state table to one primary/retry pair.

    Args:
        unit: The job pair to decide on.
        script: Path to ``is-infra-flake-failure.sh``.

    Returns:
        The pair's :class:`UnitVerdict` and a one-line explanation.
    """
    primary = classify_attempt(unit.primary, script=script)
    if primary in (AttemptState.PASSED, AttemptState.NOT_RUN):
        return UnitVerdict.CLEAR, f"{unit.name}: {primary}"

    if primary in (AttemptState.GENUINE, AttemptState.UNCLASSIFIABLE):
        # A real verdict (or an unreadable one) is never absorbed, and the
        # workflow does not retry it: this is the tracker's whole purpose.
        return UnitVerdict.PING, f"{unit.name}: {primary} failure, ping"

    retry = classify_attempt(unit.retry, script=script)
    if retry is AttemptState.PASSED:
        # Positive evidence about the same repo state on the same night.
        return UnitVerdict.CLEAR, f"{unit.name}: {primary}, retry passed"
    if retry is AttemptState.NOT_RUN:
        # The retry should have run for this state; fail closed rather than
        # silently swallowing a night with no coverage and no explanation.
        return UnitVerdict.PING, f"{unit.name}: {primary}, no retry ran, ping"
    if retry in (AttemptState.INFRA, AttemptState.NO_VERDICT):
        return (
            UnitVerdict.PING_NO_VERDICT,
            f"{unit.name}: {primary} twice, no verdict",
        )
    return UnitVerdict.PING, f"{unit.name}: {primary}, retry {retry}, ping"


def decide(units: list[Unit], *, script: Path) -> Decision:
    """Decide the tracker action for every job pair in a nightly run.

    Args:
        units: The primary/retry pairs to consider.
        script: Path to ``is-infra-flake-failure.sh``.

    Returns:
        The run-level :class:`Decision`.
    """
    verdicts: list[tuple[UnitVerdict, str]] = [
        classify_unit(unit, script=script) for unit in units
    ]
    reason = "; ".join(explanation for _, explanation in verdicts)
    pinging = [verdict for verdict, _ in verdicts if verdict is not UnitVerdict.CLEAR]
    if not pinging:
        return Decision(
            notify=False,
            action_required=False,
            annotation="",
            reason=reason,
        )
    only_no_verdict = all(verdict is UnitVerdict.PING_NO_VERDICT for verdict in pinging)
    return Decision(
        notify=True,
        action_required=not only_no_verdict,
        annotation=_NO_VERDICT_ANNOTATION if only_no_verdict else "",
        reason=reason,
    )


def build_units(*, environ: dict[str, str]) -> list[Unit]:
    """Build the nightly's job pairs from the environment.

    Args:
        environ: The environment mapping to read.

    Returns:
        One :class:`Unit` per nightly job that can fail. The pinned-digest
        verifier has no retry, so its pair always carries an empty retry.
    """
    return [
        Unit(
            name="dogfood-full",
            primary=_attempt_from_env("LINT", environ=environ),
            retry=_attempt_from_env("LINT_RETRY", environ=environ),
        ),
        Unit(
            name="dogfood-skip-gate",
            primary=_attempt_from_env("SKIP_GATE", environ=environ),
            retry=_attempt_from_env("SKIP_GATE_RETRY", environ=environ),
        ),
        Unit(
            name="verify-pinned-image-tools",
            primary=_attempt_from_env("VERIFY", environ=environ),
            retry=Attempt(),
        ),
    ]


def _emit(decision: Decision) -> None:
    """Publish the decision to stdout, ``GITHUB_OUTPUT`` and the run summary.

    Args:
        decision: The decision to publish.
    """
    lines = (
        f"notify={'true' if decision.notify else 'false'}",
        f"action-required={'true' if decision.action_required else 'false'}",
        f"annotation={decision.annotation}",
        f"reason={decision.reason}",
    )
    for line in lines:
        print(line)

    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    if decision.annotation:
        # Surfaces the coverage gap on the run itself: the deduplicated
        # notifier renders a fixed body and takes no annotation input.
        print(f"::notice title=Nightly dogfood::{decision.annotation}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_path:
        summary = [
            "### Nightly dogfood failure classification",
            "",
            f"- Tracker ping: `{'yes' if decision.notify else 'no'}`",
            f"- Action required: `{'yes' if decision.action_required else 'no'}`",
            f"- Jobs: {decision.reason}",
        ]
        if decision.annotation:
            summary.extend(("", decision.annotation))
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Run the nightly dogfood failure classifier.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``). The
            classifier reads the workflow state from the environment; the
            parser exists so ``--help`` documents that contract.

    Returns:
        Process exit code: 0 when a decision was written, 2 when the shared
        infra classifier is missing, cannot be executed, or exits with
        anything other than its two verdict codes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Decide whether a nightly dogfood failure should ping the "
            "deduplicated failure tracker (#2246). Reads the workflow's job "
            "results and outputs from the environment; see the module "
            "docstring for the variable list."
        ),
    )
    parser.parse_args(argv)

    script = Path(os.environ.get("INFRA_FLAKE_SCRIPT", "") or _INFRA_FLAKE_SCRIPT)
    if not script.is_file():
        print(f"[ERROR] infra classifier not found: {script}", file=sys.stderr)
        return 2

    units = build_units(environ=dict(os.environ))
    try:
        decision = decide(units, script=script)
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] cannot run {script}: {exc}", file=sys.stderr)
        return 2

    _emit(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
