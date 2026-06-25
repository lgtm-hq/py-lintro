"""Stable error codes for review context collection failures."""

from __future__ import annotations

from enum import StrEnum, auto


class ReviewContextErrorCode(StrEnum):
    """Machine-readable review context error codes."""

    NO_CHANGES = auto()
    DIFF_DESYNC = auto()
    NO_PARSEABLE_DIFF = auto()
    GIT_UNAVAILABLE = auto()
    NOT_GIT_REPO = auto()
    DEFAULT_BRANCH_UNKNOWN = auto()
    GIT_COMMAND_FAILED = auto()
    MERGE_BASE_FAILED = auto()
    GIT_OUTPUT_PARSE_FAILED = auto()
    GH_UNAVAILABLE = auto()
    GH_COMMAND_FAILED = auto()
    GH_JSON_INVALID = auto()
    GH_METADATA_INVALID = auto()
    REPETITIVE_SAMPLING_OMITTED = auto()
    INVALID_CHUNK_BUDGET = auto()
