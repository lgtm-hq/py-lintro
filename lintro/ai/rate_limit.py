"""``Retry-After`` extraction for provider rate-limit errors (#2506).

Both vendor SDKs raise their rate-limit error with the offending
``httpx.Response`` attached, and both services send an HTTP ``Retry-After``
header on 429. Honouring it is strictly better than exponential backoff:
the server knows when the bucket refills, and a shorter guessed delay only
burns another retry slot.

The value is honoured up to :data:`MAX_RETRY_AFTER_SECONDS` — a longer wait
is capped to it, not discarded. The parsing is otherwise defensive: a
missing, malformed, or already-elapsed header degrades to ``None``, which
leaves the caller on its own backoff.
"""

from __future__ import annotations

import math
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Final

# Ceiling on an honoured Retry-After. A longer advertised wait is clamped
# to this, not rejected: a provider saying "wait 10 minutes" is better
# served by waiting 5 than by a one-second backoff that will 429 again.
# The clamp keeps a single wait from blowing past the job budget on its
# own, while the retry budget still bounds how many such waits happen.
MAX_RETRY_AFTER_SECONDS: float = 300.0

# RFC 9110 §10.2.3: ``delay-seconds = 1*DIGIT``. No sign, no decimal
# point, no exponent, no unit suffix — so "1.5", "1e2" and "+3" are
# malformed, however happily ``float()`` would take them.
_DELTA_SECONDS_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9]+$")


def parse_retry_after(
    raw: str | None,
    *,
    now_timestamp: float | None = None,
) -> float | None:
    """Parse an HTTP ``Retry-After`` header value into seconds.

    Both header forms are accepted: delta-seconds (``"3"``) and an
    HTTP-date (``"Wed, 21 Oct 2026 07:28:00 GMT"``).

    Args:
        raw: Raw header value, or ``None`` when the header was absent.
        now_timestamp: POSIX timestamp used as "now" when the value is an
            HTTP-date. Defaults to the current time.

    Returns:
        A non-negative, finite delay in seconds, clamped to
        :data:`MAX_RETRY_AFTER_SECONDS` when the header asks for longer,
        or ``None`` when the value is absent, malformed, non-finite, or
        already in the past.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    seconds = _parse_delta_seconds(text)
    if seconds is None:
        seconds = _parse_http_date_delta(text, now_timestamp=now_timestamp)
    # A digit run longer than a float can hold still overflows to ``inf``,
    # and an HTTP-date can land outside the float range too. ``nan``/
    # ``inf`` evade the negative test and survive ``min``, and
    # ``asyncio.sleep(nan)`` raises rather than backing off.
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _parse_delta_seconds(text: str) -> float | None:
    """Parse the delta-seconds form of ``Retry-After``.

    Only the RFC 9110 grammar is accepted, ``1*DIGIT``. ``float()`` alone
    would take "1.5", "1e2", "+3", "nan" and "inf", none of which a
    conforming server sends.

    Args:
        text: Stripped header value.

    Returns:
        The delay in seconds, or ``None`` when the value is not a run of
        ASCII digits. A digit run too long for a float still yields
        ``inf``; the finite check in :func:`parse_retry_after` rejects it.
    """
    if _DELTA_SECONDS_RE.match(text) is None:
        return None
    try:
        return float(text)
    except ValueError:  # pragma: no cover - digits always parse
        return None


def _parse_http_date_delta(
    text: str,
    *,
    now_timestamp: float | None,
) -> float | None:
    """Parse the HTTP-date form of ``Retry-After`` into a delay.

    Args:
        text: Stripped header value.
        now_timestamp: POSIX timestamp used as "now"; defaults to now.

    Returns:
        Seconds until the given instant, or ``None`` when unparseable.
    """
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    reference = now_timestamp if now_timestamp is not None else time.time()
    return target.timestamp() - reference


def retry_after_from_exception(exc: Any) -> float | None:
    """Extract ``Retry-After`` from a vendor SDK error, if present.

    The anthropic and openai SDKs both hang the ``httpx.Response`` off the
    error as ``.response``; some error paths carry ``.headers`` directly.
    Anything else — including an SDK that changes shape — yields ``None``
    rather than raising inside an error handler.

    Args:
        exc: The vendor SDK exception.

    Returns:
        Parsed delay in seconds, or ``None`` when no usable header exists.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except (AttributeError, TypeError):
        return None
    return parse_retry_after(raw if isinstance(raw, str) else None)
