"""Retry helpers for transient Telegram transport failures."""

import asyncio
from typing import Awaitable, Callable, TypeVar

import structlog
from telegram.error import BadRequest, NetworkError

logger = structlog.get_logger()

T = TypeVar("T")


async def retry_telegram_network(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> T:
    """Run a Telegram request again after transient transport failures.

    ``BadRequest`` inherits from PTB's ``NetworkError`` even though it represents
    a permanent request problem (for example invalid HTML), so it must never be
    retried here. Callers can handle it separately with a formatting fallback.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must not be negative")

    for attempt in range(max_attempts):
        try:
            return await operation()
        except BadRequest:
            raise
        except NetworkError as exc:
            if attempt == max_attempts - 1:
                raise

            delay = base_delay * (2**attempt)
            logger.warning(
                "Transient Telegram request failure, retrying",
                attempt=attempt + 1,
                max_attempts=max_attempts,
                delay_seconds=delay,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(delay)

    raise AssertionError("Telegram retry loop exited unexpectedly")
