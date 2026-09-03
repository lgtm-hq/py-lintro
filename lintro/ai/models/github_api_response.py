"""Outcome of a single authenticated GitHub REST call."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GitHubApiResponse"]


@dataclass(frozen=True, slots=True)
class GitHubApiResponse:
    """What GitHub answered to one REST request.

    A boolean is enough to know a call failed but not to say why, which is how
    a throttled token ended up reported as a line-mapping problem (#2266).

    Attributes:
        status: HTTP status GitHub answered with, or ``None`` when the request
            was refused locally or failed in transit.
        message: Error text GitHub returned, empty on success or when the
            body could not be read.
    """

    status: int | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        """Return whether GitHub accepted the request.

        Returns:
            True when GitHub answered with a 2xx status.
        """
        return self.status is not None and 200 <= self.status < 300
