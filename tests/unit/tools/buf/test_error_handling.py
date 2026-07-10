"""Tests for BufPlugin error handling and documentation URLs."""

from __future__ import annotations

from typing import cast

import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.buf import BufPlugin


def test_doc_url_for_lint_rule(buf_plugin: BufPlugin) -> None:
    """Lint rule codes resolve to the buf rules documentation page.

    Args:
        buf_plugin: The plugin under test.
    """
    url = buf_plugin.doc_url("PACKAGE_LOWER_SNAKE_CASE")
    assert_that(url).is_not_none()
    assert_that(url).contains("buf.build")


def test_doc_url_none_for_non_rule_codes(buf_plugin: BufPlugin) -> None:
    """Compile/format/empty codes have no rule documentation URL.

    Args:
        buf_plugin: The plugin under test.
    """
    assert_that(buf_plugin.doc_url("COMPILE")).is_none()
    assert_that(buf_plugin.doc_url("FORMAT")).is_none()
    assert_that(buf_plugin.doc_url("")).is_none()


def test_check_timeout_returns_error_result(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout during check yields a TIMEOUT issue.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=subprocess.TimeoutExpired(cmd="buf", timeout=30),
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    assert_that(result.issues[0].code)  # type: ignore[index, union-attr].is_equal_to("TIMEOUT")


def test_fix_timeout_returns_error_result(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout during fix yields a TIMEOUT issue.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=subprocess.TimeoutExpired(cmd="buf", timeout=30),
        ):
            result = buf_plugin.fix([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues[-1].code).is_equal_to("TIMEOUT")
