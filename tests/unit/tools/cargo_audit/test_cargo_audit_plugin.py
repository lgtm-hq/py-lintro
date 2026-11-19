"""Unit tests for cargo-audit plugin."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import (
    TimeoutExpired,
)  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.cargo_audit.definition import (
    CARGO_AUDIT_DEFAULT_TIMEOUT,
    CargoAuditPlugin,
)
from lintro.utils.fingerprint_baseline import fingerprint


def _proc(*, success: bool, stdout: str = "") -> SubprocessResult:
    """Build a SubprocessResult for mocking ``_run_subprocess_result``.

    Args:
        success: Whether the subprocess succeeded (return code 0).
        stdout: Captured standard output (the JSON report stream).

    Returns:
        SubprocessResult with the JSON payload on stdout.
    """
    return SubprocessResult(
        returncode=0 if success else 1,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


@pytest.fixture
def cargo_audit_plugin() -> CargoAuditPlugin:
    """Provide a CargoAuditPlugin instance for testing.

    Returns:
        A CargoAuditPlugin instance.
    """
    return CargoAuditPlugin()


def test_definition_name(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool name.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.name).is_equal_to("cargo_audit")


def test_definition_can_fix(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool cannot fix issues.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.can_fix).is_false()


def test_definition_tool_type(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the tool type is SECURITY.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.tool_type).is_equal_to(ToolType.SECURITY)


def test_definition_file_patterns(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the file patterns.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    patterns = cargo_audit_plugin.definition.file_patterns
    assert_that(patterns).contains("Cargo.lock")


def test_definition_timeout(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the default timeout.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    assert_that(cargo_audit_plugin.definition.default_timeout).is_equal_to(
        CARGO_AUDIT_DEFAULT_TIMEOUT,
    )


def test_definition_native_configs(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify the native config files.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    configs = cargo_audit_plugin.definition.native_configs
    assert_that(configs).contains(".cargo/audit.toml")


def test_set_options_timeout(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify timeout option can be set.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    cargo_audit_plugin.set_options(timeout=180)
    assert_that(cargo_audit_plugin.options.get("timeout")).is_equal_to(180)


@pytest.mark.parametrize(
    ("timeout", "message"),
    [
        pytest.param(-1, "must be positive", id="negative"),
        pytest.param(0, "must be positive", id="zero"),
        pytest.param(-1.5, "must be an integer", id="float"),
        pytest.param("invalid", "must be an integer", id="string"),
        pytest.param(True, "must be an integer", id="boolean"),
    ],
)
def test_set_options_rejects_invalid_timeout(
    cargo_audit_plugin: CargoAuditPlugin,
    timeout: object,
    message: str,
) -> None:
    """The shared validator rejects anything but a positive integer timeout.

    Args:
        cargo_audit_plugin: The plugin instance.
        timeout: An invalid timeout value.
        message: Expected fragment of the ValueError message.
    """
    with pytest.raises(ValueError, match=message):
        cargo_audit_plugin.set_options(timeout=timeout)  # type: ignore[arg-type]


def test_fix_raises_not_implemented(cargo_audit_plugin: CargoAuditPlugin) -> None:
    """Verify fix raises NotImplementedError.

    Args:
        cargo_audit_plugin: The plugin instance.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        cargo_audit_plugin.fix(["Cargo.lock"], {})
    assert_that(str(exc_info.value)).contains("cannot automatically fix")


