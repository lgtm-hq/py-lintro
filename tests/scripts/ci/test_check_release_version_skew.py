# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the post-release version-skew audit script (#1712)."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SKEW_SCRIPT = ROOT / "scripts" / "ci" / "check-release-version-skew.py"

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
OLD = (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
RECENT = (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def _load_module() -> Any:
    """Load the hyphenated audit script as an importable module."""
    spec = importlib.util.spec_from_file_location(
        "check_release_version_skew",
        SKEW_SCRIPT,
    )
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the module defines a dataclass, and
    # ``dataclasses`` resolves string annotations through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> Any:
    """Return the loaded audit module."""
    return _load_module()


def _pypi_body(*, version: str, uploaded: str = OLD) -> str:
    """Build a stub PyPI JSON payload."""
    return json.dumps(
        {
            "info": {"version": version},
            "urls": [{"upload_time_iso_8601": uploaded}],
        },
    )


def _npm_body(*, version: str, published: str = OLD) -> str:
    """Build a stub npm registry JSON payload."""
    return json.dumps({"dist-tags": {"latest": version}, "time": {version: published}})


def _formula_body(*, version: str) -> str:
    """Build a stub Homebrew formula body."""
    return "\n".join(
        [
            "class Lintro < Formula",
            '  desc "Unified CLI"',
            f'  version "{version}"',
            '  license "MIT"',
            "end",
        ],
    )


def _runs_body(*, statuses: list[str], version: str = "1.2.3") -> str:
    """Build a stub GitHub workflow-runs payload.

    Runs are tagged with ``version`` because suppression is correlated with the
    release under audit: a pending run for some *other* version is not evidence
    about this one (#1712 review).

    Args:
        statuses: Run statuses, newest first.
        version: Tag the runs belong to, without the ``v`` prefix.

    Returns:
        A JSON payload shaped like the GitHub workflow-runs API.
    """
    return json.dumps(
        {
            "workflow_runs": [
                {"status": status, "head_branch": f"v{version}"} for status in statuses
            ],
        },
    )


def _fetcher(
    *,
    pypi: str | Exception,
    npm: str | Exception,
    formula: str | Exception,
    runs: str | Exception | None = None,
) -> Callable[..., str]:
    """Build a stub fetcher routing by URL host/path."""

    def fetch(*, url: str) -> str:
        if "pypi.org" in url:
            response: str | Exception | None = pypi
        elif "registry.npmjs.org" in url:
            response = npm
        elif "raw.githubusercontent.com" in url:
            response = formula
        elif "api.github.com" in url:
            response = runs if runs is not None else _runs_body(statuses=["completed"])
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected URL: {url}")
        if isinstance(response, Exception):
            raise response
        return str(response)

    return fetch


def _args(module: Any, *extra: str) -> Any:
    """Parse default CLI arguments with optional overrides."""
    return module.build_parser().parse_args(list(extra))


def test_all_channels_agree_exits_zero(module: Any) -> None:
    """Matching versions on every channel produce no alarm."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3"),
            npm=_npm_body(version="1.2.3"),
            formula=_formula_body(version="1.2.3"),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(0)
    assert_that(report).contains("all channels agree")


@pytest.mark.parametrize(
    ("lagging_channel", "versions"),
    [
        ("npm", {"pypi": "1.2.3", "npm": "1.2.2", "formula": "1.2.3"}),
        ("Homebrew", {"pypi": "1.2.3", "npm": "1.2.3", "formula": "1.2.2"}),
        ("PyPI", {"pypi": "1.2.2", "npm": "1.2.3", "formula": "1.2.3"}),
    ],
)
def test_single_channel_skew_exits_one(
    module: Any,
    lagging_channel: str,
    versions: dict[str, str],
) -> None:
    """A lagging channel past the settle window alarms with its own row."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version=versions["pypi"]),
            npm=_npm_body(version=versions["npm"]),
            formula=_formula_body(version=versions["formula"]),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(1)
    assert_that(report).contains("FAILED")
    assert_that(report).contains(f"{lagging_channel}=1.2.2")
    assert_that(report).contains("SKEW (expected 1.2.3)")


@pytest.mark.parametrize("channel", ["pypi", "npm", "formula"])
def test_unreachable_channel_exits_two(module: Any, channel: str) -> None:
    """A registry outage is reported distinctly from a version mismatch."""
    bodies: dict[str, Any] = {
        "pypi": _pypi_body(version="1.2.3"),
        "npm": _npm_body(version="1.2.3"),
        "formula": _formula_body(version="1.2.3"),
    }
    bodies[channel] = OSError("connection reset")
    code, report = module.audit(args=_args(module), fetch=_fetcher(**bodies), now=NOW)
    assert_that(code).is_equal_to(2)
    assert_that(report).contains("degraded")
    assert_that(report).contains("unreachable")


def test_malformed_payload_is_unreachable_not_skew(module: Any) -> None:
    """A formula without a version stanza degrades rather than alarms."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3"),
            npm=_npm_body(version="1.2.3"),
            formula="class Lintro < Formula\nend\n",
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(2)
    assert_that(report).contains("Homebrew")


def test_recent_release_is_inside_settle_window(module: Any) -> None:
    """Skew on a just-published version is suppressed as propagation lag."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3", uploaded=RECENT),
            npm=_npm_body(version="1.2.2"),
            formula=_formula_body(version="1.2.2"),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(0)
    assert_that(report).contains("settling")


def test_settle_window_is_configurable(module: Any) -> None:
    """A shorter settle window lets recent skew alarm."""
    code, report = module.audit(
        args=_args(module, "--settle-minutes", "1"),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3", uploaded=RECENT),
            npm=_npm_body(version="1.2.2"),
            formula=_formula_body(version="1.2.2"),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(1)
    assert_that(report).contains("FAILED")


@pytest.mark.parametrize("status", ["waiting", "queued", "in_progress"])
def test_pending_release_run_suppresses_alarm(module: Any, status: str) -> None:
    """The manual PyPI approval gate (a waiting run) must never alarm."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.2"),
            npm=_npm_body(version="1.2.3"),
            formula=_formula_body(version="1.2.3"),
            runs=_runs_body(statuses=[status, "completed"]),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(0)
    assert_that(report).contains("release in flight")


def test_github_api_outage_degrades_instead_of_alarming(module: Any) -> None:
    """An unconfirmable pipeline state degrades rather than firing a false alarm."""
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3"),
            npm=_npm_body(version="1.2.2"),
            formula=_formula_body(version="1.2.3"),
            runs=OSError("api down"),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(2)
    assert_that(report).contains("release-pipeline state could not be confirmed")


def test_expected_version_overrides_leader(module: Any) -> None:
    """An explicit expected version alarms even when all channels agree."""
    code, report = module.audit(
        args=_args(module, "--expected", "1.3.0"),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.3"),
            npm=_npm_body(version="1.2.3"),
            formula=_formula_body(version="1.2.3"),
        ),
        now=NOW,
    )
    assert_that(code).is_equal_to(1)
    assert_that(report).contains("Expected `1.3.0`")


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("0.9.9", "0.10.0"),
        ("1.2.3", "1.2.10"),
        ("1.2.3rc1", "1.2.3"),
        ("v1.2.3", "1.2.4"),
    ],
)
def test_version_sort_key_orders_releases(
    module: Any,
    lower: str,
    higher: str,
) -> None:
    """Version ordering is numeric and treats prereleases as older."""
    assert_that(
        module.version_sort_key(version=lower)
        < module.version_sort_key(version=higher),
    ).is_true()


def test_fetch_text_rejects_non_https(module: Any) -> None:
    """Only HTTPS URLs may be fetched."""
    assert_that(module.fetch_text).raises(ValueError).when_called_with(
        url="http://pypi.org/pypi/lintro/json",
    )


def test_main_writes_step_summary(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The report is appended to the GitHub step summary when set."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        module,
        "fetch_text",
        _fetcher(
            pypi=_pypi_body(version="1.2.3"),
            npm=_npm_body(version="1.2.3"),
            formula=_formula_body(version="1.2.3"),
        ),
    )
    assert_that(module.main([])).is_equal_to(0)
    assert_that(summary.read_text(encoding="utf-8")).contains("all channels agree")


def test_pending_run_for_another_version_does_not_suppress() -> None:
    """A pending run for a different release must not suppress this audit.

    The PyPI approval gate is manual, so a release that is never approved sits
    in ``waiting`` indefinitely. Treating any recent pending run as evidence
    would let that one stale run suppress the skew alarm for every subsequent
    release, permanently (#1712 review).
    """
    module = _load_module()
    payload = json.dumps(
        {
            "workflow_runs": [
                {"status": "waiting", "head_branch": "v0.91.48"},
                {"status": "completed", "head_branch": "v0.91.47"},
            ],
        },
    )

    pending = module.release_pipeline_pending(
        repo="lgtm-hq/py-lintro",
        workflow="publish-pypi-on-tag.yml",
        fetch=lambda url: payload,
        expected="0.91.47",
    )

    assert_that(pending).is_false()


def test_pending_run_for_the_audited_version_suppresses() -> None:
    """The release under audit still publishing is expected lag, not skew."""
    module = _load_module()
    payload = json.dumps(
        {
            "workflow_runs": [
                {"status": "waiting", "head_branch": "v0.91.48"},
            ],
        },
    )

    pending = module.release_pipeline_pending(
        repo="lgtm-hq/py-lintro",
        workflow="publish-pypi-on-tag.yml",
        fetch=lambda url: payload,
        expected="0.91.48",
    )

    assert_that(pending).is_true()


def test_pending_correlation_without_expected_is_conservative() -> None:
    """With no version to correlate against, any pending run suppresses.

    Failing towards suppression avoids a false alarm when the audit has no
    expected version to reason about.
    """
    module = _load_module()
    payload = json.dumps(
        {"workflow_runs": [{"status": "waiting", "head_branch": "v0.91.48"}]},
    )

    pending = module.release_pipeline_pending(
        repo="lgtm-hq/py-lintro",
        workflow="publish-pypi-on-tag.yml",
        fetch=lambda url: payload,
        expected=None,
    )

    assert_that(pending).is_true()


def test_stale_waiting_run_for_older_release_still_alarms(module: Any) -> None:
    """A never-approved older release must not mask skew in a later one.

    End-to-end counterpart of the correlation unit tests: the PyPI gate is
    manual, so an unapproved run stays ``waiting`` forever. Before correlation
    that single run suppressed every subsequent audit (#1712 review).
    """
    code, report = module.audit(
        args=_args(module),
        fetch=_fetcher(
            pypi=_pypi_body(version="1.2.2"),
            npm=_npm_body(version="1.2.3"),
            formula=_formula_body(version="1.2.3"),
            # Waiting run belongs to an unrelated, older tag.
            runs=_runs_body(statuses=["waiting"], version="0.9.9"),
        ),
        now=NOW,
    )

    assert_that(code).is_equal_to(1)
    assert_that(report).contains("FAILED")
