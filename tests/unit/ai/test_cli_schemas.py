"""Tests for native CLI schema builders and parsers."""

from __future__ import annotations

import json

import pytest
from assertpy import assert_that

from lintro.ai.cli_schemas import (
    FIX_CLI_SCHEMA,
    REVIEW_CLI_SCHEMA,
    SUMMARY_CLI_SCHEMA,
    cli_schema_for_fix,
    cli_schema_for_review,
    cli_schema_for_summary,
)
from lintro.ai.enums import AITransport
from lintro.ai.json_response import (
    parse_fix_response_payload,
    parse_review_response_payload,
    parse_summary_response_payload,
)


def test_cli_schema_for_review_only_when_cli_transport() -> None:
    """Review schema is attached only for CLI transport."""
    assert_that(cli_schema_for_review(transport=AITransport.API)).is_none()
    request = cli_schema_for_review(transport=AITransport.CLI)
    assert_that(request).is_not_none()
    assert_that(request.schema).is_equal_to(REVIEW_CLI_SCHEMA)


def test_cli_schema_for_summary_only_when_cli_transport() -> None:
    """Summary schema is attached only for CLI transport."""
    assert_that(cli_schema_for_summary(transport=AITransport.API)).is_none()
    request = cli_schema_for_summary(transport=AITransport.CLI)
    assert_that(request).is_not_none()
    assert_that(request.schema).is_equal_to(SUMMARY_CLI_SCHEMA)


def test_cli_schema_for_fix_supports_batch_mode() -> None:
    """Fix schema switches between single-object and batch array."""
    single = cli_schema_for_fix(transport=AITransport.CLI, batch=False)
    batch = cli_schema_for_fix(transport=AITransport.CLI, batch=True)
    assert_that(single).is_not_none()
    assert_that(batch).is_not_none()
    assert_that(single.schema["type"]).is_equal_to("object")
    assert_that(batch.schema["type"]).is_equal_to("array")


def test_parse_review_response_payload_accepts_fenced_json() -> None:
    """Review parser handles API-style fenced JSON."""
    payload = parse_review_response_payload(
        content='```json\n{"summary": "ok", "checklist": [], "findings": []}\n```',
    )
    assert_that(payload["summary"]).is_equal_to("ok")


def test_parse_summary_response_payload_rejects_non_object() -> None:
    """Summary parser rejects non-object payloads."""
    with pytest.raises(ValueError, match="must be an object"):
        parse_summary_response_payload(content="[]")


def test_parse_fix_response_payload_accepts_array() -> None:
    """Fix parser accepts batch arrays."""
    payload = parse_fix_response_payload(content=json.dumps([{"line": 1}]))
    assert_that(payload).is_length(1)