def test_check_no_cargo_lock(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully when no Cargo.lock found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    # Pass the directory itself, not a file
    # This simulates a directory without Cargo.lock

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = cargo_audit_plugin.check([str(tmp_path)], {})

    # Either no files found or no Cargo.lock found message
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


@pytest.mark.parametrize(
    "report_wrapper",
    [
        pytest.param("{}", id="plain"),
        pytest.param("warning\n{}", id="warning-prefix"),
        pytest.param("{}\nwarning", id="warning-suffix"),
        pytest.param("warning\n{}\nDone.", id="warning-wrapped"),
        pytest.param("[WARN]\n{}", id="bracketed-warning-prefix"),
        pytest.param("[INFO] fetching\n{}", id="bracketed-info-prefix"),
        pytest.param("note [1]\n{}", id="bracketed-note-prefix"),
        pytest.param(
            "[WARN]\n[INFO] fetching\nnote [1]\n{}\nDone.",
            id="multiple-bracketed-prefixes",
        ),
    ],
)
def test_check_no_vulnerabilities(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
    report_wrapper: str,
) -> None:
    """Check returns success when no vulnerabilities found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
        report_wrapper: Template for permitted text around the JSON object.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    mock_output = """{
        "vulnerabilities": {
            "count": 0,
            "list": []
        }
    }"""
    mock_output = report_wrapper.format(mock_output)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=True, stdout=mock_output),
        ):
            result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(0)
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_none()


def test_check_with_vulnerabilities(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when vulnerabilities found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    mock_output = """{
        "vulnerabilities": {
            "count": 1,
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2021-0001",
                        "title": "Test vulnerability",
                        "severity": "HIGH"
                    },
                    "package": {
                        "name": "vulnerable-crate",
                        "version": "1.0.0"
                    }
                }
            ]
        }
    }"""

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=False, stdout=mock_output),
        ):
            result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_timeout(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            side_effect=TimeoutExpired(
                cmd=["cargo", "audit"],
                timeout=120,
            ),
        ):
            result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


@pytest.mark.parametrize("success", [True, False], ids=["rc0", "rc1"])
@pytest.mark.parametrize(
    "output",
    [
        pytest.param("this is not json {[", id="invalid-json"),
        pytest.param("", id="blank"),
        pytest.param(" \n\t", id="whitespace"),
        pytest.param("null", id="null-report"),
        pytest.param("[]", id="array-report"),
        pytest.param('[{"vulnerabilities": {"list": []}}]', id="array-of-reports"),
        pytest.param("warning\n[]", id="warning-prefixed-empty-array"),
        pytest.param(
            'warning\n[{"vulnerabilities": {"list": []}}]',
            id="warning-prefixed-array",
        ),
        pytest.param(
            '[{"vulnerabilities": {"list": []}}]\nwarning',
            id="warning-suffixed-array",
        ),
        pytest.param(
            'warning\n[{"vulnerabilities": {"list": []}}]\nDone.',
            id="warning-wrapped-array",
        ),
        pytest.param(
            'warning\n[[{"vulnerabilities": {"list": []}}]]\nDone.',
            id="warning-wrapped-nested-array",
        ),
        pytest.param(
            'warning\n[{"vulnerabilities": {"list": []}}\nDone.',
            id="warning-wrapped-unclosed-array",
        ),
        pytest.param(
            '[{"vulnerabilities": {"list": []}}',
            id="bare-unclosed-array",
        ),
        pytest.param(
            '[WARN]\n[{"vulnerabilities": {"list": []}}]',
            id="bracketed-warning-array",
        ),
        pytest.param(
            '[INFO] fetching\n[[{"vulnerabilities": {"list": []}}]]\nDone.',
            id="bracketed-info-nested-array",
        ),
        pytest.param(
            'note [1]\n[{"vulnerabilities": {"list": []}}\nDone.',
            id="bracketed-note-unclosed-array",
        ),
        pytest.param(
            '[WARN]\n[[{"vulnerabilities": {"list": []}}]\nDone.',
            id="bracketed-warning-unclosed-outer-array",
        ),
        pytest.param(
            'warning\n["]", {"vulnerabilities": {"list": []}}]\nDone.',
            id="array-with-quoted-closer",
        ),
        pytest.param(
            'warning\n["[", {"vulnerabilities": {"list": []}}]\nDone.',
            id="array-with-quoted-opener",
        ),
        pytest.param(
            'warning\n["\\" ]", {"vulnerabilities": {"list": []}}\nDone.',
            id="unclosed-array-with-escaped-quote",
        ),
        pytest.param(
            'warning\n{"vulnerabilities": {"list": null}}\nDone.',
            id="warning-wrapped-null-list",
        ),
        pytest.param("{}", id="missing-vulnerabilities"),
        pytest.param('{"vulnerabilities": null}', id="null-vulnerabilities"),
        pytest.param('{"vulnerabilities": []}', id="array-vulnerabilities"),
        pytest.param('{"vulnerabilities": {}}', id="missing-list"),
        pytest.param('{"vulnerabilities": {"list": null}}', id="null-list"),
        pytest.param('{"vulnerabilities": {"list": {}}}', id="object-list"),
        pytest.param('{"vulnerabilities": {"list": false}}', id="boolean-list"),
        pytest.param('{"vulnerabilities": {"list": ""}}', id="string-list"),
    ],
)
def test_check_invalid_report_is_not_clean(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
    output: str,
    success: bool,
) -> None:
    """Missing or invalid reports fail closed regardless of exit status.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory containing a Cargo.lock to audit.
        output: Missing output or an invalid cargo-audit report.
        success: Whether the subprocess exited successfully.
    """
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text('[[package]]\nname = "test"\nversion = "1.0.0"')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=success, stdout=output),
        ),
    ):
        result = cargo_audit_plugin.check([str(cargo_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(output)


def _report(*advisory_ids: str) -> str:
    """Build a cargo-audit JSON report listing the given advisories.

    Args:
        *advisory_ids: RUSTSEC identifiers to report as vulnerabilities.

    Returns:
        The report as cargo-audit prints it.
    """
    entries = [
        {
            "advisory": {
                "id": advisory_id,
                "package": "smallvec",
                "title": "Buffer overflow",
                "description": "Overflow in SmallVec::insert_many",
                "url": f"https://rustsec.org/advisories/{advisory_id}",
            },
            "versions": {"patched": [">=1.6.1"]},
            "package": {"name": "smallvec", "version": "1.6.0"},
        }
        for advisory_id in advisory_ids
    ]
    return json.dumps(
        {"vulnerabilities": {"count": len(entries), "list": entries}},
    )


def _two_crates(tmp_path: Path) -> tuple[Path, Path]:
    """Create two unrelated crates, each with its own Cargo.lock.

    Args:
        tmp_path: Directory to create the crates in.

    Returns:
        The two lockfiles, clean crate first.
    """
    locks = []
    for name in ("clean", "vulnerable"):
        crate = tmp_path / name
        crate.mkdir()
        lock = crate / "Cargo.lock"
        lock.write_text(f'[[package]]\nname = "{name}"\nversion = "0.1.0"\n')
        locks.append(lock)
    return locks[0], locks[1]


def test_check_audits_every_lockfile(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """A file set spanning unrelated crates audits each of their lockfiles.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory holding the crates.
    """
    clean_lock, vulnerable_lock = _two_crates(tmp_path)

    def fake_audit(_cmd: list[str], **kwargs: object) -> SubprocessResult:
        vulnerable = Path(str(kwargs["cwd"])) == vulnerable_lock.parent
        report = _report("RUSTSEC-2021-0003") if vulnerable else _report()
        return _proc(success=not vulnerable, stdout=report)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            side_effect=fake_audit,
        ),
    ):
        result = cargo_audit_plugin.check([str(clean_lock), str(vulnerable_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("advisory_id").is_equal_to(
        ["RUSTSEC-2021-0003"],
    )
    assert_that(result.parse_failures_count).is_equal_to(0)


def test_check_timeout_keeps_earlier_roots_findings(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """A later lockfile timing out keeps what earlier audits found.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory holding the crates.
    """
    first_lock, second_lock = _two_crates(tmp_path)

    def fake_audit(_cmd: list[str], **kwargs: object) -> SubprocessResult:
        if Path(str(kwargs["cwd"])) == second_lock.parent:
            raise TimeoutExpired(cmd=["cargo", "audit"], timeout=120)
        return _proc(success=False, stdout=_report("RUSTSEC-2021-0003"))

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            cargo_audit_plugin,
            "_run_subprocess_result",
            side_effect=fake_audit,
        ),
    ):
        result = cargo_audit_plugin.check([str(first_lock), str(second_lock)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).extracting("file").is_equal_to(
        [str(first_lock.resolve())],
    )
    assert_that(result.output).contains(str(second_lock))


def _run_check(
    plugin: CargoAuditPlugin,
    paths: list[str],
    fake_audit: object,
    options: dict[str, object] | None = None,
) -> ToolResult:
    """Run ``check`` with the version gate bypassed and cargo-audit faked.

    Args:
        plugin: The plugin instance.
        paths: Paths to check.
        fake_audit: Replacement for ``_run_subprocess_result``.
        options: Runtime options for the check.

    Returns:
        The plugin's ToolResult.
    """
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(plugin, "_run_subprocess_result", side_effect=fake_audit),
    ):
        return plugin.check(paths, options or {})


def test_same_advisory_in_two_lockfiles_stays_two_findings(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """Each finding names its own lockfile, so neither rows nor fingerprints merge.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory holding the crates.
    """
    first_lock, second_lock = _two_crates(tmp_path)
    result = _run_check(
        cargo_audit_plugin,
        [str(first_lock), str(second_lock)],
        lambda _cmd, **_kw: _proc(success=False, stdout=_report("RUSTSEC-2021-0003")),
    )

    assert_that(result.issues).extracting("file").is_equal_to(
        [str(first_lock.resolve()), str(second_lock.resolve())],
    )
    keys = {
        entry.key
        for issue in result.issues
        if (entry := fingerprint(issue, tool=result.name, root=tmp_path)) is not None
    }
    assert_that(keys).is_length(2)


def test_timeout_is_one_budget_for_every_lockfile(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
) -> None:
    """A budget spent on the first lockfile leaves the next one unaudited.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory holding the crates.
    """
    first_lock, second_lock = _two_crates(tmp_path)
    clock = [1000.0]
    budgets: list[float] = []

    def slow_audit(_cmd: list[str], **kwargs: object) -> SubprocessResult:
        budgets.append(float(str(kwargs["timeout"])))
        clock[0] += 30
        return _proc(success=False, stdout=_report("RUSTSEC-2021-0003"))

    with patch(
        "lintro.tools.cargo_audit.definition.time.monotonic",
        side_effect=lambda: clock[0],
    ):
        result = _run_check(
            cargo_audit_plugin,
            [str(first_lock), str(second_lock)],
            slow_audit,
            {"timeout": 30},
        )

    assert_that(budgets).is_equal_to([30.0])
    assert_that(result.timed_out).is_true()
    assert_that(result.issues).extracting("file").is_equal_to(
        [str(first_lock.resolve())],
    )
    assert_that(result.output).contains(str(second_lock))


@pytest.mark.parametrize(
    ("first_report", "second_fetches"),
    [(_report(), False), ("not json", True)],
    ids=["first-report-parsed", "first-report-missing"],
)
def test_advisory_database_is_fetched_once(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
    first_report: str,
    second_fetches: bool,
) -> None:
    """Later lockfiles reuse the database once a report proves it is in place.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary directory holding the crates.
        first_report: What cargo-audit prints for the first lockfile.
        second_fetches: Whether the second run may still fetch.
    """
    first_lock, second_lock = _two_crates(tmp_path)
    commands: list[list[str]] = []

    def fake_audit(cmd: list[str], **_kw: object) -> SubprocessResult:
        commands.append(list(cmd))
        report = first_report if len(commands) == 1 else _report()
        return _proc(success=True, stdout=report)

    _run_check(cargo_audit_plugin, [str(first_lock), str(second_lock)], fake_audit)

    assert_that(commands[0]).does_not_contain("--no-fetch")
    assert_that("--no-fetch" not in commands[1]).is_equal_to(second_fetches)


def test_vendored_and_build_lockfiles_are_not_audited(
    cargo_audit_plugin: CargoAuditPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lockfiles under vendor/ or target/ belong to third-party copies.

    Args:
        cargo_audit_plugin: The plugin instance.
        tmp_path: Temporary project root.
        monkeypatch: Pytest fixture used to run from the project root.
    """
    for crate in ("app", "vendor/dep", "target/package/app-0.1.0"):
        (tmp_path / crate).mkdir(parents=True)
        (tmp_path / crate / "Cargo.lock").write_text("version = 3\n")
    monkeypatch.chdir(tmp_path)
    audited: list[Path] = []

    def fake_audit(_cmd: list[str], **kwargs: object) -> SubprocessResult:
        audited.append(Path(str(kwargs["cwd"])))
        return _proc(success=True, stdout=_report())

    result = _run_check(cargo_audit_plugin, [str(tmp_path)], fake_audit)

    assert_that(audited).is_equal_to([(tmp_path / "app").resolve()])
    assert_that(result.success).is_true()
