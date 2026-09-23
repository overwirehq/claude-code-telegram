"""Tests for transient Telegram request retries."""

from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, TimedOut

from src.bot.utils.telegram_retry import retry_telegram_network


async def test_retries_transient_network_failures() -> None:
    operation = AsyncMock(side_effect=[TimedOut("slow"), TimedOut("slow"), "sent"])
    sleep = AsyncMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("src.bot.utils.telegram_retry.asyncio.sleep", sleep)
        result = await retry_telegram_network(operation)

    assert result == "sent"
    assert operation.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]


async def test_does_not_retry_bad_request() -> None:
    operation = AsyncMock(side_effect=BadRequest("invalid HTML"))

    with pytest.raises(BadRequest, match="invalid HTML"):
        await retry_telegram_network(operation)

    operation.assert_awaited_once()


async def test_raises_after_transient_retry_budget_is_exhausted() -> None:
    operation = AsyncMock(side_effect=TimedOut("still unavailable"))
    sleep = AsyncMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("src.bot.utils.telegram_retry.asyncio.sleep", sleep)
        with pytest.raises(TimedOut, match="still unavailable"):
            await retry_telegram_network(operation)

    assert operation.await_count == 3
    assert sleep.await_count == 2
