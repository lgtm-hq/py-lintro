"""Runtime Semgrep overrides belong to one check, including discovery state."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - process results only; no real subprocesses
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.rules import resolve_rule_pack
from lintro.tools.semgrep.definition import SemgrepPlugin

PACK = "python/none-coercion"
SCRIPT_PACK = "scripts/no-direct-formatter"
REPORT = json.dumps({"results": [], "errors": [], "paths": {"scanned": ["scan.py"]}})


@pytest.fixture
def scan_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semgrep_plugin: SemgrepPlugin,
) -> list[tuple[list[str], float]]:
    """Keep discovery real while recording the external command and timeout.

    Args:
        tmp_path: Isolated scan project.
        monkeypatch: Replace only the subprocess boundary and working directory.
        semgrep_plugin: Plugin with version probing bypassed.

    Returns:
        Commands and timeouts used for scans that reach the engine.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "scan.py").write_text("value = 1\n", encoding="utf-8")
    invocations: list[tuple[list[str], float]] = []

    def run(cmd: list[str], timeout: float, cwd: str) -> SubprocessResult:
        invocations.append((cmd, timeout))
        return SubprocessResult(0, REPORT, "", REPORT)

    monkeypatch.setattr(semgrep_plugin, "_run_subprocess_result", run)
    return invocations


def test_runtime_pack_does_not_enable_later_script_discovery(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    scan_invocations: list[tuple[list[str], float]],
) -> None:
    """A runtime opt-in discovers scripts once and leaves subsequent checks off.

    Args:
        semgrep_plugin: Reused plugin under test.
        tmp_path: Project containing an extensionless script.
        scan_invocations: Observable engine invocations.
    """
    script = tmp_path / "scripts" / "generate"
    script.parent.mkdir()
    script.write_text("black generated.py\n", encoding="utf-8")
    before = semgrep_plugin.check([str(script)], {})
    selected = semgrep_plugin.check([str(script)], {"rule_packs": [SCRIPT_PACK]})
    after = semgrep_plugin.check([str(script)], {})

    assert_that(before.no_files).is_true()
    assert_that(selected.success).is_true()
    assert_that(selected.inspected_files).is_true()
    assert_that(after.no_files).is_true()
    assert_that(after.inspected_files).is_false()
    assert_that(semgrep_plugin.options["rule_packs"]).is_empty()
    assert_that(scan_invocations[0][0]).contains(str(resolve_rule_pack(SCRIPT_PACK)))


def test_runtime_overrides_preserve_configured_native_options(
    semgrep_plugin: SemgrepPlugin,
    scan_invocations: list[tuple[list[str], float]],
) -> None:
    """Command, timeout and discovery overrides leave configured state intact.

    Args:
        semgrep_plugin: Plugin with persistent native configuration.
        scan_invocations: Commands and timeouts from sequential checks.
    """
    semgrep_plugin.set_options(config="native.yaml", jobs=2, timeout=30)
    configured = dict(semgrep_plugin.options)
    exclusions = list(semgrep_plugin.exclude_patterns)
    runtime: dict[str, object] = {
        "config": "override.yaml",
        "jobs": 1,
        "timeout": 7,
        "severity": "info",
        "quiet": True,
        "exclude_patterns": ["ignored.py"],
        "include_venv": True,
    }
    overridden = semgrep_plugin.check(["scan.py"], runtime)
    restored = semgrep_plugin.check(["scan.py"], {})
    first, second = scan_invocations

    assert_that(overridden.success).is_true()
    assert_that(restored.inspected_files).is_true()
    assert_that(first[0]).contains("override.yaml", "--severity", "INFO", "--quiet")
    assert_that(first[1]).is_equal_to(7)
    assert_that(second[0]).contains("native.yaml", "--jobs", "2")
    assert_that(second[0]).does_not_contain("override.yaml", "--severity", "--quiet")
    assert_that(second[1]).is_equal_to(30)
    assert_that(semgrep_plugin.options).is_equal_to(configured)
    assert_that(semgrep_plugin.exclude_patterns).is_equal_to(exclusions)
    assert_that(semgrep_plugin.include_venv).is_false()
    assert_that(runtime["severity"]).is_equal_to("info")


