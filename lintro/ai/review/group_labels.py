"""Semantic chunk relationship labels for AI diff review."""

from __future__ import annotations

from typing import Literal, TypeAlias

RelationshipLabel: TypeAlias = Literal[
    "workflow+script+test",
    "source+test",
    "directory-prefix",
    "single-file",
]

REL_WORKFLOW_SCRIPT_TEST: RelationshipLabel = "workflow+script+test"
REL_SOURCE_TEST: RelationshipLabel = "source+test"
REL_DIRECTORY_PREFIX: RelationshipLabel = "directory-prefix"
REL_SINGLE_FILE: RelationshipLabel = "single-file"
