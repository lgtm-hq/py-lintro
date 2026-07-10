"""Shell command parsing for workflow ``run:`` script matching.

Tokenizes and strips ``run:`` shell commands so a changed script path can be
matched against the command that would execute it. Handles interpreter
prefixes (``bash``/``sh``/``python``/``node``/``bun``), ``uv run`` wrappers,
``env``/``VAR=`` assignments, ``sudo``/``timeout`` dispatchers, ``exec``/
``command`` wrappers, compound-command leaders, ``-c``/``-e`` inline-code
payloads, and ``cd`` working-directory tracking. These helpers are pure string
utilities with no knowledge of YAML workflow structure.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

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
_COMMAND_DISPATCH_WRAPPERS = frozenset({"sudo", "timeout"})
_DISPATCH_OPERAND_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset(
        {
            "-u",
            "-g",
            "-C",
            "-p",
            "-r",
            "-t",
            "-U",
            "-h",
            "-R",
            "--user",
            "--group",
            "--prompt",
            "--chdir",
            "--role",
            "--type",
        },
    ),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
}
# Mandatory positional operands consumed before the wrapped command (for
# example ``timeout DURATION command``).
_DISPATCH_POSITIONAL_OPERANDS: dict[str, int] = {"sudo": 0, "timeout": 1}
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
_FAIL_FAST_OR_TAIL = re.compile(
    r"(?i)^(?:exit(?:\s+\d+)?|false|return(?:\s+\d+)?|:)\b",
)
_NON_EXECUTION_COMMAND = re.compile(
    r"(?i)^(?:grep|cat|head|tail|less|more|sed|awk|sort|uniq|wc|find|ls|stat|"
    r"read|diff|cmp|strings|od|xxd|echo|printf|chmod|chown|chgrp|cp|mv|install|"
    r"ln|rsync|scp|touch|rm)\b",
)
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


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


def _runtime_option_is_command_string(*, runtime: str, token: str) -> bool:
    """Return True when a flag makes the runtime treat its operand as inline code.

    Detects ``python``/``python3 -c`` and ``node``/``bun -e``/``--eval`` where the
    following operand is an inline program string rather than a script path, so it
    must not be matched as an executed file. Attached short-option forms such as
    ``-c'code'`` or ``-escript`` are covered too, because the runtime still treats
    the glued operand as inline code. ``bash``/``sh -c`` payloads are handled
    separately because their operand may itself invoke a script path.

    Args:
        runtime: Lower-cased interpreter name (for example ``python3``).
        token: The shell token under inspection.

    Returns:
        True when the token switches the runtime into command-string/eval mode.
    """
    if runtime.startswith("python"):
        return bool(re.match(r"(?i)^-c", token))
    if runtime.startswith("node") or runtime == "bun":
        if token.startswith("--"):
            return token[2:].split("=", 1)[0] == "eval"
        return bool(re.match(r"(?i)^-e", token))
    return False


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


def _shell_interpreter_scan(*, segment: str) -> tuple[str, bool]:
    """Scan a leading workflow runtime, returning its tail and command-string mode.

    Args:
        segment: A shell command segment that may begin with an interpreter.

    Returns:
        A ``(remaining, is_command_string)`` pair. ``remaining`` is the segment
        text after the interpreter and its consumed flags; ``is_command_string``
        is True when a ``-c``/``-e``/``--eval`` inline-code flag was reached, in
        which case the operand must not be treated as a script path.
    """
    remaining = segment.strip()
    runtime_match = re.match(
        rf"(?i)^(?P<runtime>{_WORKFLOW_SCRIPT_RUNTIMES})\b",
        remaining,
    )
    if runtime_match is None:
        return remaining, False
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
            return "", False
        if _runtime_option_is_command_string(runtime=runtime, token=token):
            return _rest_after_first_shell_token(segment=remaining), True
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
    return remaining, False


def _strip_shell_interpreter_prefix(*, segment: str) -> str:
    """Skip a leading workflow script runtime and its flags.

    Returns an empty string when the runtime enters command-string/eval mode so
    the inline-code operand is never exposed as an executable script path.
    """
    remaining, is_command_string = _shell_interpreter_scan(segment=segment)
    if is_command_string:
        return ""
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


def _strip_command_dispatch_prefixes(*, segment: str) -> str:
    """Skip ``sudo``/``timeout`` wrappers that still execute the wrapped command."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        wrapper = token.lower()
        if wrapper not in _COMMAND_DISPATCH_WRAPPERS:
            break
        operand_flags = _DISPATCH_OPERAND_FLAGS.get(wrapper, frozenset())
        remaining = _rest_after_first_shell_token(segment=remaining)
        while True:
            option = _first_shell_token(segment=remaining)
            if option is None or not option.startswith("-"):
                break
            remaining = _rest_after_first_shell_token(segment=remaining)
            if option == "--":
                break
            flag_name = option.split("=", 1)[0]
            if "=" not in option and flag_name in operand_flags:
                operand = _first_shell_token(segment=remaining)
                if operand is not None and not operand.startswith("-"):
                    remaining = _rest_after_first_shell_token(segment=remaining)
        for _ in range(_DISPATCH_POSITIONAL_OPERANDS.get(wrapper, 0)):
            operand = _first_shell_token(segment=remaining)
            if operand is None or operand.startswith("-"):
                break
            remaining = _rest_after_first_shell_token(segment=remaining)
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


def _interpreter_command_string_invocation(*, segment: str) -> bool:
    """Return True when a segment runs interpreter inline code, not a script path.

    Detects ``python``/``python3 -c`` and ``node``/``bun -e``/``--eval`` after any
    ``env``/``VAR=``, ``sudo``/``timeout``, ``uv run``, ``exec``/``command`` and
    compound-leader wrappers, mirroring the ``bash``/``sh -c`` payload and ``-s``
    stdin short-circuits. The operand is inline code, so the segment executes no
    file and must be excluded from workflow script matching.

    Args:
        segment: A single shell command segment.

    Returns:
        True when the leading interpreter consumes its operand as inline code.
    """
    remaining = segment.strip()
    while remaining:
        if _shell_interpreter_scan(segment=remaining)[1]:
            return True
        previous = remaining
        remaining = _strip_leading_shell_prefixes(segment=remaining)
        remaining = _strip_command_dispatch_prefixes(segment=remaining)
        if re.match(r"(?i)^uv\s+run\b", remaining):
            remaining = re.sub(r"(?i)^uv\s+run\b", "", remaining, count=1).lstrip()
            remaining = _strip_uv_cli_options(segment=remaining)
        remaining = _strip_shell_dispatch_wrappers(segment=remaining)
        remaining = _strip_shell_compound_leaders(segment=remaining)
        if remaining == previous:
            break
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
        remaining = _strip_command_dispatch_prefixes(segment=remaining)
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
        remaining = _strip_command_dispatch_prefixes(segment=remaining)
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
    if _interpreter_command_string_invocation(segment=stripped):
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
