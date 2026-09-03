"""Models describing the committed eval corpus."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.models.review_finding import ReviewFinding, Severity

__all__ = ["Corpus", "CorpusItem", "LabeledFinding"]


@dataclass(frozen=True, slots=True)
class LabeledFinding:
    """A human-adjudicated finding a config is expected to report.

    Only the three fields that form a finding fingerprint are labeled
    (:func:`lintro.ai.review.finding_matcher.fingerprint_for` hashes file,
    category and title), because a label must survive line drift and reworded
    prose the same way a tracked finding does.

    Attributes:
        file: Repository-relative path the finding belongs to.
        category: Finding category label, matched case-insensitively.
        title: Expected finding title, matched after normalization.
        severity: Severity the label carries, used for the expected verdict.
    """

    file: str
    category: str
    title: str
    severity: Severity = Severity.P2

    def to_finding(self) -> ReviewFinding:
        """Return the label as a :class:`ReviewFinding` for the matcher.

        Returns:
            A finding carrying only the identity-bearing fields; prose fields
            are empty because labels are identity claims, not review copy.
        """
        return ReviewFinding(
            severity=self.severity,
            category=self.category,
            file=self.file,
            line=0,
            title=self.title,
            description="",
            cause="",
            fix="",
            confidence="",
        )


@dataclass(frozen=True, slots=True)
class CorpusItem:
    """One reviewable unit of the corpus.

    Attributes:
        item_id: Stable identifier used in run directories and reports.
        repo: ``owner/name`` the pull request belongs to.
        pr: Pull request number passed to ``lintro review --pr``.
        title: Human-readable label for reports.
        labeled_findings: Ground-truth labels, empty when the item is
            unlabeled. Efficacy is only reported for labeled items.
    """

    item_id: str
    repo: str
    pr: int
    title: str = ""
    labeled_findings: tuple[LabeledFinding, ...] = field(default_factory=tuple)

    @property
    def is_labeled(self) -> bool:
        """Return True when the item carries ground-truth labels."""
        return bool(self.labeled_findings)


@dataclass(frozen=True, slots=True)
class Corpus:
    """A whole committed corpus file.

    Attributes:
        version: Schema version of the corpus file.
        items: Corpus items, in file order.
    """

    version: int
    items: tuple[CorpusItem, ...] = field(default_factory=tuple)

    @property
    def labeled_items(self) -> tuple[CorpusItem, ...]:
        """Return only the items carrying ground-truth labels."""
        return tuple(item for item in self.items if item.is_labeled)
