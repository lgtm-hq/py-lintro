"""Shared constants for GitHub AI-review comment rendering."""

from __future__ import annotations

import re

from lintro.ai.review.models.review_finding import Severity

STICKY_MARKER = "<!-- lintro-ai-review -->"
STATE_MARKER_PREFIX = "<!-- lintro-ai-review-state:"
STATE_MARKER_SUFFIX = "-->"
STATE_VERSION = 1

# GitHub rejects comment bodies over 65,536 characters; stay well under.
MAX_COMMENT_CHARS = 60_000
# Cap how many run records are retained in the sticky state block.
MAX_STORED_RUNS = 30
# Slack reserved when budgeting the findings section against the comment cap,
# covering the truncation marker, section joins, and the final _cap_body notice.
_TRUNCATION_MARGIN = 400

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

_MENTION_RE = re.compile(r"(?<![\w/@.-])@(?=[A-Za-z0-9])")

_RUN_MECHANICS_RE = re.compile(
    r"\n\n<details><summary>⚙️ Run mechanics[\s\S]*?</details>",
)
_PREVIOUS_RUNS_RE = re.compile(
    r"\n\n<details><summary>🕔 Previous runs[\s\S]*?</details>",
)
_CHECKLIST_APPENDIX_RE = re.compile(
    r"\n### Cleared checks \(\d+\)[\s\S]*?(?=\n\n<details><summary>|\Z)",
)
_FINDINGS_SECTION_RE = re.compile(
    r"(\n### Findings(?: \(\d+\))?)([\s\S]*?)"
    r"(\n\*\*Structured checks:\*\* \d+[\s\S]*?(?=\n\n<details><summary>|\Z)|\Z)",
)
_FINDING_BLOCK_START_RE = re.compile(r"(?=\n\n[🔴🟠🟡] \*\*P[123]\*\*)")
