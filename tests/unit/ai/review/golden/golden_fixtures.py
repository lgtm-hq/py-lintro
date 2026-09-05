"""Fixed inputs for the AI review golden suite (issue #2298).

One :class:`~lintro.ai.review.models.review_context.ReviewContext` covering
the three shapes the review pipeline treats differently — a text modification,
a binary file, and a rename — plus the deterministic knobs (boundary marker,
checklist, classifications) the prompt builders read. Nothing here is random,
so every golden in ``snapshots/`` is reproducible from this module alone.
"""

from __future__ import annotations

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext

#: Fixed boundary marker patched over :func:`make_boundary_marker`, which is
#: random by design (``lintro/ai/sanitize.py``). Without pinning it no prompt
#: golden could exist at all.
GOLDEN_BOUNDARY: str = "CODE_BLOCK_0d15ea5e"

#: Repository-relative paths of the fixture's three changed files.
TEXT_FILE: str = "src/auth/session.py"
BINARY_FILE: str = "assets/logo.png"
RENAMED_FILE: str = "src/auth/tokens.py"
RENAMED_FROM: str = "src/auth/token_utils.py"

#: Low-entropy stand-in that still matches the ``api[_-]?key`` rule in
#: ``lintro/ai/secrets.py``. The prompt goldens exist partly to prove the
#: redaction choke point still fires, so the fixture must trip a rule.
FAKE_SECRET_LINE: str = 'API_KEY = "example_placeholder_value_not_real"'

_UNIFIED_DIFF: str = f"""diff --git a/{TEXT_FILE} b/{TEXT_FILE}
index 1111111..2222222 100644
--- a/{TEXT_FILE}
+++ b/{TEXT_FILE}
@@ -1,6 +1,9 @@
 from __future__ import annotations

+{FAKE_SECRET_LINE}
+
 def is_active(status: str) -> bool:
-    return status == "active"
+    if status == "expired":
+        return False
+    return True
diff --git a/{BINARY_FILE} b/{BINARY_FILE}
index 3333333..4444444 100644
Binary files a/{BINARY_FILE} and b/{BINARY_FILE} differ
diff --git a/{RENAMED_FROM} b/{RENAMED_FILE}
similarity index 92%
rename from {RENAMED_FROM}
rename to {RENAMED_FILE}
index 5555555..6666666 100644
--- a/{RENAMED_FROM}
+++ b/{RENAMED_FILE}
@@ -1,4 +1,4 @@
-def decode(token: str) -> dict[str, str]:
+def decode(token: str, *, verify: bool = False) -> dict[str, str]:
     return {{}}
"""


def golden_changed_files() -> list[ChangedFile]:
    """Return the fixture's changed files in diff order.

    Returns:
        One modified text file, one binary file, and one rename.
    """
    return [
        ChangedFile(
            path=TEXT_FILE,
            status=ChangedFileStatus.MODIFIED,
            additions=5,
            deletions=1,
        ),
        ChangedFile(
            path=BINARY_FILE,
            status=ChangedFileStatus.MODIFIED,
            additions=0,
            deletions=0,
        ),
        ChangedFile(
            path=RENAMED_FILE,
            status=ChangedFileStatus.RENAMED,
            additions=1,
            deletions=1,
            previous_path=RENAMED_FROM,
        ),
    ]


def golden_review_context() -> ReviewContext:
    """Return the fixed review context every golden is built from.

    Returns:
        A review context with PR metadata and the three-file diff.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature/session-gate",
        changed_files=golden_changed_files(),
        unified_diff=_UNIFIED_DIFF,
        pr_metadata=PRMetadata(
            title="Harden the session gate",
            body="Fail closed on unknown session status and widen token decoding.",
            number=4242,
            repo="lgtm-hq/py-lintro",
        ),
        repo_root="/workspace/py-lintro",
    )


def golden_chunks() -> list[ReviewChunk]:
    """Return the two fixed chunks the multi-chunk goldens replay.

    Chunk boundaries are pinned here rather than derived from the token
    budget so the merge and metadata goldens stay stable when the budget
    heuristics change.

    Returns:
        Two chunks covering the text file and the rename.
    """
    diff = golden_review_context().unified_diff
    text_part, rename_part = diff.split(f"diff --git a/{RENAMED_FROM}", maxsplit=1)
    return [
        ReviewChunk(
            id=1,
            files=[TEXT_FILE, BINARY_FILE],
            diff=text_part.rstrip("\n") + "\n",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=[RENAMED_FILE],
            diff=f"diff --git a/{RENAMED_FROM}{rename_part}",
            relationship=REL_SINGLE_FILE,
        ),
    ]


def golden_checklist_items() -> list[ChecklistItem]:
    """Return the fixed checklist items used by the prompt goldens.

    Returns:
        Two tier-1 checklist items with stable ids.
    """
    return [
        ChecklistItem(
            id=1,
            question="Does the change fail closed on unknown input?",
            domains=(),
            languages=(),
            category=ReviewCategory.SECURITY,
            tier=1,
        ),
        ChecklistItem(
            id=2,
            question="Is the renamed module's public signature still compatible?",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]


def golden_checklist_text() -> str:
    """Return the pre-formatted checklist prompt text.

    Returns:
        Numbered checklist rows matching :func:`golden_checklist_items`.
    """
    return (
        "1. [security] Does the change fail closed on unknown input?\n"
        "2. [logic-bug] Is the renamed module's public signature still compatible?"
    )


def golden_classifications() -> list[FileClassification]:
    """Return fixed domain classifications for the fixture's files.

    Returns:
        One classification per changed file.
    """
    return [
        FileClassification(path=TEXT_FILE, domains=[FileDomain.SECURITY]),
        FileClassification(path=BINARY_FILE, domains=[]),
        FileClassification(path=RENAMED_FILE, domains=[]),
    ]
