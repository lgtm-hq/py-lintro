#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Report whether the last tag publish failed at startup (#2516, #2550).

``release-version-pr.yml`` opens a version PR on every push to ``main`` with
``auto-merge: true``, and ``release-auto-tag.yml`` tags the merge. When the tag
publish pipeline is broken, every unrelated merge to ``main`` mints another
version that never ships: eleven versions, ``v0.151.2`` through ``v0.152.6``,
were burned that way between 2026-09-07 and 2026-09-09 while PyPI stayed on
``v0.151.1``.

This script is the gate in front of that loop. It lists ``push`` runs of the
tag publish workflow, keeps the ones whose ``head_branch`` looks like a version
tag (``v`` followed by digits, e.g. ``v0.152.7``), takes the most recent one by
``created_at``, and reports ``publish_green=false`` only when that run
concluded ``startup_failure`` (#2550).

**Only a startup failure gates.** ``startup_failure`` is GitHub's verdict for a
run that never started — unparsable YAML, a missing reusable workflow, a
permissions block the runner rejects. That is the publish pipeline being
broken, and every further tag would hit the same wall. Every other conclusion
(``success``, ``failure``, ``cancelled``, ``timed_out``, ``action_required``,
or none recorded) belongs to a run that did start: a flaky PyPI upload, a
cancelled run or a timeout is a one-off that the next tag may well clear, and
freezing the release train on it costs more than the version it saves.

**The newest run wins, in flight or not.** The gate reads the newest tag run by
``created_at`` regardless of status, not the newest *completed* one. A queued,
waiting or in-progress run has no startup failure to show, so it is green — and
picking it deliberately means an in-flight publish neither resurrects an older
verdict nor blocks the version PR behind it.

**The gate is a skip, not an error.** It never fails the job: every path exits
0 and the verdict travels in the ``publish_green`` output, which the workflow
feeds to the ``if:`` on the version-PR job. That includes the failure modes of
the gate itself — *any* error while reading the GitHub API, whatever its
exception class, reports green with a summary line naming the error and saying
the gate could not be evaluated. Fail-open is deliberate:
a gate that fails closed on an API hiccup would silently freeze releases, and
the condition it guards (a broken publish) is loud and already reported
elsewhere. The cost of a wrong green is one extra burned version; the cost of a
wrong red is a stalled release train nobody is watching.

Other benign-by-design cases, all reported green:

    - No tag runs at all (a fresh repository, or a renamed workflow).
    - The newest tag run is queued, waiting or in progress: it has not failed
      at startup, so there is nothing to gate on.
    - ``--force``: the manual override for the first release after a fix.

Usage:
    python3 scripts/ci/check-last-publish-green.py [--force]

Environment:
    GH_TOKEN / GITHUB_TOKEN: token authenticating the Actions API read.
    GITHUB_REPOSITORY: ``owner/name`` slug of the repository to query.
    GITHUB_OUTPUT: when set, receives the ``publish_green`` line.
    GITHUB_STEP_SUMMARY: when set, receives the human-readable verdict.

Exit codes:
    0 — Always. The verdict is the ``publish_green`` output, never the code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_REPO = "lgtm-hq/py-lintro"
DEFAULT_WORKFLOW = "publish-pypi-on-tag.yml"
DEFAULT_TIMEOUT_SECONDS = 30
RUNS_PER_PAGE = 100

EXIT_OK = 0

#: ``head_branch`` of a tag-triggered run is the tag name. Version tags are a
#: ``v`` followed by dot-separated digits; anything else (a branch push, a
#: candidate tag such as ``tools-candidate-…``) is not this pipeline's signal.
_VERSION_TAG = re.compile(r"^v\d+(?:\.\d+)*$")

#: The only conclusion that gates. GitHub reports it when the run never
#: started, which is the "the publish workflow itself is broken" signal.
_STARTUP_FAILURE = "startup_failure"


class TextFetcher(Protocol):
    """Callable that returns the decoded body of a URL."""

    def __call__(self, *, url: str) -> str:
        """Fetch ``url`` and return its body as text.

        Args:
            url: Absolute URL to fetch.

        Returns:
            The decoded response body.
        """
        ...  # pragma: no cover - protocol definition


@dataclass(frozen=True)
class GateVerdict:
    """Outcome of the publish gate.

    Attributes:
        green: Whether the version PR may proceed.
        summary: Markdown line explaining the verdict, written to the job
            summary and to stdout.
        warning: Text of a ``::warning::`` workflow command to emit, set when
            the gate failed open without reaching a verdict. A summary line
            alone is easy to miss; the annotation surfaces on the run itself.
    """

    green: bool
    summary: str
    warning: str | None = None


def fetch_text(*, url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Fetch ``url`` over HTTPS and return the decoded body.

    Args:
        url: Absolute ``https://`` URL to fetch.
        timeout: Socket timeout in seconds.

    Returns:
        The decoded response body.

    Raises:
        ValueError: If ``url`` is not an ``https://`` URL.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Refusing to fetch non-HTTPS URL: {url}")
    # The https:// scheme is asserted above, so no file:/custom scheme can be
    # opened; the URL is built from CLI defaults, not from untrusted input.
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "py-lintro-publish-gate",
            "Accept": "application/vnd.github+json",
        },
    )
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        request.add_header("Authorization", f"Bearer {token}")
    # nosemgrep: dynamic-urllib-use-detected
    with urllib.request.urlopen(  # nosec B310
        request,
        timeout=timeout,
    ) as response:
        return str(response.read().decode("utf-8"))


