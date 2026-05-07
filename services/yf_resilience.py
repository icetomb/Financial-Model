"""
Tiny resilience layer for yfinance / Yahoo Finance HTTP calls.

Yahoo's public endpoints have a long history of intermittent ``401
Unauthorized``, ``429 Too Many Requests``, and ``5xx`` responses that
clear up on retry seconds later.  This module gives the rest of the
codebase one place to opt into safe retries without each caller
hand-rolling its own backoff loop.

Two public symbols:

- :func:`is_transient_error` – returns True if an exception (or any
  exception in its ``__cause__`` / ``__context__`` chain) looks like a
  transient HTTP / network failure that is worth retrying.
- :func:`with_retries`       – call ``fn(*args, **kwargs)`` with
  exponential backoff on transient errors.  Non-transient errors are
  re-raised immediately so genuine bugs never get masked.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Transient-error detection
# ---------------------------------------------------------------------------

# Patterns we look for in the *string representation* of an exception (or its
# cause).  Yahoo's errors arrive in many wrappers (urllib HTTPError, requests
# HTTPError, yfinance's own subclasses) so string matching is the most robust
# common denominator.
_TRANSIENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(401|429|500|502|503|504)\b"),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"timed?\s*out|timeout", re.IGNORECASE),
    re.compile(r"connection (refused|reset|aborted|error)", re.IGNORECASE),
    re.compile(r"temporarily (unavailable|down)", re.IGNORECASE),
    re.compile(r"server\s+error", re.IGNORECASE),
    re.compile(r"bad\s+gateway", re.IGNORECASE),
    re.compile(r"service\s+unavailable", re.IGNORECASE),
    re.compile(r"gateway\s+timeout", re.IGNORECASE),
    re.compile(r"remote end closed", re.IGNORECASE),
)


def _build_transient_types() -> tuple[type[BaseException], ...]:
    """Collect known transient exception classes from optional libraries."""
    types: list[type[BaseException]] = []

    import urllib.error as _urlerr  # always available
    types.extend([_urlerr.URLError, _urlerr.HTTPError])

    try:
        import requests  # type: ignore[import]
        types.extend([
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ])
    except Exception:  # pragma: no cover - requests is a yfinance dep, but be safe
        pass

    types.extend([TimeoutError, ConnectionError])
    return tuple(types)


_TRANSIENT_TYPES: tuple[type[BaseException], ...] = _build_transient_types()


def is_transient_error(exc: BaseException | None) -> bool:
    """Return True if *exc* (or any cause it wraps) looks transient.

    Walks both ``__cause__`` and ``__context__`` so wrapped errors – e.g.
    ``models.model_1.download_data`` raises ``PredictionError`` from a
    ``urllib.error.HTTPError`` – are still detected correctly.
    """
    seen: set[int] = set()

    def _walk(e: BaseException | None) -> bool:
        if e is None or id(e) in seen:
            return False
        seen.add(id(e))

        if isinstance(e, _TRANSIENT_TYPES):
            return True

        message = str(e)
        if any(p.search(message) for p in _TRANSIENT_PATTERNS):
            return True

        return _walk(getattr(e, "__cause__", None)) or _walk(getattr(e, "__context__", None))

    return _walk(exc)


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.5
DEFAULT_MAX_DELAY = 10.0


def with_retries(
    fn: Callable[..., T],
    *args,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    logger: logging.Logger | None = None,
    label: str = "operation",
    sleeper: Callable[[float], None] | None = None,
    **kwargs,
) -> T:
    """Call ``fn(*args, **kwargs)`` with retries on transient errors.

    - Up to ``attempts`` calls (so ``attempts=3`` = initial + 2 retries).
    - Backoff is ``base_delay * 2**(attempt-1)`` capped at ``max_delay``,
      so by default the waits are 1.5 s, 3 s, 6 s, ...
    - Non-transient errors are re-raised on the first failure: a model's
      "not enough history" PredictionError must NOT trigger 3 expensive
      retries.
    - The ``sleeper`` parameter exists so tests can substitute a no-op
      and run instantly.
    - ``label`` is a human-readable identifier (e.g. ``"AAPL/Model 1"``)
      used in log lines.
    """
    # Resolve the sleeper at call time so a test that monkeypatches
    # ``time.sleep`` actually affects the backoff.
    if sleeper is None:
        sleeper = time.sleep

    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not is_transient_error(exc):
                # Genuine bug or model-side error – do not retry.
                raise
            if attempt >= attempts:
                if logger is not None:
                    logger.warning(
                        "[retry] %s exhausted after %d attempts: %s",
                        label,
                        attempt,
                        exc,
                    )
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if logger is not None:
                logger.info(
                    "[retry] %s attempt %d/%d failed (%s); waiting %.1fs",
                    label,
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
            sleeper(delay)

    # Defensive – the loop above always either returns or raises, but keep
    # mypy/typecheckers happy.
    if last_exc is not None:  # pragma: no cover - unreachable
        raise last_exc
    raise RuntimeError("with_retries exited without a result")  # pragma: no cover
