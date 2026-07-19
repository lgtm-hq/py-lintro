"""Tests for check-dogfood-skips.py (dogfood no-silent-skip gate, #1510)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from assertpy import assert_that


def _load_module() -> ModuleType:
    """Load check-dogfood-skips.py as an importable test module."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "check-dogfood-skips.py"
    )
    spec = importlib.util.spec_from_file_location("check_dogfood_skips", script_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotations (evaluated lazily under
    # ``from __future__ import annotations``) can resolve via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _skip_result(tool: str, reason: str) -> dict[str, Any]:
    """Build a skipped tool result dict for a lintro JSON report."""
    return {
        "tool": tool,
        "success": True,
        "issues_count": 0,
        "skipped": True,
        "skip_reason": reason,
        "output": f"Skipping {tool}",
    }


def _report(*results: dict[str, Any]) -> dict[str, Any]:
    """Wrap tool result dicts in a lintro JSON report envelope."""
    return {"results": list(results), "summary": {"total_issues": 0}}


def _allowlist(
    allowlist: list[dict[str, Any]] | None,
    interim: list[dict[str, Any]] | None,
) -> Any:
    """Build an allowlist object from raw section lists."""
    data: dict[str, Any] = {}
    if allowlist is not None:
        data["allowlist"] = allowlist
    if interim is not None:
        data["interim"] = interim
    return mod.load_allowlist(data)


# --------------------------------------------------------------------------
# Skip-reason classification
# --------------------------------------------------------------------------


def test_classify_binary_missing_from_version_check() -> None:
    """A failed version check / missing binary classifies as binary_missing."""
    reason = "Failed to run version check: [Errno 2] No such file or directory: 'vale'"
    assert_that(mod.classify_skip_reason(reason)).is_equal_to(
        mod.SkipClass.BINARY_MISSING,
    )


def test_classify_no_config_from_stylelint_reason() -> None:
    """A "no configuration found" reason classifies as no_config."""
    reason = "Skipping stylelint: no stylelint configuration found (e.g. .stylelintrc)."
    assert_that(mod.classify_skip_reason(reason)).is_equal_to(mod.SkipClass.NO_CONFIG)


def test_classify_opt_in_disabled_from_idiom_review_reason() -> None:
    """The idiom-review opt-in reason classifies as opt_in_disabled."""
    reason = "idiom-review is disabled by default; enable it via tools.idiom-review"
    assert_that(mod.classify_skip_reason(reason)).is_equal_to(
        mod.SkipClass.OPT_IN_DISABLED,
    )


def test_classify_empty_reason_is_other() -> None:
    """An empty or missing reason never silently classifies as tolerable."""
    assert_that(mod.classify_skip_reason("")).is_equal_to(mod.SkipClass.OTHER)
    assert_that(mod.classify_skip_reason(None)).is_equal_to(mod.SkipClass.OTHER)


def test_classify_unrecognised_reason_is_other() -> None:
    """An unrecognised reason classifies as other, not a known class."""
    assert_that(mod.classify_skip_reason("some brand new reason")).is_equal_to(
        mod.SkipClass.OTHER,
    )


# --------------------------------------------------------------------------
# Tool-name normalization
# --------------------------------------------------------------------------


def test_normalize_tool_name_folds_hyphens_and_case() -> None:
    """Hyphen and underscore spellings normalize to the same key."""
    assert_that(mod.normalize_tool_name("idiom-review")).is_equal_to("idiom_review")
    assert_that(mod.normalize_tool_name("PIP_AUDIT")).is_equal_to("pip_audit")


# --------------------------------------------------------------------------
# Allowlist parsing / validation
# --------------------------------------------------------------------------


def test_load_allowlist_none_yields_empty() -> None:
    """An empty (None) document yields an allowlist with no entries."""
    allowlist = mod.load_allowlist(None)
    assert_that(allowlist.entries).is_empty()


def test_permanent_entry_rejects_binary_missing_class() -> None:
    """binary_missing is never permitted in the permanent allowlist section."""
    with pytest.raises(mod.AllowlistError) as exc:
        _allowlist(
            allowlist=[
                {
                    "tool": "pip_audit",
                    "reason_class": "binary_missing",
                    "rationale": "nope",
                },
            ],
            interim=None,
        )
    assert_that(str(exc.value)).contains("not permanently allowlistable")


def test_interim_entry_requires_issue() -> None:
    """Interim tolerations must reference a tracking issue."""
    with pytest.raises(mod.AllowlistError) as exc:
        _allowlist(
            allowlist=None,
            interim=[
                {
                    "tool": "stylelint",
                    "reason_class": "no_config",
                    "rationale": "missing config",
                },
            ],
        )
    assert_that(str(exc.value)).contains("must reference a tracking 'issue'")


def test_interim_entry_allows_binary_missing_with_issue() -> None:
    """binary_missing IS tolerable as an interim entry with a tracking issue."""
    allowlist = _allowlist(
        allowlist=None,
        interim=[
            {
                "tool": "pip_audit",
                "reason_class": "binary_missing",
                "issue": 1505,
                "rationale": "interim until image bump",
            },
        ],
    )
    entry = allowlist.get("pip-audit")
    assert_that(entry).is_not_none()
    assert_that(entry.interim).is_true()
    assert_that(entry.issue).is_equal_to(1505)


def test_duplicate_tool_entry_is_rejected() -> None:
    """A tool may appear at most once across allowlist + interim."""
    with pytest.raises(mod.AllowlistError) as exc:
        _allowlist(
            allowlist=[
                {
                    "tool": "vale",
                    "reason_class": "no_config",
                    "rationale": "x",
                },
            ],
            interim=[
                {
                    "tool": "vale",
                    "reason_class": "no_config",
                    "issue": 1492,
                    "rationale": "y",
                },
            ],
        )
    assert_that(str(exc.value)).contains("duplicate allowlist entry")


def test_invalid_reason_class_is_rejected() -> None:
    """An unknown reason_class value fails allowlist validation."""
    with pytest.raises(mod.AllowlistError):
        _allowlist(
            allowlist=[
                {"tool": "vale", "reason_class": "bogus", "rationale": "x"},
            ],
            interim=None,
        )


# --------------------------------------------------------------------------
# Skip evaluation against the allowlist
# --------------------------------------------------------------------------


def test_binary_missing_without_entry_is_violation() -> None:
    """A missing-binary skip with no allowlist entry is a hard violation."""
    payload = _report(
        _skip_result(
            "osv_scanner",
            "Failed to run version check: No such file or directory: 'osv-scanner'",
        ),
    )
    findings = mod.evaluate_skips(payload, mod.load_allowlist(None))
    assert_that(findings).is_length(1)
    assert_that(findings[0].allowed).is_false()
    assert_that(findings[0].skip_class).is_equal_to(mod.SkipClass.BINARY_MISSING)


def test_no_config_skip_is_allowed_when_interim_listed() -> None:
    """A no_config skip listed as interim is allowed (as a warning)."""
    payload = _report(
        _skip_result(
            "stylelint",
            "Skipping stylelint: no stylelint configuration found.",
        ),
    )
    allowlist = _allowlist(
        allowlist=None,
        interim=[
            {
                "tool": "stylelint",
                "reason_class": "no_config",
                "issue": 1491,
                "rationale": "interim",
            },
        ],
    )
    findings = mod.evaluate_skips(payload, allowlist)
    assert_that(findings).is_length(1)
    assert_that(findings[0].allowed).is_true()
    assert_that(findings[0].is_warning).is_true()


def test_opt_in_permanent_allowlist_is_allowed_without_warning() -> None:
    """An opt_in_disabled permanent entry is allowed and is not a warning."""
    payload = _report(
        _skip_result(
            "idiom-review",
            "idiom-review is disabled by default; enable it via tools.idiom-review",
        ),
    )
    allowlist = _allowlist(
        allowlist=[
            {
                "tool": "idiom-review",
                "reason_class": "opt_in_disabled",
                "rationale": "no API key in CI",
            },
        ],
        interim=None,
    )
    findings = mod.evaluate_skips(payload, allowlist)
    assert_that(findings).is_length(1)
    assert_that(findings[0].allowed).is_true()
    assert_that(findings[0].is_warning).is_false()


def test_class_mismatch_is_violation() -> None:
    """A skip whose class differs from its allowlist entry is a violation."""
    payload = _report(
        _skip_result(
            "vale",
            "Failed to run version check: No such file or directory: 'vale'",
        ),
    )
    allowlist = _allowlist(
        allowlist=None,
        interim=[
            {
                "tool": "vale",
                "reason_class": "no_config",
                "issue": 1492,
                "rationale": "interim",
            },
        ],
    )
    findings = mod.evaluate_skips(payload, allowlist)
    assert_that(findings).is_length(1)
    assert_that(findings[0].allowed).is_false()
    assert_that(findings[0].message).contains("re-triage")


def test_non_skipped_tools_are_ignored() -> None:
    """Tools that ran (skipped=false) produce no findings."""
    payload = _report(
        {
            "tool": "ruff",
            "success": True,
            "issues_count": 0,
            "skipped": False,
            "skip_reason": None,
            "output": "ok",
        },
    )
    findings = mod.evaluate_skips(payload, mod.load_allowlist(None))
    assert_that(findings).is_empty()


def test_extract_results_rejects_missing_results_key() -> None:
    """A payload without a results array is a malformed report."""
    with pytest.raises(ValueError):
        mod.evaluate_skips({"summary": {}}, mod.load_allowlist(None))


# --------------------------------------------------------------------------
# End-to-end run() exit codes
# --------------------------------------------------------------------------


def test_run_passes_on_green_report(tmp_path: Path) -> None:
    """run() exits 0 when every skip is allowlisted."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            _report(
                _skip_result(
                    "idiom-review",
                    "idiom-review is disabled by default; enable it via config",
                ),
                _skip_result(
                    "stylelint",
                    "Skipping stylelint: no stylelint configuration found.",
                ),
            ),
        ),
        encoding="utf-8",
    )
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text(
        yaml.safe_dump(
            {
                "allowlist": [
                    {
                        "tool": "idiom-review",
                        "reason_class": "opt_in_disabled",
                        "rationale": "no API key in CI",
                    },
                ],
                "interim": [
                    {
                        "tool": "stylelint",
                        "reason_class": "no_config",
                        "issue": 1491,
                        "rationale": "interim",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    assert_that(mod.run(str(report), str(allowlist))).is_equal_to(0)


def test_run_fails_on_unlisted_skip(tmp_path: Path) -> None:
    """run() exits 1 when a skip is not covered by the allowlist."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            _report(
                _skip_result(
                    "gitleaks",
                    "Failed to run version check: No such file or directory: 'gitleaks'",
                ),
            ),
        ),
        encoding="utf-8",
    )
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("allowlist: []\n", encoding="utf-8")
    assert_that(mod.run(str(report), str(allowlist))).is_equal_to(1)


