"""Workflow script-reference matching for review chunking.

Determines whether a changed script or local-action file is invoked by a
changed CI workflow. Parses ``run:`` shell commands (interpreters, ``uv run``
wrappers, ``env``/``exec`` prefixes, multi-line and folded blocks) and resolves
local action roots so ``uses:`` references match changed implementation files.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lintro.ai.review.path_utils import is_test_path

_GITHUB_WORKSPACE_PREFIX = r"\$\{\{\s*github\.workspace\s*\}\}/"
_SHELL_FLAG = r"(?:\s+-[\w-]+(?:=\S+)?)*"
_WORKFLOW_SCRIPT_RUNTIMES = r"(?:bash|sh|python3?|node|bun)"
_RUN_INTERPRETER = rf"{_WORKFLOW_SCRIPT_RUNTIMES}{_SHELL_FLAG}\s+"
_UV_ARG_OPTIONS = frozenset({"with", "directory", "project", "package", "python"})
_UV_SHORT_OPERAND_OPTIONS = frozenset({"p", "w"})
_BUN_SUBCOMMANDS = frozenset({"run"})
_NODE_LONG_OPERAND_OPTIONS = frozenset({"import", "loader", "require"})
_BUN_LONG_OPERAND_OPTIONS = frozenset({"env-file", "preload", "require"})
_NODE_SHORT_OPERAND_OPTIONS = frozenset({"r"})
_RUNTIME_TERMINATING_LONG_OPTIONS = frozenset({"help", "version"})
_RUNTIME_TERMINATING_SHORT_OPTIONS = frozenset({"h", "v"})
_ENV_OPERAND_FLAGS = frozenset({"-u", "-U", "-C", "-S"})
_ENV_OPERAND_LONG_OPTIONS = frozenset({"chdir", "split-string"})
_SHELL_DISPATCH_WRAPPERS = frozenset({"exec", "command"})
_SHELL_COMPOUND_LEADERS = frozenset({"then", "do", "else", "elif"})
_UV_OPTION = (
    r"(?:"
    r"\s+--(?:with|directory|project|package)\s+\S+"
    r"|\s+--[\w-]+(?:=(?:[^\s\"']+|\"[^\"]*\"|'[^']*'))?"
    r")*"
)
_UV_RUN_WRAPPER = (
    rf"uv\s+run{_UV_OPTION}(?:\s+{_WORKFLOW_SCRIPT_RUNTIMES}){_SHELL_FLAG}\s+"
)
_RUN_COMMAND_PREFIX = rf"(?:{_RUN_INTERPRETER}|{_UV_RUN_WRAPPER}|uv\s+run\s+)"
_YAML_STEP_PREFIX = r"^[ \t]*(?:-\s+)?"
_RUN_STEP = rf"{_YAML_STEP_PREFIX}run:"
_USES_STEP = rf"{_YAML_STEP_PREFIX}uses:\s*"
_FAIL_FAST_OR_TAIL = re.compile(
    r"(?i)^(?:exit(?:\s+\d+)?|false|return(?:\s+\d+)?|:)\b",
)
_QUOTED_RUN_PATH = r'["\'](?:\./)?'
_NON_EXECUTION_COMMAND = re.compile(
    r"(?i)^(?:grep|cat|head|tail|less|more|sed|awk|sort|uniq|wc|find|ls|stat|"
    r"read|diff|cmp|strings|od|xxd|echo|printf|chmod|chown|chgrp|cp|mv|install|"
    r"ln|rsync|scp|touch|rm)\b",
)
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NON_EXECUTABLE_WORKFLOW_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".rst", ".toml", ".txt", ".yaml", ".yml"},
)
_ACTION_BUILD_ARTIFACT_DIRS = frozenset(
    {"coverage", "dist", "lib", "node_modules", "out", "vendor"},
)
_ACTION_SOURCE_LAYOUT_DIRS = frozenset({"source", "src"})


def _is_workflow_linked_script(*, path: str) -> bool:
    """Return True when a path may be invoked from a changed workflow."""
    if is_test_path(path):
        return False
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    if suffix in {".sh", ".bash"}:
        return True
    if path.startswith(".github/actions/") and len(pure_path.parts) >= 3:
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


def _is_comment_line(*, line: str) -> bool:
    """Return True when a workflow or shell line is comment-only."""
    return line.lstrip().startswith("#")


def _normalize_posix_shell_path(*, path: str) -> str:
    """Normalize a repository-relative POSIX path."""
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized == ".":
        return ""
    parts: list[str] = []
    for part in PurePosixPath(normalized).parts:
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _shell_paths_equal(*, left: str, right: str) -> bool:
    """Return True when two repository-relative paths refer to the same file."""
    return _normalize_posix_shell_path(path=left) == _normalize_posix_shell_path(
        path=right,
    )


def _resolve_shell_path(*, token: str, cwd: str) -> str:
    """Resolve a shell token against the current working directory."""
    cleaned = token.strip().strip("\"'")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    base = _normalize_posix_shell_path(path=cwd)
    if base:
        return _normalize_posix_shell_path(path=f"{base}/{cleaned}")
    return _normalize_posix_shell_path(path=cleaned)


def _rest_after_first_shell_token(*, segment: str) -> str:
    """Return the remainder of a shell segment after its first token."""
    token_match = re.match(r'\s*"((?:\\.|[^"\\])*)"|\'([^\']*)\'|(\S+)', segment)
    if token_match is None:
        return segment
    return segment[token_match.end() :].lstrip()


def _strip_leading_shell_prefixes(*, segment: str) -> str:
    """Skip leading VAR=value assignments and ``env(1)`` wrappers."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None:
            break
        if _ASSIGNMENT_PREFIX.match(first_token):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        if first_token.lower() == "env":
            remaining = _rest_after_first_shell_token(segment=remaining)
            while True:
                token = _first_shell_token(segment=remaining)
                if token is None:
                    break
                if _ASSIGNMENT_PREFIX.match(token):
                    remaining = _rest_after_first_shell_token(segment=remaining)
                    continue
                if token.startswith("-"):
                    remaining = _rest_after_first_shell_token(segment=remaining)
                    if token in _ENV_OPERAND_FLAGS:
                        operand = _first_shell_token(segment=remaining)
                        if operand is not None and not operand.startswith("-"):
                            remaining = _rest_after_first_shell_token(segment=remaining)
                    elif token.startswith("--"):
                        option_name = token[2:].split("=", 1)[0]
                        if (
                            option_name in _ENV_OPERAND_LONG_OPTIONS
                            and "=" not in token
                        ):
                            operand = _first_shell_token(segment=remaining)
                            if operand is not None and not operand.startswith("-"):
                                remaining = _rest_after_first_shell_token(
                                    segment=remaining,
                                )
                    continue
                break
            continue
        break
    return remaining


