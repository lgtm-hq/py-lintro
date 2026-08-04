"""Model-written reasoning that explains a derived readiness verdict."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["VerdictReasoning"]


@dataclass(frozen=True, slots=True)
class VerdictReasoning:
    """Why the change is or is not safe to merge.

    The verdict itself is derived in code from open finding severities; this
    record carries only the model's explanation of it.

    Attributes:
        deciding_factor: One short paragraph naming the single issue that
            decides the verdict (or stating that nothing blocks the merge).
        failure_mechanism: One short paragraph tracing how that issue fails in
            production. Empty when nothing blocks the merge.
        files_needing_attention: Repository-relative paths a reviewer should
            look at first.
    """

    deciding_factor: str
    failure_mechanism: str = ""
    files_needing_attention: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """Return True when no reasoning text or file pointer is present."""
        return not (
            self.deciding_factor.strip()
            or self.failure_mechanism.strip()
            or self.files_needing_attention
        )
