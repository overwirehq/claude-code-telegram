"""Buffer for Telegram message chunks from multi-part pastes.

When a user pastes text longer than 4096 characters, the Telegram client
silently splits it into multiple messages.  This module detects that pattern
(messages at or near the 4096-char limit arriving in quick succession) and
coalesces them into a single string before handing them off for processing.

Design constraints
------------------
* The bot's ``StopAwareUpdateProcessor`` serialises non-priority updates with
  a global ``asyncio.Lock``.  To allow the *next* chunk to enter the handler
  quickly, the handler must **return immediately** when buffering — no blocking
  on ``run_command()``.
* Once the debounce timer fires (or a short "tail" chunk is detected), the
  combined text is handed to a *flush callback* that runs as an independent
  ``asyncio.Task``, outside the sequential lock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# Type alias — keep it simple, avoid NamedTuple overhead.
BufferKey = Tuple[int, int, Optional[int]]  # (user_id, chat_id, thread_id)


@dataclass
class BufferedResult:
    """Payload returned when the buffer is ready for processing."""

    combined_text: str
    first_update: Any  # telegram.Update
    last_context: Any  # ContextTypes.DEFAULT_TYPE
    chunk_count: int


@dataclass
class _BufferEntry:
    """Internal mutable state for one pending buffer."""

    texts: List[str] = field(default_factory=list)
    first_update: Any = None
    last_context: Any = None
    timer_task: Optional[asyncio.Task[None]] = None
    status_message: Any = None  # telegram.Message


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

FlushCallback = Callable[[BufferKey, BufferedResult], Coroutine[Any, Any, None]]


class MessageBuffer:
    """Per-key debounce buffer for Telegram message chunks.

    Parameters
    ----------
    chunk_timeout:
        Seconds to wait after the last chunk before flushing.
    chunk_threshold:
        Minimum message length (chars) to consider a message a potential
        Telegram-split chunk.
    on_flush:
        Async callback invoked when the buffer flushes via timer.
        Signature: ``async def on_flush(key, result) -> None``.
    """

    def __init__(
        self,
        chunk_timeout: float = 0.5,
        chunk_threshold: int = 3000,
        on_flush: Optional[FlushCallback] = None,
    ) -> None:
        self._buffers: Dict[BufferKey, _BufferEntry] = {}
        self._chunk_timeout = chunk_timeout
        self._chunk_threshold = chunk_threshold
        self._on_flush = on_flush

    # -- query ---------------------------------------------------------------

    @property
    def pending_keys(self) -> List[BufferKey]:
        """Return keys that currently have buffered chunks."""
        return list(self._buffers.keys())

    def has_buffer(self, key: BufferKey) -> bool:
        """Return True if there is a pending buffer for *key*."""
        return key in self._buffers

    @staticmethod
    def is_likely_chunk(text: str, threshold: int) -> bool:
        """Return True if *text* looks like a Telegram-split chunk."""
        return len(text) >= threshold

    # -- mutate --------------------------------------------------------------

    async def add_chunk(
        self,
        key: BufferKey,
        text: str,
        update: Any,
        context: Any,
    ) -> Optional[BufferedResult]:
        """Append a message chunk to the buffer.

        Returns
        -------
        ``None``
            The chunk was buffered; caller should return immediately.
        ``BufferedResult``
            The buffer has been flushed (short tail chunk detected or
            the caller should process the combined text inline).
        """
        entry = self._buffers.get(key)

        if entry is not None:
            # ---- existing buffer: append ----------------------------------
            entry.texts.append(text)
            entry.last_context = context
            self._cancel_timer(entry)

            if self.is_likely_chunk(text, self._chunk_threshold):
                # Another full-size chunk — more may follow.
                self._schedule_timer(key)
                return None

            # Short chunk → likely the tail.  Flush immediately.
            return self._pop_result(key)

        # ---- no existing buffer -------------------------------------------
        if not self.is_likely_chunk(text, self._chunk_threshold):
            # Short message, nothing pending — nothing to buffer.
            return BufferedResult(
                combined_text=text,
                first_update=update,
                last_context=context,
                chunk_count=1,
            )

        # First full-size chunk — start buffering.
        entry = _BufferEntry(
            texts=[text],
            first_update=update,
            last_context=context,
        )
        self._buffers[key] = entry

        # Send a lightweight status message so the user sees feedback.
        try:
            entry.status_message = await update.message.reply_text(
                "Receiving message\u2026"
            )
        except Exception:
            logger.debug("Failed to send buffer status message")

        self._schedule_timer(key)
        return None

    def cancel(self, key: BufferKey) -> Optional[BufferedResult]:
        """Cancel a pending buffer synchronously.

        Returns the accumulated result (so the caller can decide whether
        to process or discard it), or ``None`` if nothing was buffered.
        """
        entry = self._buffers.get(key)
        if entry is None:
            return None

        self._cancel_timer(entry)
        result = self._pop_result(key)

        # Best-effort cleanup of the status message.
        if entry.status_message is not None:
            asyncio.ensure_future(self._delete_message(entry.status_message))

        return result

    # -- internal ------------------------------------------------------------

    def _schedule_timer(self, key: BufferKey) -> None:
        entry = self._buffers.get(key)
        if entry is None:
            return
        entry.timer_task = asyncio.create_task(self._timer_coro(key))

    @staticmethod
    def _cancel_timer(entry: _BufferEntry) -> None:
        if entry.timer_task is not None and not entry.timer_task.done():
            entry.timer_task.cancel()
            entry.timer_task = None

    async def _timer_coro(self, key: BufferKey) -> None:
        """Sleep for the debounce period, then flush."""
        try:
            await asyncio.sleep(self._chunk_timeout)
        except asyncio.CancelledError:
            return

        result = self._pop_result(key)
        if result is None:
            return  # entry was already consumed (e.g. by cancel)

        logger.info(
            "Chunk buffer timer fired",
            user_id=key[0],
            chunk_count=result.chunk_count,
            combined_length=len(result.combined_text),
        )

        if self._on_flush is not None:
            asyncio.create_task(self._on_flush(key, result))

    def _pop_result(self, key: BufferKey) -> Optional[BufferedResult]:
        """Remove the entry for *key* and return a ``BufferedResult``."""
        entry = self._buffers.pop(key, None)
        if entry is None:
            return None

        self._cancel_timer(entry)

        # Best-effort cleanup of the status message.
        if entry.status_message is not None:
            asyncio.ensure_future(self._delete_message(entry.status_message))

        return BufferedResult(
            combined_text="".join(entry.texts),
            first_update=entry.first_update,
            last_context=entry.last_context,
            chunk_count=len(entry.texts),
        )

    @staticmethod
    async def _delete_message(msg: Any) -> None:
        try:
            await msg.delete()
        except Exception:
            pass
