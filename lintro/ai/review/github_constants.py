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
# present, so a v2 blob re-encodes with only the version restamped. v4 (#2723)
# changes no field: it marks the point after which finding fingerprints are
# computed over the canonical category, so every open finding record read from
# a v2/v3 blob is archived as re-baselined rather than matched. v1 is no
# longer a readable version: #2305 retired its migration, so a v1 blob decodes
# as no state at all and the round starts fresh. v5 (#2796) adds
# ``pr_spend_usd``, the PR's cumulative review spend; a v4 artifact without
# it seeds the total from its surviving runs. v6 (#2803) adds each run's
# ``degradations`` records, so a rerun at the same head can redo a failed step
# or carry its warning; a v5 run loads with none.
STATE_VERSION = 6
STATE_VERSION_V2 = 2
STATE_VERSION_V3 = 3
STATE_VERSION_V4 = 4
STATE_VERSION_V5 = 5
#: Versions whose open finding records are re-baselined on load (#2723).
REBASELINED_STATE_VERSIONS = frozenset({STATE_VERSION_V2, STATE_VERSION_V3})

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
