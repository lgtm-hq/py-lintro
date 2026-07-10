"""Workflow script-reference matching for review chunking.

Determines whether a changed script or local-action file is invoked by a
changed CI workflow. Parses ``run:`` shell commands (interpreters, ``uv run``
wrappers, ``env``/``exec`` prefixes, multi-line and folded blocks) and resolves
local action roots so ``uses:`` references match changed implementation files.

Shell tokenizing and stripping live in :mod:`shell_run_parse`; local action
path resolution lives in :mod:`github_action_paths`. This module owns the YAML
workflow structure: locating ``run:``/``uses:`` steps and scanning literal and
folded multi-line ``run:`` blocks.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lintro.ai.review.chunker.github_action_paths import (
    _ACTION_MANIFEST_NAMES,
    _NON_EXECUTABLE_WORKFLOW_SUFFIXES,
    _github_action_directory,
    _github_action_reference_paths,
)
from lintro.ai.review.chunker.shell_run_parse import (
    _GITHUB_WORKSPACE_PREFIX,
    _RUN_COMMAND_PREFIX,
    _bash_stdin_invocation,
    _interpreter_command_string_invocation,
    _is_comment_line,
    _line_references_path,
    _segment_executes_reference_path,
    _shell_command_string_payload_after_wrappers,
    _shell_cwd_after_line,
    _strip_run_command_prefix,
)
from lintro.ai.review.path_utils import is_test_path

_YAML_STEP_PREFIX = r"^[ \t]*(?:-\s+)?"
_RUN_STEP = rf"{_YAML_STEP_PREFIX}run:"
_USES_STEP = rf"{_YAML_STEP_PREFIX}uses:\s*"
_QUOTED_RUN_PATH = r'["\'](?:\./)?'


def _is_workflow_linked_script(*, path: str) -> bool:
    """Return True when a path may be invoked from a changed workflow."""
    if is_test_path(path):
        return False
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    if suffix in {".sh", ".bash"}:
        return True
    if path.startswith(".github/actions/") and len(pure_path.parts) >= 3:
        if pure_path.name in _ACTION_MANIFEST_NAMES:
            return True
        if suffix in _NON_EXECUTABLE_WORKFLOW_SUFFIXES and pure_path.name not in {
            "action.yml",
            "action.yaml",
        }:
            return False
        return pure_path.name in {
            "action.yml",
            "action.yaml",
            "Dockerfile",
            "run",
            "entrypoint",
        } or bool(suffix)
    return path.startswith(("scripts/", "bin/")) and (
        not suffix or suffix not in _NON_EXECUTABLE_WORKFLOW_SUFFIXES
    )


def _workflow_text_for_matching(
    *,
    workflow_path: str,
    workflow_diff: str,
    post_image_files: dict[str, str],
) -> str:
    """Return full post-change workflow text when available, else diff hunks."""
    if workflow_path in post_image_files:
        return post_image_files[workflow_path]
    return _workflow_post_image_text(workflow_diff=workflow_diff)


def _single_run_command(*, line: str) -> re.Match[str] | None:
    """Return a match when a line contains a GitHub Actions ``run:`` step."""
    return re.search(rf"{_RUN_STEP}(?!\s*[|>])(?:\s+(.+))?$", line)


def _single_run_command_text(*, match: re.Match[str]) -> str | None:
    """Return the shell command text from a ``run:`` step match."""
    command = match.group(1)
    if command is None:
        return None
    return command.strip()


def _run_reference_patterns(*, path: str) -> tuple[re.Pattern[str], ...]:
    """Build ``run:`` matchers for supported repo-root script reference forms."""
    escaped = re.escape(path)
    boundary = r"(?:$|[^\w./-])"
    direct = (
        rf"{_RUN_STEP}\s*{escaped}{boundary}",
        rf"{_RUN_STEP}\s*\./{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_GITHUB_WORKSPACE_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_QUOTED_RUN_PATH}{escaped}['\"]{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}\./{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{_GITHUB_WORKSPACE_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{_QUOTED_RUN_PATH}{escaped}['\"]{boundary}",
    )
    return tuple(re.compile(pattern) for pattern in direct)


def _run_line_reference_surface(*, line: str) -> str:
    """Return a run line safe for regex matching (hide inline-code payload text)."""
    single_run = _single_run_command(line=line)
    if single_run is None:
        return line
    command = _single_run_command_text(match=single_run)
    if command is None:
        return line
    if _shell_command_string_payload_after_wrappers(segment=command) is not None:
        return line[: single_run.start(1)]
    if _interpreter_command_string_invocation(segment=command):
        return line[: single_run.start(1)]
    return line


def _multiline_run_block_style(*, line: str) -> str | None:
    """Return ``literal`` or ``folded`` when a line opens a multiline ``run:`` block."""
    if _is_comment_line(line=line):
        return None
    if re.search(rf"{_YAML_STEP_PREFIX}run:\s*\|", line):
        return "literal"
    if re.search(rf"{_YAML_STEP_PREFIX}run:\s*>", line):
        return "folded"
    return None


def _line_starts_multiline_run_block(*, line: str) -> bool:
    """Return True when an uncommented line opens a multiline ``run:`` block."""
    return _multiline_run_block_style(line=line) is not None


def _runtime_command_executes_script(*, command: str) -> bool:
    """Return False when a runtime flag terminates before executing a script.

    Command-string/eval invocations (``python -c``, ``node -e``) do execute code,
    so they are reported as executing here; per-segment matching still excludes the
    inline-code operand from being grouped as a referenced script.
    """
    if _interpreter_command_string_invocation(segment=command.strip()):
        return True
    remaining = _strip_run_command_prefix(segment=command.strip())
    return bool(remaining.strip())


def _script_referenced_in_workflow(*, script_path: str, workflow_text: str) -> bool:
    """Return True when a workflow run/uses step references a script path."""
    action_dir = _github_action_directory(path=script_path)
    reference_paths = [script_path]
    if action_dir is not None:
        reference_paths.extend(_github_action_reference_paths(path=script_path))

    patterns: list[re.Pattern[str]] = []
    for path in reference_paths:
        patterns.extend(_run_reference_patterns(path=path))
        patterns.append(
            re.compile(
                rf"{_USES_STEP}\.?/?{re.escape(path)}(?:$|[^\w./-])",
            ),
        )
    for content in workflow_text.splitlines():
        if _is_comment_line(line=content):
            continue
        single_run = _single_run_command(line=content)
        command = (
            _single_run_command_text(match=single_run)
            if single_run is not None
            else None
        )
        if command is not None and _bash_stdin_invocation(segment=command):
            continue
        if command is not None and not _runtime_command_executes_script(
            command=command,
        ):
            continue
        if any(
            pattern.search(_run_line_reference_surface(line=content))
            for pattern in patterns
        ):
            return True
        if command is not None and any(
            _segment_executes_reference_path(
                segment=command,
                path=reference_path,
                cwd="",
            )
            or _line_references_path(line=command, path=reference_path, cwd="")
            for reference_path in reference_paths
        ):
            return True
    return _script_referenced_in_multiline_run_blocks(
        reference_paths=reference_paths,
        workflow_text=workflow_text,
    )


def _script_referenced_in_multiline_run_blocks(
    *,
    reference_paths: list[str],
    workflow_text: str,
) -> bool:
    """Match script paths inside multi-line ``run: |`` / ``run: >`` shell blocks."""
    run_indent: int | None = None
    block_style: str | None = None
    folded_lines: list[str] = []
    shell_cwd = ""

    def _folded_block_references_script() -> bool:
        if not folded_lines:
            return False
        joined = " ".join(part for part in folded_lines if part)
        return any(
            _line_references_path(line=joined, path=path, cwd=shell_cwd)
            for path in reference_paths
        )

    for line in workflow_text.splitlines():
        opener = _multiline_run_block_style(line=line)
        if opener is not None:
            if block_style == "folded" and _folded_block_references_script():
                return True
            run_indent = len(line) - len(line.lstrip(" "))
            block_style = opener
            folded_lines = []
            shell_cwd = ""
            continue
        if run_indent is None:
            continue
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent <= run_indent:
            if block_style == "folded" and _folded_block_references_script():
                return True
            opener = _multiline_run_block_style(line=line)
            if opener is not None:
                run_indent = line_indent
                block_style = opener
                folded_lines = []
                shell_cwd = ""
                continue
            run_indent = None
            block_style = None
            folded_lines = []
            shell_cwd = ""
            continue
        if _is_comment_line(line=line):
            continue
        if block_style == "folded":
            folded_lines.append(line.strip())
            continue
        for path in reference_paths:
            if _line_references_path(line=line, path=path, cwd=shell_cwd):
                return True
        shell_cwd = _shell_cwd_after_line(line=line, cwd=shell_cwd)
    return block_style == "folded" and _folded_block_references_script()


def _workflow_post_image_text(*, workflow_diff: str) -> str:
    """Return the post-change workflow text represented by diff hunks."""
    lines: list[str] = []
    for line in workflow_diff.splitlines():
        if line.startswith(("+++", "---", "diff --git", "index ", "@@")):
            continue
        if line.startswith(("+", " ")):
            lines.append(line[1:])
    return "\n".join(lines)
