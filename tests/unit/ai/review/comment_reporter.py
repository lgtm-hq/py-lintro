"""In-memory GitHub issue comments for posting lifecycle regressions."""

from pathlib import Path
from unittest.mock import MagicMock

from lintro.ai.integrations.github_pr import GitHubPRReporter


class CommentReporter(GitHubPRReporter):
    """Store comment bodies while using the production single-marker lookup."""

    def __init__(self, bodies: tuple[str, ...] = ()) -> None:
        """Seed the pull request with ordered comment bodies.

        Args:
            bodies: Existing comments, assigned sequential ids.
        """
        super().__init__(
            repo="owner/name",
            pr_number=7,
            workspace_root=Path.cwd(),
        )
        self.comments = dict(enumerate(bodies, start=1))
        self.lookup_unknown = False
        self.delete_ok = True

    def is_available(self) -> bool:
        """Enable the in-memory PR context without credentials.

        Returns:
            True for this test reporter.
        """
        return True

    def lookup_issue_comments(
        self,
        *,
        markers: tuple[str, ...],
        anchored: tuple[str, ...] = (),
        strict: bool = True,
        all_matches: list[tuple[int, str]] | None = None,
    ) -> dict[str, tuple[int, str]] | None:
        """Find comments by marker, or simulate an unavailable listing.

        Args:
            markers: Markers to locate.
            anchored: Markers that must lead the comment body.
            strict: Whether malformed entries invalidate the listing.
            all_matches: Collect all anchored matches when supplied.

        Returns:
            First match for each marker, or an unknown listing.
        """
        if self.lookup_unknown:
            return None
        found: dict[str, tuple[int, str]] = {}
        for comment_id, body in self.comments.items():
            for marker in markers:
                if body.startswith(marker) if marker in anchored else marker in body:
                    if marker in anchored and all_matches is not None:
                        all_matches.append((comment_id, body))
                    found.setdefault(marker, (comment_id, body))
        return found

    def post_issue_comment(self, body: str) -> bool:
        """Create a comment.

        Args:
            body: Comment body.

        Returns:
            Whether the comment was created.
        """
        self.comments[max(self.comments, default=0) + 1] = body
        return True

    def update_issue_comment_status(self, *, comment_id: int, body: str) -> int | None:
        """Replace an existing comment.

        Args:
            comment_id: Comment id.
            body: New body.

        Returns:
            HTTP status for the in-memory edit.
        """
        if comment_id not in self.comments:
            return 404
        self.comments[comment_id] = body
        return 200

    def delete_issue_comment(self, *, comment_id: int) -> bool:
        """Delete a comment unless failure was requested.

        Args:
            comment_id: Comment id.

        Returns:
            Whether deletion succeeded.
        """
        if self.delete_ok:
            self.comments.pop(comment_id, None)
        return self.delete_ok

    def fetch_pr_diff_lines(self) -> dict[str, set[int]] | None:
        """Return no inline anchors.

        Returns:
            Empty diff mapping.
        """
        return {}


def use_combined_comment_lookup(reporter: MagicMock) -> None:
    """Adapt a legacy reporter stub to the shared comment listing.

    Args:
        reporter: Stub whose single-marker lookup already models the board.
    """

    def lookup(**_kwargs: object) -> dict[str, tuple[int, str]]:
        """Find the stub's sticky using its existing lookup sequence.

        Args:
            **_kwargs: Listing options unused by a stub without failure notices.

        Returns:
            The existing sticky match, or confirmed absence.
        """
        from lintro.ai.review.github_constants import STICKY_MARKER

        found = reporter.find_issue_comment(marker=STICKY_MARKER)
        return {STICKY_MARKER: found} if found else {}

    reporter.lookup_issue_comments.side_effect = lookup