def is_version_tag(*, ref: str) -> bool:
    """Return whether ``ref`` names a version tag.

    Args:
        ref: A run's ``head_branch`` value, which for a tag push is the tag.

    Returns:
        ``True`` for ``v`` plus dot-separated digits, ``False`` otherwise.
    """
    return bool(_VERSION_TAG.match(ref.strip()))


def list_tag_runs(
    *,
    repo: str,
    workflow: str,
    fetch: TextFetcher,
) -> list[dict[str, Any]]:
    """Return the publish runs triggered by a version-tag push, newest first.

    Args:
        repo: Repository in ``owner/name`` form.
        workflow: Publish workflow file name.
        fetch: Text fetcher used for the GitHub API request.

    Returns:
        Matching run objects sorted by ``created_at``, newest first.

    Raises:
        RuntimeError: If the GitHub API could not be queried or parsed.
    """
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{workflow}/runs?event=push&per_page={RUNS_PER_PAGE}"
    )
    try:
        payload = json.loads(fetch(url=url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"GitHub API unreachable: {type(exc).__name__}: {exc}",
        ) from exc
    except Exception as exc:
        # Total by design. The named cases above carry the clearer message,
        # but the fetch-and-parse boundary must swallow *everything* else too
        # (``http.client`` exceptions such as ``IncompleteRead`` are neither
        # ``OSError`` nor ``ValueError``), because the caller turns any failure
        # here into a green verdict rather than a red job.
        raise RuntimeError(
            f"GitHub API read failed: {type(exc).__name__}: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            "Unexpected GitHub API payload: expected a top-level JSON object",
        )
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise RuntimeError("Unexpected GitHub API payload: workflow_runs missing")
    tag_runs = [
        run
        for run in runs
        if isinstance(run, dict) and is_version_tag(ref=str(run.get("head_branch", "")))
    ]
    return sorted(
        tag_runs,
        key=lambda run: str(run.get("created_at", "")),
        reverse=True,
    )


def evaluate(
    *,
    repo: str,
    workflow: str,
    fetch: TextFetcher,
    force: bool = False,
) -> GateVerdict:
    """Decide whether the version PR may proceed.

    Not green only when the newest version-tag run — by ``created_at``,
    whatever its status — concluded ``startup_failure``.

    Never raises: every failure below the gate — an unreachable API, a
    malformed payload, an exception type nobody anticipated — becomes a green
    verdict carrying the error in its summary, so the gate cannot redden or
    stall a release on its own.

    Args:
        repo: Repository in ``owner/name`` form.
        workflow: Publish workflow file name.
        fetch: Text fetcher used for the GitHub API request.
        force: Bypass the gate and report green.

    Returns:
        The gate verdict and the summary line describing it.
    """
    if force:
        return GateVerdict(
            green=True,
            summary=(
                "## Publish gate: forced\n\n"
                "`--force` was requested, so the last tag publish was not "
                "consulted and the version PR proceeds."
            ),
        )
    try:
        runs = list_tag_runs(repo=repo, workflow=workflow, fetch=fetch)
    except Exception as exc:
        # Deliberately blind: the gate's whole contract is that it never fails
        # the job, so an unforeseen exception class must not escape either.
        detail = (
            str(exc)
            if isinstance(exc, RuntimeError)
            else f"{type(exc).__name__}: {exc}"
        )
        return GateVerdict(
            green=True,
            summary=(
                "## Publish gate: not evaluated\n\n"
                f"The last `{workflow}` tag run could not be read ({detail}), "
                "so the gate could not be evaluated and the version PR "
                "proceeds (the gate never fails the release train on its own "
                "errors)."
            ),
            warning=(
                f"The last {workflow} tag run could not be read ({detail}); "
                "the publish gate failed open and the version PR proceeds "
                "unchecked."
            ),
        )
    if not runs:
        return GateVerdict(
            green=True,
            summary=(
                "## Publish gate: green\n\n"
                f"No `{workflow}` run on a version tag was found, so there is "
                "no broken publish to gate on."
            ),
        )
    latest = runs[0]
    status = str(latest.get("status", "")).strip() or "unknown"
    conclusion = str(latest.get("conclusion") or "none")
    tag = str(latest.get("head_branch", "")).strip() or "unknown tag"
    run_url = str(latest.get("html_url", "")).strip() or "unknown run"
    if conclusion != _STARTUP_FAILURE:
        return GateVerdict(
            green=True,
            summary=(
                "## Publish gate: green\n\n"
                f"The last tag publish (`{tag}`) is `{status}` with conclusion "
                f"`{conclusion}`, which is not a startup failure, so the "
                f"version PR proceeds: {run_url}"
            ),
        )
    return GateVerdict(
        green=False,
        summary=(
            "## Publish gate: version PR skipped\n\n"
            f"The version PR is skipped because the last tag publish (`{tag}`) "
            f"failed at startup: {run_url}\n\nA `startup_failure` means the "
            "publish workflow never ran, so every further tag would hit the "
            "same wall and merges to `main` would keep burning versions "
            "(#2516). Fix the workflow, then re-run this one with "
            "`force: true`."
        ),
    )


def _write_output(*, green: bool) -> str:
    """Emit the verdict to stdout and ``GITHUB_OUTPUT`` when set.

    Args:
        green: Whether the gate reports green.

    Returns:
        The emitted ``publish_green=<value>`` line.
    """
    line = f"publish_green={'true' if green else 'false'}"
    print(line)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    return line


def _write_summary(*, text: str) -> None:
    """Append ``text`` to the GitHub step summary when running in Actions.

    Args:
        text: Markdown appended to the job summary.
    """
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with Path(summary).open("a", encoding="utf-8") as handle:
        handle.write(f"{text}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        description="Report whether the last tag publish failed at startup.",
        epilog="Always exits 0; the verdict is the publish_green output.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the gate and report green (first release after a fix).",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO,
        help="Repository to query, in owner/name form.",
    )
    parser.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW,
        help=f"Publish workflow file name (default: {DEFAULT_WORKFLOW}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the publish gate.

    The workflow passes the force flag as a single, possibly empty, quoted
    word (``"${FORCE_FLAG}"``), which keeps the conditional in the workflow
    expression rather than in inline shell; empty arguments are dropped here.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``EXIT_OK``, always.
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args([arg for arg in raw if arg])
    verdict = evaluate(
        repo=args.repo,
        workflow=args.workflow,
        fetch=fetch_text,
        force=args.force,
    )
    print(verdict.summary)
    if verdict.warning:
        # A job-summary paragraph is easy to scroll past; an annotation shows
        # on the run itself, so a gate that never reached a verdict is visible
        # without opening the summary.
        print(f"::warning title=Publish gate::{verdict.warning}")
    _write_summary(text=verdict.summary)
    _write_output(green=verdict.green)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
