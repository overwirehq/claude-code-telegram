"""Tests for the MessageBuffer (multi-part paste detection)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.bot.utils.message_buffer import (
    BufferedResult,
    BufferKey,
    MessageBuffer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(user_id: int = 1, chat_id: int = 100, thread_id: int = 0) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.chat.id = chat_id
    update.message.message_thread_id = thread_id
    update.message.message_id = 42
    update.message.reply_text = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
    return update


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot_data = {}
    return ctx


KEY: BufferKey = (1, 100, None)
LONG_TEXT = "x" * 4096
SHORT_TEXT = "hello"


# ---------------------------------------------------------------------------
# is_likely_chunk
# ---------------------------------------------------------------------------


class TestIsLikelyChunk:
    def test_short_message(self) -> None:
        assert MessageBuffer.is_likely_chunk("hi", 4000) is False

    def test_exactly_threshold(self) -> None:
        assert MessageBuffer.is_likely_chunk("a" * 4000, 4000) is True

    def test_above_threshold(self) -> None:
        assert MessageBuffer.is_likely_chunk("a" * 4096, 4000) is True

    def test_below_threshold(self) -> None:
        assert MessageBuffer.is_likely_chunk("a" * 3999, 4000) is False

    def test_custom_threshold(self) -> None:
        assert MessageBuffer.is_likely_chunk("a" * 3000, 3000) is True


# ---------------------------------------------------------------------------
# add_chunk — first chunk
# ---------------------------------------------------------------------------


class TestAddChunkFirstChunk:
    async def test_short_message_returns_result_immediately(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        update = _make_update()
        ctx = _make_context()

        result = await buf.add_chunk(KEY, SHORT_TEXT, update, ctx)

        assert result is not None
        assert isinstance(result, BufferedResult)
        assert result.combined_text == SHORT_TEXT
        assert result.chunk_count == 1
        assert result.first_update is update
        # No entry in buffer
        assert not buf.has_buffer(KEY)

    async def test_long_chunk_returns_none(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        update = _make_update()
        ctx = _make_context()

        result = await buf.add_chunk(KEY, LONG_TEXT, update, ctx)

        assert result is None
        assert buf.has_buffer(KEY)

    async def test_long_chunk_sends_status_message(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        update = _make_update()
        ctx = _make_context()

        await buf.add_chunk(KEY, LONG_TEXT, update, ctx)

        update.message.reply_text.assert_called_once_with("Receiving message\u2026")


# ---------------------------------------------------------------------------
# add_chunk — subsequent chunks
# ---------------------------------------------------------------------------


class TestAddChunkSubsequent:
    async def test_second_long_chunk_stays_buffered(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        u1, u2 = _make_update(), _make_update()
        c1, c2 = _make_context(), _make_context()

        await buf.add_chunk(KEY, LONG_TEXT, u1, c1)
        result = await buf.add_chunk(KEY, LONG_TEXT, u2, c2)

        assert result is None
        assert buf.has_buffer(KEY)

    async def test_short_tail_flushes_immediately(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        u1, u2 = _make_update(), _make_update()
        c1, c2 = _make_context(), _make_context()

        await buf.add_chunk(KEY, LONG_TEXT, u1, c1)
        result = await buf.add_chunk(KEY, SHORT_TEXT, u2, c2)

        assert result is not None
        assert result.combined_text == LONG_TEXT + SHORT_TEXT
        assert result.chunk_count == 2
        assert result.first_update is u1
        assert result.last_context is c2
        # Buffer consumed
        assert not buf.has_buffer(KEY)


# ---------------------------------------------------------------------------
# BufferedResult — concatenation semantics
# ---------------------------------------------------------------------------


class TestBufferedResult:
    async def test_no_delimiter_between_chunks(self) -> None:
        """Telegram splits at char boundaries — join with empty string."""
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        u1, u2, u3 = _make_update(), _make_update(), _make_update()
        c1, c2, c3 = _make_context(), _make_context(), _make_context()

        await buf.add_chunk(KEY, "AAAA" * 1000, u1, c1)
        await buf.add_chunk(KEY, "BBBB" * 1000, u2, c2)
        result = await buf.add_chunk(KEY, "CC", u3, c3)

        assert result is not None
        assert result.combined_text == "AAAA" * 1000 + "BBBB" * 1000 + "CC"
        assert result.chunk_count == 3


# ---------------------------------------------------------------------------
# Timer flush
# ---------------------------------------------------------------------------


class TestTimerFlush:
    async def test_timer_fires_calls_on_flush(self) -> None:
        flush_called: list[tuple[BufferKey, BufferedResult]] = []

        async def _on_flush(key: BufferKey, result: BufferedResult) -> None:
            flush_called.append((key, result))

        buf = MessageBuffer(chunk_timeout=0.1, chunk_threshold=4000, on_flush=_on_flush)
        update = _make_update()
        ctx = _make_context()

        await buf.add_chunk(KEY, LONG_TEXT, update, ctx)
        # Wait for timer + a margin
        await asyncio.sleep(0.25)

        assert len(flush_called) == 1
        key, result = flush_called[0]
        assert key == KEY
        assert result.combined_text == LONG_TEXT
        assert result.chunk_count == 1
        assert not buf.has_buffer(KEY)

    async def test_timer_resets_on_new_chunk(self) -> None:
        flush_called: list[tuple[BufferKey, BufferedResult]] = []

        async def _on_flush(key: BufferKey, result: BufferedResult) -> None:
            flush_called.append((key, result))

        buf = MessageBuffer(
            chunk_timeout=0.15, chunk_threshold=4000, on_flush=_on_flush
        )
        u1, u2 = _make_update(), _make_update()
        c1, c2 = _make_context(), _make_context()

        await buf.add_chunk(KEY, LONG_TEXT, u1, c1)
        await asyncio.sleep(0.08)  # Before first timer fires
        await buf.add_chunk(KEY, LONG_TEXT, u2, c2)
        await asyncio.sleep(0.08)  # First timer would have fired if not reset
        assert len(flush_called) == 0  # Still buffered
        await asyncio.sleep(0.12)  # Now second timer fires

        assert len(flush_called) == 1
        assert flush_called[0][1].combined_text == LONG_TEXT + LONG_TEXT
        assert flush_called[0][1].chunk_count == 2


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestCancel:
    async def test_cancel_removes_entry(self) -> None:
        buf = MessageBuffer(chunk_timeout=1.0, chunk_threshold=4000)
        await buf.add_chunk(KEY, LONG_TEXT, _make_update(), _make_context())

        result = buf.cancel(KEY)

        assert result is not None
        assert result.combined_text == LONG_TEXT
        assert not buf.has_buffer(KEY)

    async def test_cancel_nonexistent_key(self) -> None:
        buf = MessageBuffer(chunk_timeout=1.0, chunk_threshold=4000)
        result = buf.cancel(KEY)
        assert result is None

    async def test_cancel_prevents_timer_flush(self) -> None:
        flush_called: list[BufferedResult] = []

        async def _on_flush(_key: BufferKey, result: BufferedResult) -> None:
            flush_called.append(result)

        buf = MessageBuffer(chunk_timeout=0.1, chunk_threshold=4000, on_flush=_on_flush)
        await buf.add_chunk(KEY, LONG_TEXT, _make_update(), _make_context())
        buf.cancel(KEY)

        await asyncio.sleep(0.2)
        assert len(flush_called) == 0


# ---------------------------------------------------------------------------
# Independence — different keys
# ---------------------------------------------------------------------------


class TestKeyIndependence:
    async def test_different_users_independent(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        key_a: BufferKey = (1, 100, None)
        key_b: BufferKey = (2, 100, None)

        await buf.add_chunk(key_a, LONG_TEXT, _make_update(user_id=1), _make_context())
        await buf.add_chunk(key_b, LONG_TEXT, _make_update(user_id=2), _make_context())

        assert buf.has_buffer(key_a)
        assert buf.has_buffer(key_b)

        # Flushing one doesn't affect the other
        result = buf.cancel(key_a)
        assert result is not None
        assert buf.has_buffer(key_b)

    async def test_different_threads_independent(self) -> None:
        buf = MessageBuffer(chunk_timeout=0.5, chunk_threshold=4000)
        key_a: BufferKey = (1, 100, 10)
        key_b: BufferKey = (1, 100, 20)

        await buf.add_chunk(key_a, LONG_TEXT, _make_update(), _make_context())
        await buf.add_chunk(key_b, LONG_TEXT, _make_update(), _make_context())

        assert buf.has_buffer(key_a)
        assert buf.has_buffer(key_b)


# ---------------------------------------------------------------------------
# pending_keys
# ---------------------------------------------------------------------------


class TestPendingKeys:
    async def test_pending_keys_empty_initially(self) -> None:
        buf = MessageBuffer()
        assert buf.pending_keys == []

    async def test_pending_keys_after_buffering(self) -> None:
        buf = MessageBuffer(chunk_timeout=1.0, chunk_threshold=4000)
        await buf.add_chunk(KEY, LONG_TEXT, _make_update(), _make_context())
        assert KEY in buf.pending_keys