def _strip_uv_cli_options(*, segment: str) -> str:
    """Skip ``uv run`` CLI options that precede the invoked command."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if token.startswith("--"):
            option_name = token[2:].split("=", 1)[0]
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" not in token and (
                option_name in _UV_ARG_OPTIONS or option_name.startswith("with")
            ):
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        if len(token) >= 2 and token[0] == "-" and token[1] != "-":
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" in token:
                continue
            option_body = token[1:]
            if option_body in _UV_SHORT_OPERAND_OPTIONS:
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
                continue
            if len(option_body) > 1 and option_body[-1] in _UV_SHORT_OPERAND_OPTIONS:
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
                continue
            continue
        break
    return remaining


def _runtime_long_option_needs_operand(*, runtime: str, option_name: str) -> bool:
    """Return True when a Node/Bun long option consumes a separate operand."""
    if runtime == "bun":
        return option_name in _BUN_LONG_OPERAND_OPTIONS
    if runtime.startswith("node"):
        return option_name in _NODE_LONG_OPERAND_OPTIONS
    return False


def _runtime_short_option_needs_operand(*, runtime: str, option_char: str) -> bool:
    """Return True when a Node/Bun short option consumes a separate operand."""
    return bool(runtime.startswith("node") and option_char == "r")


def _runtime_option_is_terminating(*, runtime: str, token: str) -> bool:
    """Return True when a Node/Bun flag ends execution before any script."""
    if not (runtime.startswith("node") or runtime == "bun"):
        return False
    if token.startswith("--"):
        option_name = token[2:].split("=", 1)[0]
        return option_name in _RUNTIME_TERMINATING_LONG_OPTIONS
    return bool(
        len(token) == 2
        and token[0] == "-"
        and token[1] in _RUNTIME_TERMINATING_SHORT_OPTIONS,
    )


def _clustered_runtime_option_needs_next_operand(*, runtime: str, token: str) -> bool:
    """Return True when a clustered short option still needs the next shell token."""
    option_body = token[1:]
    if not option_body or "=" in token:
        return False
    for index, char in enumerate(option_body):
        needs_operand = _runtime_short_option_needs_operand(
            runtime=runtime,
            option_char=char,
        )
        if runtime in {"bash", "sh"} and char == "o":
            needs_operand = True
        if not needs_operand:
            continue
        return index == len(option_body) - 1
    return False


def _strip_shell_interpreter_prefix(*, segment: str) -> str:
    """Skip a leading workflow script runtime and its flags."""
    remaining = segment.strip()
    runtime_match = re.match(
        rf"(?i)^(?P<runtime>{_WORKFLOW_SCRIPT_RUNTIMES})\b",
        remaining,
    )
    if runtime_match is None:
        return remaining
    runtime = runtime_match.group("runtime").lower()
    remaining = remaining[runtime_match.end() :].lstrip()
    if runtime == "bun":
        subcommand = _first_shell_token(segment=remaining)
        if subcommand is not None and subcommand.lower() in _BUN_SUBCOMMANDS:
            remaining = _rest_after_first_shell_token(segment=remaining)
    pending_operand = False
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if pending_operand:
            remaining = _rest_after_first_shell_token(segment=remaining)
            pending_operand = False
            continue
        if _runtime_option_is_terminating(runtime=runtime, token=token):
            return ""
        if re.match(r"(?i)^-c", token) or re.match(r"(?i)^-s", token):
            break
        if not token.startswith("-"):
            break
        if token.startswith("--"):
            option_name = token[2:].split("=", 1)[0]
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" not in token and _runtime_long_option_needs_operand(
                runtime=runtime,
                option_name=option_name,
            ):
                pending_operand = True
            continue
        remaining = _rest_after_first_shell_token(segment=remaining)
        if "=" in token:
            continue
        option_body = token[1:]
        if len(option_body) == 1:
            if (
                _runtime_short_option_needs_operand(
                    runtime=runtime,
                    option_char=option_body,
                )
                or token in {"-W"}
                or re.match(r"(?i)^-o$", token)
            ):
                pending_operand = True
            continue
        if _clustered_runtime_option_needs_next_operand(runtime=runtime, token=token):
            pending_operand = True
            continue
    return remaining


def _strip_command_options(*, segment: str) -> str:
    """Skip ``command`` builtin option flags before the invoked executable."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if len(token) == 2 and token[0] == "-" and token[1] == "-":
            return _rest_after_first_shell_token(segment=remaining)
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        break
    return remaining