def test_runtime_explicit_auto_does_not_change_later_native_pack_selection(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    scan_invocations: list[tuple[list[str], float]],
) -> None:
    """Explicit auto applies once; later pack-only checks retain native rules.

    Args:
        semgrep_plugin: Plugin whose default auto selection is implicit.
        tmp_path: Project containing native Semgrep configuration.
        scan_invocations: Commands used for each successful scan.
    """
    native = tmp_path / ".semgrep.yaml"
    native.write_text("rules: []\n", encoding="utf-8")
    explicit = semgrep_plugin.check(
        ["scan.py"],
        {"config": "auto", "rule_packs": [PACK]},
    )
    implicit = semgrep_plugin.check(["scan.py"], {"rule_packs": [PACK]})
    configs = [
        [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--config"]
        for cmd, _timeout in scan_invocations
    ]

    assert_that(explicit.success).is_true()
    assert_that(implicit.success).is_true()
    assert_that(configs).is_equal_to(
        [
            ["auto", str(resolve_rule_pack(PACK))],
            [str(native), str(resolve_rule_pack(PACK))],
        ],
    )


def test_runtime_empty_pack_list_disables_configured_pack_once(
    semgrep_plugin: SemgrepPlugin,
    tmp_path: Path,
    scan_invocations: list[tuple[list[str], float]],
) -> None:
    """An empty override disables inherited discovery only for that execution.

    Args:
        semgrep_plugin: Plugin with a persistently selected script pack.
        tmp_path: Project containing a Makefile.
        scan_invocations: Commands from scans that discover work.
    """
    makefile = tmp_path / "Makefile"
    makefile.write_text("format:\n\tblack generated.py\n", encoding="utf-8")
    semgrep_plugin.set_options(rule_packs=[SCRIPT_PACK])
    disabled = semgrep_plugin.check([str(makefile)], {"rule_packs": []})
    enabled = semgrep_plugin.check([str(makefile)], {})

    assert_that(disabled.no_files).is_true()
    assert_that(enabled.success).is_true()
    assert_that(enabled.inspected_files).is_true()
    assert_that(semgrep_plugin.options["rule_packs"]).is_equal_to([SCRIPT_PACK])
    assert_that(scan_invocations[0][0]).contains("Makefile")


@pytest.mark.parametrize("outcome", ["no-files", "timeout", "invalid-report"])
def test_unsuccessful_runtime_scan_does_not_leak_config(
    semgrep_plugin: SemgrepPlugin,
    scan_invocations: list[tuple[list[str], float]],
    outcome: str,
) -> None:
    """Early returns and failed reports also leave the next invocation unchanged.

    Args:
        semgrep_plugin: Reused plugin with a configured native rule file.
        scan_invocations: Commands from the healthy subprocess boundary.
        outcome: How the first scan exits before a usable report.
    """
    semgrep_plugin.set_options(config="native.yaml")
    failure = (
        subprocess.TimeoutExpired("semgrep", 1)
        if outcome == "timeout"
        else SubprocessResult(0, "invalid", "", "invalid")
    )
    with patch.object(semgrep_plugin, "_run_subprocess_result", side_effect=[failure]):
        failed = semgrep_plugin.check(
            [] if outcome == "no-files" else ["scan.py"],
            {"config": "override.yaml"},
        )
    restored = semgrep_plugin.check(["scan.py"], {})

    assert_that(failed.no_files).is_equal_to(outcome == "no-files")
    assert_that(failed.timed_out).is_equal_to(outcome == "timeout")
    assert_that(failed.success).is_equal_to(outcome == "no-files")
    assert_that(restored.success).is_true()
    assert_that(scan_invocations[0][0]).contains("native.yaml")
    assert_that(scan_invocations[0][0]).does_not_contain("override.yaml")


@pytest.mark.parametrize(
    "invalid",
    [
        {"rule_packs": ["python/typo"]},
        {"rule_packs": PACK},
        {"rule_packs": None},
        {"config": 42},
        {"jobs": 0},
        {"severity": "invalid"},
    ],
)
def test_invalid_runtime_options_fail_before_discovery_without_state_changes(
    semgrep_plugin: SemgrepPlugin,
    scan_invocations: list[tuple[list[str], float]],
    invalid: dict[str, object],
) -> None:
    """Invalid overrides cannot hide behind empty paths or poison the next check.

    Args:
        semgrep_plugin: Plugin configured with native and packaged rules.
        scan_invocations: Commands from subsequent successful execution.
        invalid: Malformed runtime option rejected before file discovery.
    """
    semgrep_plugin.set_options(config="native.yaml", rule_packs=[PACK])
    configured = dict(semgrep_plugin.options)
    with pytest.raises(ValueError):
        semgrep_plugin.check([], invalid)
    result = semgrep_plugin.check(["scan.py"], {})

    assert_that(result.success).is_true()
    assert_that(semgrep_plugin.options).is_equal_to(configured)
    assert_that(scan_invocations[0][0]).contains(
        "native.yaml",
        str(resolve_rule_pack(PACK)),
    )
