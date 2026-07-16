"""Stable error codes for review context collection failures."""

from __future__ import annotations

from enum import StrEnum


class ReviewContextErrorCode(StrEnum):
    """Machine-readable review context error codes."""

    NO_CHANGES = "no-changes"
    DIFF_DESYNC = "diff-desync"
    NO_PARSEABLE_DIFF = "no-parseable-diff"
    GIT_UNAVAILABLE = "git-unavailable"
    NOT_GIT_REPO = "not-git-repo"
    DEFAULT_BRANCH_UNKNOWN = "default-branch-unknown"
    GIT_COMMAND_FAILED = "git-command-failed"
    MERGE_BASE_FAILED = "merge-base-failed"
    GIT_OUTPUT_PARSE_FAILED = "git-output-parse-failed"
    GH_UNAVAILABLE = "gh-unavailable"
    GH_COMMAND_FAILED = "gh-command-failed"
    GH_JSON_INVALID = "gh-json-invalid"
    GH_METADATA_INVALID = "gh-metadata-invalid"
    REPETITIVE_SAMPLING_OMITTED = "repetitive-sampling-omitted"
    INVALID_CHUNK_BUDGET = "invalid-chunk-budget"
    INVALID_REVIEW_MODE = "invalid-review-mode"
    BASH_UNAVAILABLE = "bash-unavailable"