def _strip_shell_dispatch_wrappers(*, segment: str) -> str:
    """Skip leading ``exec``/``command`` dispatch wrappers."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None or first_token.lower() not in _SHELL_DISPATCH_WRAPPERS:
            break
        wrapper = first_token.lower()
        remaining = _rest_after_first_shell_token(segment=remaining)
        if wrapper == "command":
            remaining = _strip_command_options(segment=remaining)
    return remaining


def _strip_shell_compound_leaders(*, segment: str) -> str:
    """Skip compound-command leaders such as ``then`` and ``do``."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None or first_token.lower() not in _SHELL_COMPOUND_LEADERS:
            break
        remaining = _rest_after_first_shell_token(segment=remaining)
    return remaining


def _strip_run_command_prefix(*, segment: str) -> str:
    """Skip interpreter and ``uv run`` wrappers before the invoked command."""
    remaining = segment.strip()
    if re.match(r"(?i)^uv\s+run\b", remaining):
        remaining = re.sub(r"(?i)^uv\s+run\b", "", remaining, count=1).lstrip()
        remaining = _strip_uv_cli_options(segment=remaining)
    return _strip_shell_interpreter_prefix(segment=remaining)


def _short_option_cluster_includes(*, token: str, option: str) -> bool:
    """Return True when a clustered bash/sh short-option token includes ``option``."""
    if not token.startswith("-") or token.startswith("--"):
        return False
    if re.fullmatch(rf"(?i)-{re.escape(option)}", token):
        return True
    body = token[1:]
    return bool(body) and body.isalpha() and option.lower() in body.lower()


