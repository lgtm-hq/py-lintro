"""Native CLI JSON Schema contracts for AI products."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.json_response import CliSchemaRequest

if TYPE_CHECKING:
    pass

__all__ = [
    "FIX_CLI_SCHEMA",
    "REVIEW_CLI_SCHEMA",
    "SUMMARY_CLI_SCHEMA",
    "cli_schema_for_fix",
    "cli_schema_for_review",
    "cli_schema_for_summary",
]

REVIEW_CLI_SCHEMA: dict[str, object] = {
    "type": "object",
    # summary is intentionally not required at the root: parse_review_summary
    # already degrades to None (TL;DR-only rendering) when it is absent, so a
    # CLI response with only findings and checklist must still validate.
    "required": ["checklist", "findings"],
    "additionalProperties": False,
    "properties": {
        # The narrative fields (#1907) are optional: the schema stays
        # satisfiable by a model that only produces findings, and the parsers
        # degrade to TL;DR-only rendering when they are absent.
        "summary": {
            "type": "object",
            "required": ["headline"],
            "additionalProperties": False,
            "properties": {
                "headline": {"type": "string"},
                "walkthrough": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["text"],
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "finding_ref": {"type": "string"},
                        },
                    },
                },
            },
        },
        "verdict_reasoning": {
            "type": "object",
            "required": ["deciding_factor"],
            "additionalProperties": False,
            "properties": {
                "deciding_factor": {"type": "string"},
                "failure_mechanism": {"type": "string"},
                "files_needing_attention": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "file_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "overview"],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string"},
                    "overview": {"type": "string"},
                },
            },
        },
        "checklist": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "answer", "evidence"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer"},
                    "answer": {"type": "string", "enum": ["yes", "no"]},
                    "evidence": {"type": "string"},
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "severity",
                    "category",
                    "file",
                    "line",
                    "title",
                    "description",
                    "cause",
                    "fix",
                    "confidence",
                    "checklist_ids",
                ],
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "category": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "cause": {"type": "string"},
                    "fix": {"type": "string"},
                    # Requested by the prompt schema; without it here the
                    # strict CLI schema would reject the field outright.
                    "suggested_code": {"type": "string"},
                    # #1911 structured hunk. Optional like the rest: a fix that
                    # is not a clean hunk omits it and renders as a described
                    # one-liner instead of a committable suggestion.
                    "suggested_change": {
                        "type": "object",
                        "required": ["lines", "replacement"],
                        "additionalProperties": False,
                        "properties": {
                            "lines": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "replacement": {"type": "string"},
                            # #2101 anchor block. Optional so a model that
                            # omits it still produces a valid review; when
                            # present it is what patch validation matches
                            # against the file at head.
                            "before": {"type": "string"},
                        },
                    },
                    # #1925 finding-model fields. All optional: the parser
                    # degrades every one of them to a default, so a model that
                    # ignores them still produces a valid review.
                    "kind": {
                        "type": "string",
                        "enum": ["finding", "question"],
                    },
                    "failure_scenario": {"type": "string"},
                    "evidence_style": {
                        "type": "string",
                        "enum": ["diff_local", "cross_file", "speculative"],
                    },
                    "occurrences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["file", "line"],
                            "additionalProperties": False,
                            "properties": {
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                            },
                        },
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "checklist_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
            },
        },
        "flagged_files": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "reason"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

SUMMARY_CLI_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [
        "overview",
        "key_patterns",
        "priority_actions",
        "triage_suggestions",
        "estimated_effort",
    ],
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string"},
        "key_patterns": {"type": "array", "items": {"type": "string"}},
        "priority_actions": {"type": "array", "items": {"type": "string"}},
        "triage_suggestions": {"type": "array", "items": {"type": "string"}},
        "estimated_effort": {"type": "string"},
    },
}

FIX_CLI_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [
        "original_code",
        "suggested_code",
        "explanation",
        "confidence",
        "risk_level",
    ],
    "additionalProperties": False,
    "properties": {
        "original_code": {"type": "string"},
        "suggested_code": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "risk_level": {
            "type": "string",
            "enum": ["safe-style", "behavioral-risk"],
        },
    },
}

FIX_BATCH_CLI_SCHEMA: dict[str, object] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": [
            "line",
            "original_code",
            "suggested_code",
            "explanation",
            "confidence",
            "risk_level",
        ],
        "additionalProperties": False,
        "properties": {
            "line": {"type": "integer"},
            "original_code": {"type": "string"},
            "suggested_code": {"type": "string"},
            "explanation": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "risk_level": {
                "type": "string",
                "enum": ["safe-style", "behavioral-risk"],
            },
        },
    },
}


def cli_schema_for_review(*, transport: AITransport | None) -> CliSchemaRequest | None:
    """Return native review schema args for CLI transport."""
    if transport != AITransport.CLI:
        return None
    return CliSchemaRequest(schema=REVIEW_CLI_SCHEMA, schema_name="lintro_review")


def cli_schema_for_summary(*, transport: AITransport | None) -> CliSchemaRequest | None:
    """Return native summary schema args for CLI transport."""
    if transport != AITransport.CLI:
        return None
    return CliSchemaRequest(schema=SUMMARY_CLI_SCHEMA, schema_name="lintro_summary")


def cli_schema_for_fix(
    *,
    transport: AITransport | None,
    batch: bool = False,
) -> CliSchemaRequest | None:
    """Return native fix schema args for CLI transport."""
    if transport != AITransport.CLI:
        return None
    schema = FIX_BATCH_CLI_SCHEMA if batch else FIX_CLI_SCHEMA
    name = "lintro_fix_batch" if batch else "lintro_fix"
    return CliSchemaRequest(schema=schema, schema_name=name)
