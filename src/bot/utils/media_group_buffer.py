"""Buffer for Telegram media-group (album) photos.

When a user sends an album (text + N photos), Telegram delivers it as
N separate ``Update``s that share the same ``message.media_group_id``.
Only one of those messages carries the user's caption.  Without buffering,
the bot would fire N independent Claude requests — one per photo.

This module collects all photos sharing a ``media_group_id`` within a
short debounce window, then hands them off as a single payload via an
``on_flush`` callback.

Design constraints
------------------
* Unlike :class:`~src.bot.utils.message_buffer.MessageBuffer`, there is no
  length-based heuristic here — *any* photo with a ``media_group_id`` is
  buffered, because by definition such photos are part of an album.
* Albums always have 2+ items, so we flush exclusively via the debounce
  timer (no "short tail" early-flush path).
* The flush callback runs as an independent ``asyncio.Task`` so the
  handler that received the final chunk can return immediately.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# (user_id, chat_id, thread_id, media_group_id)
MediaGroupKey = Tuple[int, int, Optional[int], str]


@dataclass
class BufferedMediaGroup:
    """Payload returned when a media-group buffer is ready for processing."""

    photos: List[Any]  # telegram.PhotoSize — largest size per message
    caption: Optional[str]
    first_update: Any  # telegram.Update (the first one we saw)
    last_context: Any  # ContextTypes.DEFAULT_TYPE
    photo_count: int


@dataclass
class _MediaGroupEntry:
    """Internal mutable state for one pending media-group buffer."""

    photos: List[Any] = field(default_factory=list)
    caption: Optional[str] = None
    first_update: Any = None
    last_context: Any = None
    timer_task: Optional[asyncio.Task[None]] = None
    status_message: Any = None  # telegram.Message


FlushCallback = Callable[[MediaGroupKey, BufferedMediaGroup], Coroutine[Any, Any, None]]


class MediaGroupBuffer:
    """Per-album debounce buffer for Telegram photo groups.

    Parameters
    ----------
    flush_timeout:
        Seconds to wait after the last photo before flushing the buffer.
        Telegram typically delivers all photos in an album within a few
        hundred milliseconds; the default of ``1.0`` gives generous
        headroom while keeping perceived latency low.
    on_flush:
        Async callback invoked when the buffer flushes.
        Signature: ``async def on_flush(key, result) -> None``.
    """

    def __init__(
        self,
        flush_timeout: float = 1.0,
        on_flush: Optional[FlushCallback] = None,
    ) -> None:
        self._buffers: Dict[MediaGroupKey, _MediaGroupEntry] = {}
        self._flush_timeout = flush_timeout
        self._on_flush = on_flush

    # -- query ---------------------------------------------------------------

    @property
    def pending_keys(self) -> List[MediaGroupKey]:
        """Return keys that currently have buffered photos."""
        return list(self._buffers.keys())

    def has_buffer(self, key: MediaGroupKey) -> bool:
        """Return True if there is a pending buffer for *key*."""
        return key in self._buffers

    # -- mutate --------------------------------------------------------------

    async def add_photo(
        self,
        key: MediaGroupKey,
        photo: Any,
        caption: Optional[str],
        update: Any,
        context: Any,
    ) -> None:
        """Append a photo to the buffer and (re)start the debounce timer.

        Always returns ``None`` — albums are flushed exclusively via the
        timer, never inline.  The caller should return immediately after
        calling this method.
        """
        entry = self._buffers.get(key)

        if entry is None:
            entry = _MediaGroupEntry(
                photos=[photo],
                caption=caption,
                first_update=update,
                last_context=context,
            )
            self._buffers[key] = entry

            # Lightweight user feedback — the final response is still
            # anchored to the first update's message_id via ``first_update``.
            try:
                entry.status_message = await update.message.reply_text(
                    "Receiving album\u2026"
                )
            except Exception:
                logger.debug("Failed to send album status message")
        else:
            entry.photos.append(photo)
            entry.last_context = context
            # Captions can arrive on any message in the group (Telegram
            # places it on one of them, not always the first).  Keep the
            # first non-empty caption we see.
            if not entry.caption and caption:
                entry.caption = caption
            self._cancel_timer(entry)

        self._schedule_timer(key)

    def cancel(self, key: MediaGroupKey) -> Optional[BufferedMediaGroup]:
        """Cancel a pending buffer synchronously and return its contents."""
        entry = self._buffers.get(key)
        if entry is None:
            return None

        self._cancel_timer(entry)
        return self._pop_result(key)

    # -- internal ------------------------------------------------------------

    def _schedule_timer(self, key: MediaGroupKey) -> None:
        entry = self._buffers.get(key)
        if entry is None:
            return
        entry.timer_task = asyncio.create_task(self._timer_coro(key))

    @staticmethod
    def _cancel_timer(entry: _MediaGroupEntry) -> None:
        if entry.timer_task is not None and not entry.timer_task.done():
            entry.timer_task.cancel()
            entry.timer_task = None

    async def _timer_coro(self, key: MediaGroupKey) -> None:
        """Sleep for the debounce period, then flush."""
        try:
            await asyncio.sleep(self._flush_timeout)
        except asyncio.CancelledError:
            return

        result = self._pop_result(key)
        if result is None:
            return

        logger.info(
            "Media group buffer timer fired",
            user_id=key[0],
            media_group_id=key[3],
            photo_count=result.photo_count,
            has_caption=bool(result.caption),
        )

        if self._on_flush is not None:
            asyncio.create_task(self._on_flush(key, result))

    def _pop_result(self, key: MediaGroupKey) -> Optional[BufferedMediaGroup]:
        """Remove the entry for *key* and return a :class:`BufferedMediaGroup`."""
        entry = self._buffers.pop(key, None)
        if entry is None:
            return None

        self._cancel_timer(entry)

        # Best-effort cleanup of the status message.
        if entry.status_message is not None:
            asyncio.ensure_future(self._delete_message(entry.status_message))

        return BufferedMediaGroup(
            photos=list(entry.photos),
            caption=entry.caption,
            first_update=entry.first_update,
            last_context=entry.last_context,
            photo_count=len(entry.photos),
        )

    @staticmethod
    async def _delete_message(msg: Any) -> None:
        try:
            await msg.delete()
        except Exception:
            pass