def _trailing_shell_c_payload(*, segment: str) -> str | None:
    """Return a ``-c`` command-string payload after shell wrappers are stripped."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            return None
        if _short_option_cluster_includes(token=token, option="c"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            return _first_shell_token(segment=remaining)
        if _short_option_cluster_includes(token=token, option="s"):
            return None
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        return None


def _command_string_payload(*, segment: str) -> str | None:
    """Return the shell payload passed to ``bash``/``sh -c``, if present."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return None
    remaining = re.sub(r"(?i)^(?:bash|sh)\b", "", stripped, count=1).lstrip()
    return _trailing_shell_c_payload(segment=remaining)


def _bash_stdin_invocation(*, segment: str) -> bool:
    """Return True when a segment invokes ``bash``/``sh -s`` (stdin script mode)."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return False
    remaining = re.sub(r"(?i)^(?:bash|sh)\b", "", stripped, count=1).lstrip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            return False
        if _short_option_cluster_includes(token=token, option="s"):
            return True
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        return False


def _shell_command_string_payload(*, segment: str) -> str | None:
    """Return ``bash``/``sh`` ``-c`` payloads that may execute a script path."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return None
    return _command_string_payload(segment=stripped)


def _shell_command_string_payload_after_wrappers(*, segment: str) -> str | None:
    """Return ``bash``/``sh -c`` payload after stripping non-executing wrappers."""
    remaining = segment.strip()
    while remaining:
        payload = _shell_command_string_payload(segment=remaining)
        if payload is not None:
            return payload
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        previous = remaining
        remaining = _strip_leading_shell_prefixes(segment=remaining)
        payload = _shell_command_string_payload(segment=remaining)
        if payload is not None:
            return payload
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        remaining = _strip_run_command_prefix(segment=remaining)
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        remaining = _strip_shell_dispatch_wrappers(segment=remaining)
        remaining = _strip_shell_compound_leaders(segment=remaining)
        if remaining == previous:
            break
    return None


def _first_shell_token(*, segment: str) -> str | None:
    """Return the first token from a shell segment."""
    token_match = re.match(r'\s*"((?:\\.|[^"\\])*)"|\'([^\']*)\'|(\S+)', segment)
    if token_match is None:
        return None
    if token_match.group(1) is not None:
        return token_match.group(1)
    if token_match.group(2) is not None:
        return token_match.group(2)
    return token_match.group(3)


def _strip_workspace_prefix(*, token: str) -> str:
    """Remove a leading ``${{ github.workspace }}/`` prefix from a shell token."""
    cleaned = token.strip().strip("\"'")
    match = re.match(rf"(?i){_GITHUB_WORKSPACE_PREFIX}(.*)", cleaned)
    if match is None:
        return cleaned
    return match.group(1)


def _normalize_invoked_command_segment(*, segment: str) -> str:
    """Remove shell wrappers until the invoked command token is exposed."""
    remaining = segment.strip()
    while True:
        if _bash_stdin_invocation(segment=remaining):
            return remaining
        previous = remaining
        remaining = _strip_leading_shell_prefixes(segment=remaining)
        remaining = _strip_run_command_prefix(segment=remaining)
        remaining = _strip_shell_dispatch_wrappers(segment=remaining)
        remaining = _strip_shell_compound_leaders(segment=remaining)
        if remaining == previous:
            break
    return remaining


def _segment_invokes_path_directly(*, segment: str, path: str, cwd: str) -> bool:
    """Return True when ``path`` is the invoked command rather than an argument."""
    remaining = segment.strip()
    workspace_segment = re.match(
        rf"(?i)^{_GITHUB_WORKSPACE_PREFIX}(.+)$",
        remaining,
    )
    if workspace_segment is not None:
        remaining = workspace_segment.group(1).lstrip()

    remaining = _normalize_invoked_command_segment(segment=remaining)

    if _bash_stdin_invocation(segment=remaining):
        return False

    first_token = _first_shell_token(segment=remaining)
    if first_token is None:
        return False
    token_path = _strip_workspace_prefix(token=first_token)
    return _shell_paths_equal(
        left=_resolve_shell_path(token=token_path, cwd=cwd),
        right=path,
    )


