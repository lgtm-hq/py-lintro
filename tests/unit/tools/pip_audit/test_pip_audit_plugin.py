"""Unit tests for the pip-audit plugin.

The plugin invokes the real ``pip-audit`` binary, which queries live
vulnerability databases. All tests here mock ``_run_subprocess_result`` so the
behavior is deterministic and network-independent.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.parsers.pip_audit.pip_audit_issue import PipAuditIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.pip_audit import (
    PIP_AUDIT_DEFAULT_TIMEOUT,
    PipAuditPlugin,
    _build_targets,
    _target_source,
)

_CLEAN_OUTPUT = json.dumps({"dependencies": [], "fixes": []})
_VULN_OUTPUT = json.dumps(
    {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.4.1",
                "vulns": [
                    {
                        "id": "PYSEC-2019-217",
                        "fix_versions": ["2.11.3"],
                        "aliases": ["CVE-2019-10906"],
                        "description": "Sandbox escape.",
                    },
                ],
            },
        ],
        "fixes": [],
    },
)


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
def pip_audit_plugin() -> PipAuditPlugin:
    """Provide a PipAuditPlugin instance for testing.

    Returns:
        A PipAuditPlugin instance.
    """
    return PipAuditPlugin()


def test_definition_name(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify the tool name.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    assert_that(pip_audit_plugin.definition.name).is_equal_to("pip_audit")


def test_definition_can_fix(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify the tool cannot fix issues.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    assert_that(pip_audit_plugin.definition.can_fix).is_false()


def test_definition_tool_type(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify the tool type is SECURITY.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    assert_that(pip_audit_plugin.definition.tool_type).is_equal_to(ToolType.SECURITY)


def test_definition_file_patterns(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify the file patterns cover requirements and project manifests.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    patterns = pip_audit_plugin.definition.file_patterns
    assert_that(patterns).contains("requirements*.txt", "pyproject.toml", "setup.py")


def test_definition_timeout(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify the default timeout.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    assert_that(pip_audit_plugin.definition.default_timeout).is_equal_to(
        PIP_AUDIT_DEFAULT_TIMEOUT,
    )


def test_doc_url(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify doc_url resolves to an OSV advisory page.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    url = pip_audit_plugin.doc_url("PYSEC-2019-217")
    assert_that(url).contains("osv.dev").contains("PYSEC-2019-217")


def test_doc_url_empty_code(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify doc_url returns None for an empty code.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    assert_that(pip_audit_plugin.doc_url("")).is_none()


def test_set_options_timeout(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify timeout option can be set.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    pip_audit_plugin.set_options(timeout=180)
    assert_that(pip_audit_plugin.options.get("timeout")).is_equal_to(180)


def test_set_options_negative_timeout(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify a negative timeout raises ValueError.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    with pytest.raises(ValueError, match="non-negative"):
        pip_audit_plugin.set_options(timeout=-1)


def test_set_options_non_numeric_timeout(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify a non-numeric timeout raises ValueError.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    with pytest.raises(ValueError, match="must be a number"):
        pip_audit_plugin.set_options(timeout="invalid")


def test_fix_raises_not_implemented(pip_audit_plugin: PipAuditPlugin) -> None:
    """Verify fix raises NotImplementedError.

    Args:
        pip_audit_plugin: The plugin instance.
    """
    with pytest.raises(NotImplementedError) as exc_info:
        pip_audit_plugin.fix(["requirements.txt"], {})
    assert_that(str(exc_info.value)).contains("cannot automatically fix")


def test_build_targets_groups_requirements_and_projects(tmp_path: Path) -> None:
    """Requirements files map to ``-r`` and project files to their directory."""
    req = tmp_path / "requirements.txt"
    req.write_text("jinja2==2.4.1\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname='x'\n")
    setup = tmp_path / "setup.py"
    setup.write_text("from setuptools import setup\n")

    targets = _build_targets([str(req), str(pyproject), str(setup)])

    # One requirements target plus a single de-duplicated project directory.
    assert_that(targets).contains(["-r", str(req)])
    project_targets = [t for t in targets if t[:1] != ["-r"]]
    assert_that(project_targets).is_length(1)
    assert_that(_target_source(project_targets[0])).is_equal_to(str(tmp_path))


def test_check_no_vulnerabilities(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no vulnerabilities are found.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("packaging==25.0\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=True, stdout=_CLEAN_OUTPUT),
        ):
            result = pip_audit_plugin.check([str(req)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_vulnerabilities(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when vulnerabilities are found.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("jinja2==2.4.1\n")

    # pip-audit exits non-zero when it finds vulnerabilities.
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=False, stdout=_VULN_OUTPUT),
        ):
            result = pip_audit_plugin.check([str(req)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    assert result.issues is not None
    first = result.issues[0]
    assert isinstance(first, PipAuditIssue)
    assert_that(first.vuln_id).is_equal_to("PYSEC-2019-217")


def test_check_unparseable_output_fails_closed(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """Non-empty, unparseable stdout is treated as a failed (non-clean) scan.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("jinja2==2.4.1\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=False, stdout="fatal: boom"),
        ):
            result = pip_audit_plugin.check([str(req)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_timeout(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """Check handles a subprocess timeout gracefully.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("jinja2==2.4.1\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            side_effect=TimeoutExpired(cmd=["pip-audit"], timeout=120),
        ):
            result = pip_audit_plugin.check([str(req)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_empty_stdout_on_success_fails_closed(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """Exit 0 with no JSON report is a failure, not a clean pass.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    req = tmp_path / "requirements.txt"
    req.write_text("packaging==25.0\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            return_value=_proc(success=True, stdout=""),
        ):
            result = pip_audit_plugin.check([str(req)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_runs_from_requirements_directory(
    pip_audit_plugin: PipAuditPlugin,
    tmp_path: Path,
) -> None:
    """The audit runs with cwd at the requirements file's directory.

    Args:
        pip_audit_plugin: The plugin instance.
        tmp_path: Temporary directory path.
    """
    sub = tmp_path / "svc"
    sub.mkdir()
    req = sub / "requirements.txt"
    req.write_text("packaging==25.0\n")

    seen_cwd: list[str | None] = []

    def _capture(cmd: list[str], **kwargs: object) -> SubprocessResult:
        seen_cwd.append(kwargs.get("cwd"))  # type: ignore[arg-type]
        return _proc(success=True, stdout=_CLEAN_OUTPUT)

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            pip_audit_plugin,
            "_run_subprocess_result",
            side_effect=_capture,
        ):
            pip_audit_plugin.check([str(req)], {})

    assert_that(seen_cwd).is_length(1)
    assert_that(str(seen_cwd[0])).is_equal_to(str(sub.resolve()))
