"""Tests for MediaGroupBuffer (Telegram photo-album buffering).

Covers:
- Basic buffering: first photo starts a buffer, subsequent ones append
- Timer fires exactly once and delivers all photos + caption
- Caption arriving on later messages is captured
- Multiple independent groups are keyed separately
- cancel() clears state and returns pending contents
- Status-message lifecycle (best-effort delete on flush/cancel)
"""

import asyncio
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.utils.media_group_buffer import (
    BufferedMediaGroup,
    MediaGroupBuffer,
    MediaGroupKey,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_update(photo_id: str = "p", with_reply: bool = True) -> MagicMock:
    """Build a minimal mock Update/Message with reply_text wired up."""
    update = MagicMock()
    update.message = MagicMock()
    if with_reply:
        status_msg = MagicMock()
        status_msg.delete = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=status_msg)
    else:
        update.message.reply_text = AsyncMock(side_effect=RuntimeError("no reply"))
    update._photo_id = photo_id  # just for test identity
    return update


def _make_photo(file_id: str) -> MagicMock:
    photo = MagicMock()
    photo.file_id = file_id
    return photo


# ---------------------------------------------------------------------------
# add_photo — basic behavior
# ---------------------------------------------------------------------------


class TestAddPhoto:
    async def test_first_photo_starts_buffer(self):
        """First photo initialises an entry and sends status message."""
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key: MediaGroupKey = (1, 2, None, "mg-A")
        update = _make_update()

        await buf.add_photo(key, _make_photo("f1"), "cap", update, object())

        assert buf.has_buffer(key)
        assert buf.pending_keys == [key]
        update.message.reply_text.assert_awaited_once()

    async def test_subsequent_photo_appends(self):
        """A second photo on the same key extends the buffer, not new status msg."""
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key: MediaGroupKey = (1, 2, None, "mg-A")
        u1, u2 = _make_update(), _make_update()

        await buf.add_photo(key, _make_photo("f1"), "cap", u1, object())
        await buf.add_photo(key, _make_photo("f2"), None, u2, object())

        # Only the FIRST update sends a status message.
        u1.message.reply_text.assert_awaited_once()
        u2.message.reply_text.assert_not_awaited()
        assert buf.has_buffer(key)

    async def test_different_keys_separate_buffers(self):
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key_a: MediaGroupKey = (1, 2, None, "mg-A")
        key_b: MediaGroupKey = (1, 2, None, "mg-B")

        await buf.add_photo(key_a, _make_photo("a1"), "capA", _make_update(), object())
        await buf.add_photo(key_b, _make_photo("b1"), "capB", _make_update(), object())

        assert set(buf.pending_keys) == {key_a, key_b}

    async def test_returns_none_always(self):
        """add_photo is purely buffering — never returns a result inline."""
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key: MediaGroupKey = (1, 2, None, "mg")
        result = await buf.add_photo(
            key, _make_photo("f1"), None, _make_update(), object()
        )
        assert result is None


# ---------------------------------------------------------------------------
# Timer flush
# ---------------------------------------------------------------------------


