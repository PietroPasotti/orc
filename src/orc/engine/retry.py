"""Retry utilities for the orc orchestrator.

Provides a :func:`retry` decorator and a :func:`retry_call` helper that wrap
functions with configurable exponential back-off retry logic.  Intended for
use with transient failures (network I/O, filesystem races).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable)


def retry(
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator that retries the wrapped function on specified exceptions.

    Parameters
    ----------
    max_attempts:
        Maximum number of total invocations (including the first).
        ``1`` means no retries.
    backoff_factor:
        Multiplier applied to the delay after each failure.
        With ``initial_delay=1`` and ``backoff_factor=2``, delays are
        1 s, 2 s, 4 s, …
    initial_delay:
        Seconds to wait before the first retry.
    exceptions:
        Exception types that trigger a retry.  Other exceptions propagate
        immediately.

    Examples
    --------
    ::

        @retry(max_attempts=3, exceptions=(httpx.TimeoutException,))
        def fetch_updates() -> list[dict]:
            return get_telegram_updates()
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        logger.warning(
                            "retry exhausted",
                            fn=fn.__name__,
                            attempts=attempt,
                            error=str(exc),
                        )
                        raise
                    logger.debug(
                        "retrying after failure",
                        fn=fn.__name__,
                        attempt=attempt,
                        delay=delay,
                        error=str(exc),
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

        return wrapper  # type: ignore[return-value]

    return decorator
