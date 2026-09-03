"""Shared constants for GitHub AI-review comment rendering."""

from __future__ import annotations

import re

from lintro.ai.review.models.review_finding import Severity

STICKY_MARKER = "<!-- lintro-ai-review -->"
ARCHIVE_MARKER = "<!-- lintro-ai-review-archive -->"
# Split history into a second comment before GitHub's hard cap.
PRIMARY_SOFT_LIMIT = 56_000
STATE_MARKER_PREFIX = "<!-- lintro-ai-review-state:"
STATE_MARKER_SUFFIX = "-->"
# Current review-state schema version. v2 added per-run statistics and
# per-finding identity records on top of v1's run aggregates (issue #1906);
# v3 adds the per-round convergence score and the per-finding evidence style
# it is computed from (issue #2099). Both v3 additions are written only when
# present, so a v2 blob re-encodes byte-identically.
STATE_VERSION = 3
STATE_VERSION_V1 = 1
STATE_VERSION_V2 = 2

# GitHub rejects comment bodies over 65,536 characters.
GITHUB_COMMENT_HARD_LIMIT = 65_536
# Soft budget for the full sticky comment (visible body + hidden state block).
# Staying under this leaves headroom below GitHub's hard limit.
MAX_COMMENT_CHARS = 60_000
# Cap how many run records are retained in the sticky state block.
MAX_STORED_RUNS = 30
# Number of leading characters of a commit sha rendered in comment surfaces.
SHORT_SHA_LENGTH = 7

_SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.P1: "🔴",
    Severity.P2: "🟠",
    Severity.P3: "🟡",
}

_FOOTER = (
    "<sub>🤖 Automated review by lintro · not a substitute for human review · "
    "`~` = approximate (estimated locally; provider did not report token "
    "usage)</sub>"
)

#: One-line footer of the v5 sticky comment (#1909). Names where finding detail
#: actually lives, so the sticky is read as an index rather than a duplicate.
STICKY_FOOTER = (
    "<sub>🤖 lintro review · findings are commented inline · "
    "[how to read this report](https://github.com/lgtm-hq/py-lintro/blob/main/"
    "docs/ai-review-report.md)</sub>"
)

_MENTION_RE = re.compile(r"(?<![\w/@.-])@(?=[A-Za-z0-9])")
