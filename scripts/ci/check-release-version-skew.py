#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Alarm on post-release version skew across PyPI, npm and the Homebrew tap.

A partially failed release (see #1702) can leave the published channels on
different versions while every visible check still reports success: the npm
publish skips through its ``needs:`` chain (a correct fail-safe), Homebrew keeps
the previous pin, and PyPI moves on alone. Nothing surfaced that skew, so it
stayed invisible until somebody compared the registries by hand.

This script is that comparison, automated. It resolves the current version of
each published channel and fails loudly when they disagree:

    - PyPI: ``https://pypi.org/pypi/<package>/json`` -> ``info.version``
    - npm: ``https://registry.npmjs.org/<package>`` -> ``dist-tags.latest``
    - Homebrew: the tap's ``Formula/lintro.rb`` -> ``version "..."``

It is an alarm, never a gate: it does not block, retry, or reorder the release
pipeline. Two suppression rules keep expected propagation lag quiet:

    1. **Settle window** — a leader version first published less than
       ``--settle-minutes`` ago is still propagating, so skew is expected.
    2. **Release in flight** — PyPI publishes pass through a manual approval
       gate, so a ``waiting``/``queued``/``in_progress`` run of the release
       workflow means the pipeline has simply not finished yet. Homebrew tap lag
       during that window is normal and must not alarm.

Usage:
    python3 scripts/ci/check-release-version-skew.py [options]

Exit codes:
    0 — All channels agree, or the skew is inside a suppression window.
    1 — Version skew: the channels disagree past the suppression windows.
    2 — Check degraded: a channel (or the GitHub API) was unreachable, so no
        verdict could be reached. Distinct from a real mismatch on purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

DEFAULT_PYPI_PACKAGE = "lintro"
DEFAULT_NPM_PACKAGE = "@lgtm-hq/lintro"
DEFAULT_TAP_REPO = "lgtm-hq/homebrew-tap"
DEFAULT_TAP_FORMULA = "Formula/lintro.rb"
DEFAULT_TAP_BRANCH = "main"
DEFAULT_REPO = "lgtm-hq/py-lintro"
DEFAULT_RELEASE_WORKFLOW = "publish-pypi-on-tag.yml"
DEFAULT_SETTLE_MINUTES = 120
DEFAULT_TIMEOUT_SECONDS = 30

EXIT_OK = 0
EXIT_SKEW = 1
EXIT_DEGRADED = 2

# GitHub Actions run states that mean "the release pipeline has not finished".
# ``waiting`` is the manual PyPI approval gate: intentional, never an alarm.
_PENDING_RUN_STATES = frozenset(
    {"queued", "in_progress", "waiting", "pending", "requested", "action_required"},
)

_FORMULA_VERSION = re.compile(r'^\s*version\s+"([^"]+)"', re.MULTILINE)
_VERSION_CORE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")


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
class ChannelStatus:
    """Resolved state of a single publication channel.

    Attributes:
        name: Human-readable channel name (``PyPI``, ``npm``, ``Homebrew``).
        version: The channel's current version, or ``None`` when unreachable.
        published_at: When that version first appeared, when the channel
            exposes a timestamp.
        error: Failure detail when the channel could not be resolved.
    """

    name: str
    version: str | None = None
    published_at: datetime | None = None
    error: str | None = None

    @property
    def reachable(self) -> bool:
        """Return whether the channel produced a usable version."""
        return self.error is None and self.version is not None


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
    # opened; the URLs are built from CLI defaults, not from untrusted input.
    request = (
        urllib.request.Request(  # noqa: S310 — HTTPS-only validated above  # nosec B310
            url,
            headers={"User-Agent": "py-lintro-version-skew-audit"},
        )
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected  # nosec B310
        request,
        timeout=timeout,
    ) as response:
        return str(response.read().decode("utf-8"))


def _parse_timestamp(*, value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def version_sort_key(*, version: str) -> tuple[tuple[int, ...], int, str]:
    """Return a sort key ordering release versions newest-last.

    Numeric components compare numerically; a bare ``X.Y.Z`` release sorts
    after any prerelease suffix of the same core (``0.9.1`` > ``0.9.1rc1``).

    Args:
        version: A version string, with or without a leading ``v``.

    Returns:
        A tuple usable as a ``sorted``/``max`` key.
    """
    core = version.strip().lstrip("vV")
    match = _VERSION_CORE.match(core)
    if match is None:
        return ((), 0, core)
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = match.group(2)
    return (numbers, 0 if suffix else 1, suffix)


def resolve_pypi(
    *,
    package: str,
    fetch: TextFetcher,
) -> ChannelStatus:
    """Resolve the current PyPI version of ``package``.

    Args:
        package: PyPI project name.
        fetch: Text fetcher used for the registry request.

    Returns:
        The resolved channel status.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        payload: dict[str, Any] = json.loads(fetch(url=url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return ChannelStatus(name="PyPI", error=f"{type(exc).__name__}: {exc}")
    version = str(payload.get("info", {}).get("version", "")).strip()
    if not version:
        return ChannelStatus(name="PyPI", error="no info.version in PyPI response")
    uploads = [
        _parse_timestamp(value=entry.get("upload_time_iso_8601"))
        for entry in payload.get("urls", [])
        if isinstance(entry, dict)
    ]
    stamps = [stamp for stamp in uploads if stamp is not None]
    return ChannelStatus(
        name="PyPI",
        version=version,
        published_at=min(stamps) if stamps else None,
    )


def resolve_npm(
    *,
    package: str,
    fetch: TextFetcher,
) -> ChannelStatus:
    """Resolve the current ``latest`` dist-tag version of an npm package.

    Args:
        package: npm package name (may be scoped).
        fetch: Text fetcher used for the registry request.

    Returns:
        The resolved channel status.
    """
    url = f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='')}"
    try:
        payload: dict[str, Any] = json.loads(fetch(url=url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return ChannelStatus(name="npm", error=f"{type(exc).__name__}: {exc}")
    version = str(payload.get("dist-tags", {}).get("latest", "")).strip()
    if not version:
        return ChannelStatus(name="npm", error="no dist-tags.latest in npm response")
    times = payload.get("time", {})
    published_at = _parse_timestamp(
        value=times.get(version) if isinstance(times, dict) else None,
    )
    return ChannelStatus(name="npm", version=version, published_at=published_at)


def resolve_homebrew(
    *,
    repo: str,
    formula: str,
    branch: str,
    fetch: TextFetcher,
) -> ChannelStatus:
    """Resolve the version pinned by the Homebrew tap formula.

    Args:
        repo: Tap repository in ``owner/name`` form.
        formula: Path to the formula inside the tap.
        branch: Tap branch to read.
        fetch: Text fetcher used for the raw file request.

    Returns:
        The resolved channel status.
    """
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{formula}"
    try:
        body = fetch(url=url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return ChannelStatus(name="Homebrew", error=f"{type(exc).__name__}: {exc}")
    match = _FORMULA_VERSION.search(body)
    if match is None:
        return ChannelStatus(
            name="Homebrew",
            error=f"no version stanza found in {formula}",
        )
    return ChannelStatus(name="Homebrew", version=match.group(1).strip())


def release_pipeline_pending(
    *,
    repo: str,
    workflow: str,
    fetch: TextFetcher,
    expected: str | None = None,
) -> bool:
    """Return whether the release being audited is still in flight.

    PyPI publishes sit behind a manual approval gate, which surfaces as a
    ``waiting`` run. While such a run exists for *this* release the downstream
    channels are expected to lag, so skew must not alarm.

    The run must be correlated with ``expected``. A pending run for a different
    version is not evidence about this one: the approval gate is manual, so a
    release that is never approved stays ``waiting`` indefinitely and would
    otherwise suppress the alarm for every later release, permanently. When
    ``expected`` is None the correlation cannot be made and any pending run
    suppresses, which is the conservative direction (no false alarm).

    Args:
        repo: Repository in ``owner/name`` form.
        workflow: Release workflow file name.
        fetch: Text fetcher used for the GitHub API request.
        expected: Version under audit. Runs for other versions are ignored.

    Returns:
        ``True`` when a recent run for ``expected`` has not completed.

    Raises:
        RuntimeError: If the GitHub API could not be queried or parsed.
    """
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{workflow}/runs?per_page=10"
    )
    try:
        payload: dict[str, Any] = json.loads(fetch(url=url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"GitHub API unreachable: {type(exc).__name__}: {exc}",
        ) from exc
    runs = payload.get("workflow_runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("Unexpected GitHub API payload: workflow_runs missing")
    pending_runs = [
        run
        for run in runs
        if isinstance(run, dict) and str(run.get("status", "")) in _PENDING_RUN_STATES
    ]
    if expected is None:
        return bool(pending_runs)
    wanted = expected.strip().lstrip("vV")
    return any(
        str(run.get("head_branch", "")).strip().lstrip("vV") == wanted
        for run in pending_runs
    )


def leader_version(*, channels: list[ChannelStatus]) -> str | None:
    """Return the newest version reported by any reachable channel."""
    versions = [c.version for c in channels if c.reachable and c.version]
    if not versions:
        return None
    return max(versions, key=lambda value: version_sort_key(version=value))


def leader_published_at(
    *,
    channels: list[ChannelStatus],
    leader: str,
) -> datetime | None:
    """Return when ``leader`` first appeared on any channel that reports it."""
    stamps = [
        channel.published_at
        for channel in channels
        if channel.version == leader and channel.published_at is not None
    ]
    return min(stamps) if stamps else None


def render_table(*, channels: list[ChannelStatus], expected: str | None) -> str:
    """Render a per-channel status table as Markdown.

    Args:
        channels: Resolved channel statuses.
        expected: The version every channel should report, when known.

    Returns:
        A Markdown table.
    """
    rows = ["| Channel | Version | Status |", "| --- | --- | --- |"]
    for channel in channels:
        if channel.error is not None:
            rows.append(f"| {channel.name} | — | unreachable: {channel.error} |")
            continue
        if expected is None or channel.version == expected:
            status = "ok"
        else:
            status = f"SKEW (expected {expected})"
        rows.append(f"| {channel.name} | {channel.version} | {status} |")
    return "\n".join(rows)


def _write_summary(*, text: str) -> None:
    """Append ``text`` to the GitHub step summary when running in Actions."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with Path(summary).open("a", encoding="utf-8") as handle:
        handle.write(f"{text}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Alarm on post-release version skew across PyPI, npm and Homebrew."
        ),
        epilog="Exit codes: 0 agree/settling, 1 version skew, 2 check degraded.",
    )
    parser.add_argument(
        "--expected",
        default=None,
        help=(
            "Version every channel must report (default, or when empty: the "
            "newest version any channel reports wins)."
        ),
    )
    parser.add_argument("--pypi-package", default=DEFAULT_PYPI_PACKAGE)
    parser.add_argument("--npm-package", default=DEFAULT_NPM_PACKAGE)
    parser.add_argument("--tap-repo", default=DEFAULT_TAP_REPO)
    parser.add_argument("--tap-formula", default=DEFAULT_TAP_FORMULA)
    parser.add_argument("--tap-branch", default=DEFAULT_TAP_BRANCH)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--release-workflow", default=DEFAULT_RELEASE_WORKFLOW)
    parser.add_argument(
        "--settle-minutes",
        type=int,
        default=DEFAULT_SETTLE_MINUTES,
        help=(
            "Suppress skew while the newest version is younger than this "
            f"(default: {DEFAULT_SETTLE_MINUTES})."
        ),
    )
    return parser


def audit(
    *,
    args: argparse.Namespace,
    fetch: TextFetcher,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Run the version-skew audit and return an exit code and report.

    Args:
        args: Parsed command-line arguments.
        fetch: Text fetcher used for every network request.
        now: Current time, injectable for tests.

    Returns:
        A ``(exit_code, report)`` pair.
    """
    moment = now or datetime.now(tz=UTC)
    # An empty ``--expected`` (workflow dispatch with the input left blank) is
    # the same as omitting it: fall back to the newest channel version.
    requested = args.expected or None
    channels = [
        resolve_pypi(package=args.pypi_package, fetch=fetch),
        resolve_npm(package=args.npm_package, fetch=fetch),
        resolve_homebrew(
            repo=args.tap_repo,
            formula=args.tap_formula,
            branch=args.tap_branch,
            fetch=fetch,
        ),
    ]

    unreachable = [channel for channel in channels if not channel.reachable]
    if unreachable:
        names = ", ".join(channel.name for channel in unreachable)
        table = render_table(channels=channels, expected=requested)
        return EXIT_DEGRADED, (
            f"## Version skew audit: degraded\n\n"
            f"Could not resolve {names}; no skew verdict was reached.\n\n{table}"
        )

    expected = requested or leader_version(channels=channels)
    table = render_table(channels=channels, expected=expected)
    skewed = [channel for channel in channels if channel.version != expected]
    if not skewed:
        return EXIT_OK, f"## Version skew audit: all channels agree\n\n{table}"

    if expected is not None:
        published = leader_published_at(channels=channels, leader=expected)
        if published is not None:
            age_minutes = (moment - published).total_seconds() / 60
            if age_minutes < args.settle_minutes:
                return EXIT_OK, (
                    f"## Version skew audit: settling\n\n"
                    f"`{expected}` is {age_minutes:.0f}m old "
                    f"(settle window {args.settle_minutes}m); "
                    f"channel lag is still expected.\n\n{table}"
                )

    try:
        pending = release_pipeline_pending(
            repo=args.repo,
            workflow=args.release_workflow,
            fetch=fetch,
            expected=expected,
        )
    except RuntimeError as exc:
        return EXIT_DEGRADED, (
            f"## Version skew audit: degraded\n\n"
            f"Channels disagree but the release-pipeline state could not be "
            f"confirmed ({exc}), so no alarm was raised.\n\n{table}"
        )
    if pending:
        return EXIT_OK, (
            f"## Version skew audit: release in flight\n\n"
            f"A `{args.release_workflow}` run has not completed (the PyPI "
            f"approval gate is a `waiting` run); channel lag is expected.\n\n"
            f"{table}"
        )

    lagging = ", ".join(f"{c.name}={c.version}" for c in skewed)
    return EXIT_SKEW, (
        f"## Version skew audit: FAILED\n\n"
        f"Published channels disagree past the settle window. "
        f"Expected `{expected}`; lagging: {lagging}.\n\n{table}"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the version-skew audit.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    exit_code, report = audit(args=args, fetch=fetch_text)
    print(report)
    _write_summary(text=report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