class TestFlush:
    async def test_timer_fires_after_timeout(self):
        """After flush_timeout elapses, on_flush is invoked exactly once."""
        flushes: List[Tuple[MediaGroupKey, BufferedMediaGroup]] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append((key, result))
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), "cap", _make_update(), object())
        await buf.add_photo(key, _make_photo("f2"), None, _make_update(), object())

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        # Allow the flush task scheduled via create_task() to run.
        await asyncio.sleep(0.01)

        assert len(flushes) == 1
        flushed_key, result = flushes[0]
        assert flushed_key == key
        assert result.photo_count == 2
        assert result.caption == "cap"
        assert [p.file_id for p in result.photos] == ["f1", "f2"]
        assert not buf.has_buffer(key)

    async def test_timer_reset_on_new_photo(self):
        """Each new photo restarts the debounce timer so late additions are included."""
        flushes: List[BufferedMediaGroup] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append(result)
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.15, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), "cap", _make_update(), object())
        # Wait less than the full timeout, then add another photo.
        await asyncio.sleep(0.08)
        await buf.add_photo(key, _make_photo("f2"), None, _make_update(), object())
        await asyncio.sleep(0.08)  # still within reset timeout
        await buf.add_photo(key, _make_photo("f3"), None, _make_update(), object())

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        await asyncio.sleep(0.01)

        assert len(flushes) == 1
        assert flushes[0].photo_count == 3

    async def test_caption_captured_on_later_photo(self):
        """If caption only arrives on a non-first message, it's still captured."""
        flushes: List[BufferedMediaGroup] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append(result)
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), None, _make_update(), object())
        await buf.add_photo(
            key, _make_photo("f2"), "late caption", _make_update(), object()
        )

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        assert flushes[0].caption == "late caption"

    async def test_first_non_empty_caption_wins(self):
        """If multiple messages carry captions, the first one is kept."""
        flushes: List[BufferedMediaGroup] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append(result)
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), "first", _make_update(), object())
        await buf.add_photo(key, _make_photo("f2"), "second", _make_update(), object())

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        assert flushes[0].caption == "first"

    async def test_first_update_preserved(self):
        """The first update is preserved so replies anchor to the album origin."""
        flushes: List[BufferedMediaGroup] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append(result)
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")
        first = _make_update("first")
        second = _make_update("second")

        await buf.add_photo(key, _make_photo("f1"), "cap", first, object())
        await buf.add_photo(key, _make_photo("f2"), None, second, object())

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        assert flushes[0].first_update is first

    async def test_last_context_preserved(self):
        """last_context reflects the most recent update's context."""
        flushes: List[BufferedMediaGroup] = []
        flush_done = asyncio.Event()

        async def _on_flush(key, result):
            flushes.append(result)
            flush_done.set()

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")
        ctx1 = object()
        ctx2 = object()

        await buf.add_photo(key, _make_photo("f1"), "cap", _make_update(), ctx1)
        await buf.add_photo(key, _make_photo("f2"), None, _make_update(), ctx2)

        await asyncio.wait_for(flush_done.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        assert flushes[0].last_context is ctx2


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------


class TestCancel:
    async def test_cancel_returns_pending_contents(self):
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), "cap", _make_update(), object())
        await buf.add_photo(key, _make_photo("f2"), None, _make_update(), object())

        result = buf.cancel(key)

        assert result is not None
        assert result.photo_count == 2
        assert result.caption == "cap"
        assert not buf.has_buffer(key)

    async def test_cancel_prevents_flush(self):
        """Once cancelled, the debounce timer must not invoke on_flush."""
        flushes: List[BufferedMediaGroup] = []

        async def _on_flush(key, result):
            flushes.append(result)

        buf = MediaGroupBuffer(flush_timeout=0.05, on_flush=_on_flush)
        key: MediaGroupKey = (1, 2, None, "mg")

        await buf.add_photo(key, _make_photo("f1"), "cap", _make_update(), object())
        buf.cancel(key)

        # Wait well past the flush timeout.
        await asyncio.sleep(0.15)
        assert flushes == []

    async def test_cancel_missing_key(self):
        buf = MediaGroupBuffer(flush_timeout=5.0)
        assert buf.cancel((1, 2, None, "nope")) is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_reply_text_failure_does_not_break_buffering(self):
        """A failure sending the status message is swallowed silently."""
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key: MediaGroupKey = (1, 2, None, "mg")
        update = _make_update(with_reply=False)

        # Must not raise — status message send failure is non-fatal.
        await buf.add_photo(key, _make_photo("f1"), "cap", update, object())
        assert buf.has_buffer(key)

    async def test_thread_id_in_key_disambiguates(self):
        """Same media_group_id in different threads must not collide."""
        buf = MediaGroupBuffer(flush_timeout=5.0)
        key_thread_a: MediaGroupKey = (1, 2, 10, "mg")
        key_thread_b: MediaGroupKey = (1, 2, 20, "mg")

        await buf.add_photo(
            key_thread_a, _make_photo("a"), "cA", _make_update(), object()
        )
        await buf.add_photo(
            key_thread_b, _make_photo("b"), "cB", _make_update(), object()
        )

        assert len(buf.pending_keys) == 2


# Enable asyncio for all tests in this module.
pytestmark = pytest.mark.asyncio
