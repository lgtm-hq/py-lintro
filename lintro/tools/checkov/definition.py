"""Checkov tool definition.

Checkov is a static analysis tool for Infrastructure-as-Code (IaC) that detects
security and compliance misconfigurations. Lintro scopes it to Terraform
sources and runs it hermetically: no policy download, no external module fetch,
and no result upload to any platform.

Checkov emits both ``--output json`` and ``--output sarif``. The native JSON is
parsed here because SARIF is lossy for checkov: it hard-codes ``level:
"error"`` for every result (checkov's own severity is ``null`` without a
platform API key), drops the resource address from ``results[]``, and carries
no ``helpUri``. See ``docs/tool-analysis/checkov-analysis.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro._tool_versions import get_min_version
from lintro.enums.capability import Cap
from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.checkov.checkov_parser import parse_checkov_output
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.core.batch_runner import (
    BatchCheckPolicy,
    BatchOutput,
    BatchSuccess,
    run_batch_check,
)
from lintro.tools.core.option_validators import (
    filter_none_options,
    normalize_str_or_list,
    validate_bool,
)

# Checkov builds a resource graph before evaluating policies, which is slower
# than a line-oriented linter on the same file count.
CHECKOV_DEFAULT_TIMEOUT: int = 120
# Terraform only. Dockerfiles are left to hadolint (a shared claim would
# double-report the same file under two rule sets), and broad ``*.yaml`` /
# ``*.json`` globs would feed every ``package.json`` and CI config to checkov.
# CloudFormation and Kubernetes manifests need content-based detection before
# they can be claimed without that noise; both are tracked for a follow-up.
CHECKOV_FILE_PATTERNS: list[str] = ["*.tf", "*.tf.json"]
#: Frameworks checkov is pinned to, one per claimed file pattern: ``terraform``
#: parses HCL (``*.tf``) and ``terraform_json`` Terraform's JSON syntax
#: (``*.tf.json``). Keep this in lockstep with ``CHECKOV_FILE_PATTERNS`` — a
#: pattern whose framework is missing is discovered, handed to checkov and
#: silently evaluated against no policies at all.
CHECKOV_FRAMEWORKS: str = "terraform,terraform_json"


@register_tool
@dataclass
class CheckovPlugin(BaseToolPlugin):
    """Checkov Infrastructure-as-Code security scanner plugin.

    Detects security and compliance misconfigurations in Terraform sources.
    Checkov reports misconfigurations without rewriting files, so ``fix()`` is
    unsupported.
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition containing tool metadata.
        """
        return ToolDefinition(
            name="checkov",
            description=(
                "Infrastructure-as-Code security scanner for Terraform "
                "misconfigurations"
            ),
            can_fix=False,
            tool_type=ToolType.SECURITY | ToolType.INFRASTRUCTURE,
            file_patterns=CHECKOV_FILE_PATTERNS,
            claims=[
                Claim(
                    patterns=CHECKOV_FILE_PATTERNS,
                    capabilities={Cap.CHECK},
                ),
            ],
            reads_tree=True,
            # Checkov's graph checks (the ``CKV2_*`` family) evaluate relations
            # between resources, so a resource defined in one file and
            # referenced from another must be visible in the same invocation.
            # Sharding the file list would silently drop those findings.
            partitionable=False,
            native_configs=[".checkov.yaml", ".checkov.yml"],
            version_command=["checkov", "--version"],
            min_version=get_min_version(ToolName.CHECKOV),
            default_options={
                "timeout": CHECKOV_DEFAULT_TIMEOUT,
                "checks": None,
                "skip_checks": None,
                "compact": True,
            },
            default_timeout=CHECKOV_DEFAULT_TIMEOUT,
        )

    def set_options(
        self,
        checks: str | list[str] | None = None,
        skip_checks: str | list[str] | None = None,
        compact: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Set Checkov-specific options.

        Args:
            checks: Run only these check IDs, either as a list or a single id.
                The CLI splits ``--tool-options`` on commas, so several ids can
                only arrive as a pipe-delimited list (``checks=CKV_AWS_23|…``).
            skip_checks: Check IDs to skip, in the same two forms.
            compact: Omit the offending code block from checkov's JSON, which
                keeps the payload small. Lintro reports file and line range
                either way.
            **kwargs: Other tool options.
        """
        checks = normalize_str_or_list(checks, "checks")
        skip_checks = normalize_str_or_list(skip_checks, "skip_checks")
        validate_bool(compact, "compact")

        options = filter_none_options(
            checks=checks,
            skip_checks=skip_checks,
            compact=compact,
        )
        super().set_options(**options, **kwargs)

    def _build_command(self, files: list[str]) -> list[str]:
        """Build the checkov check command.

        Args:
            files: List of Terraform files to check.

        Returns:
            List of command arguments.
        """
        cmd: list[str] = self._get_executable_command("checkov")
        cmd.extend(["--output", "json"])
        # Checkov defaults to every framework it supports, which means the
        # secrets framework also runs over the ``.tf`` files handed to it and
        # emits CKV_SECRET_* findings. Secrets are gitleaks' and trufflehog's
        # surface in lintro, so pinning the frameworks keeps this tool to the
        # IaC policy findings it claims and avoids double-reporting.
        #
        # Both Terraform frameworks are named: checkov parses HCL under
        # ``terraform`` and Terraform's JSON syntax under ``terraform_json``,
        # and a pin naming only the former evaluates *zero* policies against a
        # ``.tf.json`` file while still exiting 0 — a clean scan for a file
        # nothing ran on, which is exactly the fail-open this plugin refuses
        # elsewhere.
        cmd.extend(["--framework", CHECKOV_FRAMEWORKS])
        # Hermetic by construction: never fetch policies from the registry and
        # never fetch remote Terraform modules...
        cmd.append("--skip-download")
        cmd.extend(["--download-external-modules", "False"])
        # ...and never upload results. No --bc-api-key is ever passed, but
        # checkov also reads BC_API_KEY from the environment, so the absence of
        # the flag is not by itself a guarantee. This one makes it a guarantee
        # whatever the operator's shell exports.
        cmd.append("--skip-results-upload")

        if self.options.get("compact", True):
            cmd.append("--compact")

        checks = self.options.get("checks")
        if isinstance(checks, list) and checks:
            cmd.extend(["--check", ",".join(str(check) for check in checks)])

        skip_checks = self.options.get("skip_checks")
        if isinstance(skip_checks, list) and skip_checks:
            cmd.extend(["--skip-check", ",".join(str(c) for c in skip_checks)])

        # ``-f`` attaches exactly one path per flag; several paths after a
        # single ``-f`` leave all but the first unscanned. Repeat the flag.
        for file_path in files:
            cmd.extend(["-f", file_path])
        return cmd

    def doc_url(self, code: str) -> str | None:
        """Return a documentation URL for the given check code.

        Checkov's per-check ``guideline`` URL comes from platform metadata that
        ``--skip-download`` suppresses on every lintro run, so this static
        fallback to checkov's policy index is what findings actually carry. The
        parser still prefers a native guideline if one is ever present.

        Args:
            code: Checkov check ID (e.g., ``CKV_AWS_23``).

        Returns:
            URL to Checkov's policy index, or None if code is empty.
        """
        if code:
            return DocUrlTemplate.CHECKOV
        return None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check Terraform files with Checkov for misconfigurations.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        ctx = self.prepare(paths, options, no_files_message="No files to check.")
        if isinstance(ctx, ToolResult):
            return ctx

        cmd = self._build_command(files=ctx.files)
        logger.debug(f"[CheckovPlugin] Running: {' '.join(cmd[:8])}...")

        # ``--output json`` always produces a report for a run that reached the
        # policy engine, so output with no JSON in it means checkov never got
        # that far. Recorded here rather than inferred from the result, because
        # an unparseable report and a genuinely clean one both parse to zero
        # issues and the policy below cannot tell them apart.
        unparseable: list[str] = []

        def _parse(output: str) -> list[Any]:
            """Extract and parse checkov's JSON report from combined output.

            Args:
                output: Combined stdout and stderr from checkov.

            Returns:
                Parsed failed checks; empty when there was no JSON report.
            """
            payload = extract_checkov_json(output)
            if payload is None:
                unparseable.append(output)
                return []
            issues = parse_checkov_output(payload)
            for issue in issues:
                # ``or`` and not an unconditional assignment: a report that
                # carried a native ``guideline`` already propagated it into
                # ``doc_url``, and that link is more specific than the static
                # policy index this falls back to.
                issue.doc_url = issue.doc_url or self.doc_url(issue.check_id) or ""
            return issues

        # Checkov exits 0 on a clean report and 1 when any check failed, so
        # exit status and findings must both be clean. A non-zero exit with
        # nothing parseable is a runtime error (bad argument, unparseable HCL),
        # and surfacing the raw output there fails closed instead of reporting
        # a silent security pass.
        result = run_batch_check(
            ctx,
            plugin=self,
            cmd=cmd,
            parse=_parse,
            policy=BatchCheckPolicy(
                success=BatchSuccess.EXIT_AND_ISSUES,
                output=BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES,
            ),
            on_error=lambda exc: ToolResult(
                name=self.definition.name,
                success=False,
                output=f"Checkov failed: {exc}",
                issues_count=0,
            ),
        )
        if unparseable and result.success:
            # Exit zero with no report is the fail-open case: a soft-fail
            # config, or checkov dying before it wrote one. Reporting a pass
            # there would claim the files were scanned when they were not.
            return ToolResult(
                name=self.definition.name,
                success=False,
                output=(
                    unparseable[0].strip()
                    or "Checkov produced no JSON report; the scan did not run."
                ),
                issues_count=0,
                parse_failures_count=1,
            )
        return result

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Checkov cannot fix issues, only report them.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options.

        Returns:
            ToolResult: Never returns, always raises NotImplementedError.

        Raises:
            NotImplementedError: Checkov does not support fixing issues.
        """
        raise NotImplementedError(
            "Checkov cannot automatically fix issues. Run 'lintro check' to see "
            "issues.",
        )


def extract_checkov_json(output: str) -> str | None:
    """Return the JSON document embedded in checkov's combined output.

    The batch runner hands the parser stdout and stderr merged. Checkov writes
    its JSON report to stdout but logs to stderr — unresolvable Terraform
    variables print as ``${var}`` and log lines are commonly bracketed
    (``[WARNING] …``), so the payload can be surrounded by, and interleaved
    with, text that carries its own braces.

    Slicing from the first opener to the last closer is therefore wrong: a
    single bracketed log line after the report extends the slice past the end
    of the JSON. This walks every candidate opener instead and returns the
    first one that decodes as a complete JSON value.

    Args:
        output: Combined stdout and stderr from checkov.

    Returns:
        The JSON substring, or None when the output carries no JSON document.
    """
    if not output or not output.strip():
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(output, index)
        except (json.JSONDecodeError, ValueError):
            continue
        return output[index:end]
    logger.warning("checkov output did not contain a parseable JSON report")
    return None