def _segment_executes_reference_path(*, segment: str, path: str, cwd: str) -> bool:
    """Return True when one shell command segment executes ``path``."""
    stripped = segment.strip()
    if not stripped or _NON_EXECUTION_COMMAND.match(stripped):
        return False
    if _bash_stdin_invocation(segment=stripped):
        return False
    payload = _shell_command_string_payload_after_wrappers(segment=stripped)
    if payload is not None:
        return _line_references_path(line=payload, path=path, cwd=cwd)
    return _segment_invokes_path_directly(segment=stripped, path=path, cwd=cwd)


def _line_references_path(*, line: str, path: str, cwd: str) -> bool:
    """Return True when a shell line executes a script at ``path``."""
    stripped = line.strip()
    if not stripped or _is_comment_line(line=line):
        return False
    current = cwd
    for segment in re.split(r"&&|;", stripped):
        segment = segment.strip()
        if not segment:
            continue
        if _segment_executes_reference_path(
            segment=segment,
            path=path,
            cwd=current,
        ):
            return True
        if _segment_has_shell_pipeline(segment=segment):
            continue
        current = _shell_cwd_after_line(line=segment, cwd=current)
    return False


def _segment_has_shell_pipeline(*, segment: str) -> bool:
    """Return True when a segment contains a pipe operator other than ``||``."""
    return bool(re.search(r"(?<!\|)\|(?!\|)", segment))


def _shell_cwd_after_line(*, line: str, cwd: str) -> str:
    """Return cwd after sequential ``cd`` commands that persist in the shell."""
    current = _normalize_posix_shell_path(path=cwd)
    for segment in re.split(r"&&|;", line):
        segment = segment.strip()
        if not segment or _segment_has_shell_pipeline(segment=segment):
            continue
        if "||" in segment:
            left, right = (part.strip() for part in segment.split("||", 1))
            cd_match = re.match(r"(?i)^cd\s+(.+)$", left)
            if cd_match is None or not _FAIL_FAST_OR_TAIL.match(right):
                continue
            current = _resolve_shell_path(token=cd_match.group(1), cwd=current)
            continue
        cd_match = re.match(r"(?i)^cd\s+(.+)$", segment)
        if cd_match is None:
            continue
        current = _resolve_shell_path(token=cd_match.group(1), cwd=current)
    return current


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
    """Return a run line safe for regex matching (hide ``-c`` payload text)."""
    single_run = _single_run_command(line=line)
    if single_run is None:
        return line
    command = _single_run_command_text(match=single_run)
    if command is None:
        return line
    if _shell_command_string_payload_after_wrappers(segment=command) is not None:
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
    """Return False when a runtime flag terminates before executing a script."""
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
        if line.startswith("+") or line.startswith(" "):
            lines.append(line[1:])
    return "\n".join(lines)


def _resolve_github_action_root(*, path: PurePosixPath) -> PurePosixPath | None:
    """Walk up from an action implementation file to the local action root."""
    parts = path.parts
    if len(parts) < 3 or parts[:2] != (".github", "actions"):
        return None
    if path.name in {"action.yml", "action.yaml"}:
        dir_path = path.parent
    elif path.suffix.lower() in _NON_EXECUTABLE_WORKFLOW_SUFFIXES:
        return None
    elif (
        path.name == "Dockerfile"
        or bool(path.suffix)
        or path.name in {"run", "entrypoint"}
    ):
        dir_path = path.parent
    else:
        return None
    dir_parts = dir_path.parts
    artifact_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_BUILD_ARTIFACT_DIRS
        ),
        None,
    )
    if artifact_index is not None:
        return PurePosixPath(*dir_parts[:artifact_index])
    source_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_SOURCE_LAYOUT_DIRS
        ),
        None,
    )
    if source_index is not None:
        return PurePosixPath(*dir_parts[:source_index])
    if len(dir_path.parts) >= 3 and dir_path.parts[:2] == (".github", "actions"):
        return dir_path
    return None


def _github_action_directory(*, path: str) -> str | None:
    """Return the local action directory represented by an action implementation."""
    action_root = _resolve_github_action_root(path=PurePosixPath(path))
    if action_root is None:
        return None
    return action_root.as_posix()


def _github_action_reference_paths(*, path: str) -> list[str]:
    """Return the local action directory for workflow ``uses:`` matching."""
    action_dir = _github_action_directory(path=path)
    if action_dir is None:
        return []
    return [action_dir]
