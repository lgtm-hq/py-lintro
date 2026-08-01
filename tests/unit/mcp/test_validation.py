"""Unit tests for MCP argument validation and path-argument resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError
from lintro.mcp.registry import McpToolSpec
from lintro.mcp.validation import resolve_path_arguments, validate_arguments

_PATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["target"],
    "additionalProperties": False,
}


def _path_spec() -> McpToolSpec:
    """Build a tool spec declaring string and list path arguments.

    Returns:
        A tool specification with two declared path arguments.
    """
    return McpToolSpec(
        name="demo_paths",
        description="demo",
        input_schema=_PATH_SCHEMA,
        handler=lambda arguments: arguments,
        path_arguments=("target", "targets"),
    )


def test_validate_arguments_accepts_valid_payload() -> None:
    """A payload matching the schema passes validation."""
    validate_arguments(spec=_path_spec(), arguments={"target": "src"})


def test_validate_arguments_rejects_missing_required_field() -> None:
    """A missing required property raises INVALID_INPUT."""
    with pytest.raises(McpError) as exc_info:
        validate_arguments(spec=_path_spec(), arguments={})

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)
    assert_that(exc_info.value.to_dict()["detail"]["tool"]).is_equal_to("demo_paths")


def test_validate_arguments_rejects_wrong_type() -> None:
    """A property of the wrong type raises INVALID_INPUT with the field name."""
    with pytest.raises(McpError) as exc_info:
        validate_arguments(spec=_path_spec(), arguments={"target": 5})

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)
    assert_that(exc_info.value.to_dict()["detail"]["field"]).is_equal_to("target")


def test_validate_arguments_rejects_unknown_property() -> None:
    """``additionalProperties: false`` is enforced."""
    with pytest.raises(McpError) as exc_info:
        validate_arguments(
            spec=_path_spec(),
            arguments={"target": "src", "surprise": 1},
        )

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)


def test_resolve_path_arguments_is_noop_without_declarations() -> None:
    """A tool without declared path arguments gets its arguments unchanged."""
    spec = McpToolSpec(
        name="plain",
        description="d",
        input_schema={"type": "object", "properties": {"target": {}}},
        handler=lambda arguments: arguments,
    )

    assert_that(
        resolve_path_arguments(
            spec=spec,
            arguments={"target": "/etc/passwd"},
            workspace=Path.cwd(),
        ),
    ).is_equal_to({"target": "/etc/passwd"})


def test_resolve_path_arguments_resolves_strings_and_lists(tmp_path: Path) -> None:
    """Declared path arguments are rewritten to resolved absolute paths."""
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")

    resolved = resolve_path_arguments(
        spec=_path_spec(),
        arguments={"target": "a.py", "targets": ["b.py"]},
        workspace=tmp_path,
    )

    assert_that(resolved["target"]).is_equal_to(str((tmp_path / "a.py").resolve()))
    assert_that(resolved["targets"]).is_equal_to([str((tmp_path / "b.py").resolve())])


def test_resolve_path_arguments_rejects_escape(tmp_path: Path) -> None:
    """A declared path argument pointing outside the workspace is rejected."""
    with pytest.raises(McpError) as exc_info:
        resolve_path_arguments(
            spec=_path_spec(),
            arguments={"target": "../escape.py"},
            workspace=tmp_path,
        )

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)


def test_resolve_path_arguments_rejects_escape_inside_list(tmp_path: Path) -> None:
    """Every element of a path list is boundary-checked, not just the first."""
    (tmp_path / "ok.py").write_text("ok = 1\n", encoding="utf-8")

    with pytest.raises(McpError) as exc_info:
        resolve_path_arguments(
            spec=_path_spec(),
            arguments={"target": "ok.py", "targets": ["ok.py", "/etc/passwd"]},
            workspace=tmp_path,
        )

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)


def test_resolve_path_arguments_rejects_non_string_value(tmp_path: Path) -> None:
    """A path argument that is neither string nor string list is invalid."""
    with pytest.raises(McpError) as exc_info:
        resolve_path_arguments(
            spec=_path_spec(),
            arguments={"target": {"nested": "value"}},
            workspace=tmp_path,
        )

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)


def test_resolve_path_arguments_skips_absent_optional_path(tmp_path: Path) -> None:
    """Optional declared path arguments that are absent are left alone."""
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")

    resolved = resolve_path_arguments(
        spec=_path_spec(),
        arguments={"target": "a.py"},
        workspace=tmp_path,
    )

    assert_that(resolved).does_not_contain_key("targets")