def test_run_returns_config_error_on_bad_report(tmp_path: Path) -> None:
    """run() exits 2 when the report file is missing or unparseable."""
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("allowlist: []\n", encoding="utf-8")
    assert_that(mod.run(str(tmp_path / "nope.json"), str(allowlist))).is_equal_to(2)


def test_run_returns_config_error_on_invalid_allowlist(tmp_path: Path) -> None:
    """run() exits 2 when the allowlist violates the schema."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text(
        yaml.safe_dump(
            {
                "allowlist": [
                    {
                        "tool": "pip_audit",
                        "reason_class": "binary_missing",
                        "rationale": "not allowed permanently",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    assert_that(mod.run(str(report), str(allowlist))).is_equal_to(2)


def test_committed_allowlist_is_valid_and_seeds_expected_tools() -> None:
    """The committed allowlist parses and seeds the tracked tools."""
    allowlist_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "dogfood-skip-allowlist.yaml"
    )
    data = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    allowlist = mod.load_allowlist(data)
    for tool in ("idiom-review", "stylelint", "vale", "commitlint", "pip_audit"):
        assert_that(allowlist.get(tool)).is_not_none()
    assert_that(allowlist.get("idiom-review").interim).is_false()
    assert_that(allowlist.get("pip_audit").issue).is_equal_to(1505)
