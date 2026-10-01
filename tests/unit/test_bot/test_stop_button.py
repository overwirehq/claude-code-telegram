"""Tests for the Stop button (interrupt) feature.

Covers:
- Stop button appears on progress message
- Stop callback fires interrupt event
- Non-owner cannot stop another user's request
- Stop after completion (graceful handling)
- Double-stop prevention
- SDK execute_command with interrupt_event triggers client.interrupt()
- Partial response preserved after interrupt
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from telegram import InlineKeyboardMarkup

from src.bot.orchestrator import ActiveRequest, MessageOrchestrator
from src.claude.sdk_integration import ClaudeResponse, ClaudeSDKManager
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path):
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        agentic_mode=True,
    )


@pytest.fixture
def orchestrator(settings):
    deps: dict = {}
    return MessageOrchestrator(settings, deps)


@pytest.fixture
def sdk_manager(tmp_path):
    config = Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        claude_timeout_seconds=5,
    )
    return ClaudeSDKManager(config)


# ---------------------------------------------------------------------------
# ActiveRequest / orchestrator unit tests
# ---------------------------------------------------------------------------


class TestActiveRequest:
    """Basic ActiveRequest dataclass behaviour."""

    def test_defaults(self):
        req = ActiveRequest(user_id=42)
        assert req.user_id == 42
        assert isinstance(req.interrupt_event, asyncio.Event)
        assert not req.interrupt_event.is_set()
        assert req.interrupted is False
        assert req.progress_msg is None


class TestStopCallback:
    """_handle_stop_callback routing logic."""

    async def test_owner_can_stop(self, orchestrator):
        """Clicking Stop fires the interrupt event."""
        event = asyncio.Event()
        progress_msg = AsyncMock()
        active = ActiveRequest(
            user_id=100, interrupt_event=event, progress_msg=progress_msg
        )
        orchestrator._active_requests[100] = active

        query = AsyncMock()
        query.data = "stop:100"
        query.from_user = MagicMock()
        query.from_user.id = 100

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_stop_callback(update, context)

        assert event.is_set()
        assert active.interrupted is True
        query.answer.assert_awaited_once_with("Stopping...", show_alert=False)
        progress_msg.edit_text.assert_awaited_once_with(
            "Stopping...", reply_markup=None
        )

    async def test_non_owner_blocked(self, orchestrator):
        """A different user cannot stop someone else's request."""
        event = asyncio.Event()
        active = ActiveRequest(
            user_id=100, interrupt_event=event, progress_msg=AsyncMock()
        )
        orchestrator._active_requests[100] = active

        query = AsyncMock()
        query.data = "stop:100"
        query.from_user = MagicMock()
        query.from_user.id = 999  # different user

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_stop_callback(update, context)

        assert not event.is_set()
        assert not active.interrupted
        query.answer.assert_awaited_once_with(
            "Only the requesting user can stop this.", show_alert=True
        )

    async def test_stop_after_completion(self, orchestrator):
        """Clicking Stop after request completed is handled gracefully."""
        query = AsyncMock()
        query.data = "stop:100"
        query.from_user = MagicMock()
        query.from_user.id = 100

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        # No active request registered
        await orchestrator._handle_stop_callback(update, context)

        query.answer.assert_awaited_once_with("Already completed.", show_alert=False)

    async def test_double_stop_prevention(self, orchestrator):
        """Second click shows 'Already stopping...' instead of re-firing."""
        event = asyncio.Event()
        active = ActiveRequest(
            user_id=100, interrupt_event=event, progress_msg=AsyncMock()
        )
        active.interrupted = True  # already stopped once
        orchestrator._active_requests[100] = active

        query = AsyncMock()
        query.data = "stop:100"
        query.from_user = MagicMock()
        query.from_user.id = 100

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_stop_callback(update, context)

        query.answer.assert_awaited_once_with("Already stopping...", show_alert=False)


class TestStopButtonOnProgress:
    """Verify the Stop button is attached to progress messages."""

    async def test_progress_message_has_stop_button(self, orchestrator, settings):
        """agentic_text sends progress_msg with Stop keyboard."""
        user_id = 42
        mock_response = ClaudeResponse(
            content="Done",
            session_id="s1",
            cost=0.01,
            duration_ms=100,
            num_turns=1,
        )

        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.message = AsyncMock()
        update.message.message_id = 1
        update.message.text = "test"
        update.message.chat = AsyncMock()
        update.message.chat.send_action = AsyncMock()

        progress_msg = AsyncMock()
        progress_msg.delete = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)
        update.effective_message = update.message

        context = MagicMock()
        context.user_data = {"current_directory": settings.approved_directory}
        context.bot_data = {
            "claude_integration": AsyncMock(),
            "rate_limiter": None,
            "audit_logger": None,
            "storage": None,
        }
        context.bot_data["claude_integration"].run_command = AsyncMock(
            return_value=mock_response
        )

        with patch(
            "src.bot.orchestrator.MessageOrchestrator._start_typing_heartbeat"
        ) as mock_hb:
            mock_task = AsyncMock()
            mock_task.cancel = MagicMock()
            mock_hb.return_value = mock_task

            with patch(
                "src.bot.orchestrator._update_working_directory_from_claude_response"
            ):
                with patch("src.bot.utils.formatting.ResponseFormatter") as MockFmt:
                    MockFmt.return_value.format_claude_response.return_value = []
                    await orchestrator.agentic_text(update, context)

        # First reply_text call should be the progress message with Stop button
        first_call = update.message.reply_text.call_args_list[0]
        assert first_call.args[0] == "Working..."
        reply_markup = first_call.kwargs.get("reply_markup")
        assert reply_markup is not None
        assert isinstance(reply_markup, InlineKeyboardMarkup)
        button = reply_markup.inline_keyboard[0][0]
        assert button.text == "Stop"
        assert button.callback_data == f"stop:{user_id}"

    async def test_active_request_cleaned_up_after_success(
        self, orchestrator, settings
    ):
        """_active_requests is cleared in the finally block."""
        user_id = 42
        mock_response = ClaudeResponse(
            content="Done",
            session_id="s1",
            cost=0.01,
            duration_ms=100,
            num_turns=1,
        )

        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.message = AsyncMock()
        update.message.message_id = 1
        update.message.text = "test"
        update.message.chat = AsyncMock()
        update.message.chat.send_action = AsyncMock()

        progress_msg = AsyncMock()
        progress_msg.delete = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)
        update.effective_message = update.message

        context = MagicMock()
        context.user_data = {"current_directory": settings.approved_directory}
        context.bot_data = {
            "claude_integration": AsyncMock(),
            "rate_limiter": None,
            "audit_logger": None,
            "storage": None,
        }
        context.bot_data["claude_integration"].run_command = AsyncMock(
            return_value=mock_response
        )

        with patch(
            "src.bot.orchestrator.MessageOrchestrator._start_typing_heartbeat"
        ) as mock_hb:
            mock_task = AsyncMock()
            mock_task.cancel = MagicMock()
            mock_hb.return_value = mock_task
            with patch(
                "src.bot.orchestrator._update_working_directory_from_claude_response"
            ):
                with patch("src.bot.utils.formatting.ResponseFormatter") as MockFmt:
                    MockFmt.return_value.format_claude_response.return_value = []
                    await orchestrator.agentic_text(update, context)

        assert user_id not in orchestrator._active_requests

    async def test_active_request_cleaned_up_after_error(self, orchestrator, settings):
        """_active_requests is cleared even when run_command raises."""
        user_id = 42

        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.message = AsyncMock()
        update.message.message_id = 1
        update.message.text = "test"
        update.message.chat = AsyncMock()
        update.message.chat.send_action = AsyncMock()

        progress_msg = AsyncMock()
        progress_msg.delete = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=progress_msg)
        update.effective_message = update.message

        context = MagicMock()
        context.user_data = {"current_directory": settings.approved_directory}
        context.bot_data = {
            "claude_integration": AsyncMock(),
            "rate_limiter": None,
            "audit_logger": None,
            "storage": None,
        }
        context.bot_data["claude_integration"].run_command = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        with patch(
            "src.bot.orchestrator.MessageOrchestrator._start_typing_heartbeat"
        ) as mock_hb:
            mock_task = AsyncMock()
            mock_task.cancel = MagicMock()
            mock_hb.return_value = mock_task
            with patch(
                "src.bot.orchestrator._format_error_message", return_value="err"
            ):
                await orchestrator.agentic_text(update, context)

        assert user_id not in orchestrator._active_requests


# ---------------------------------------------------------------------------
# SDK-level interrupt tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_parse_message():
    """Patch parse_message as identity so mocks can yield typed Message objects."""
    with patch("src.claude.sdk_integration.parse_message", side_effect=lambda x: x):
        yield


def _mock_client(*messages, delay: float = 0.0):
    """Create a mock ClaudeSDKClient that yields messages with optional delay."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.query = AsyncMock()
    client.interrupt = AsyncMock()

    async def receive_raw_messages():
        for msg in messages:
            if delay:
                await asyncio.sleep(delay)
            yield msg

    query_mock = AsyncMock()
    query_mock.receive_messages = receive_raw_messages
    client._query = query_mock

    return client


class TestSDKInterrupt:
    """Test interrupt_event cancels the run task in execute_command."""

    async def test_interrupt_event_cancels_task(self, sdk_manager, tmp_path):
        """Setting the interrupt_event should cancel the client task."""
        assistant_msg = AssistantMessage(
            content=[TextBlock(text="partial")],
            model="claude-sonnet-4-20250514",
        )
        result_msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.01,
            result="partial",
        )

        # First message arrives at t=0.05, second at t=0.5
        # Interrupt fires at t=0.15 (after first msg, during wait for second)
        client = _mock_client(assistant_msg, result_msg, delay=0.05)

        interrupt_event = asyncio.Event()

        async def set_interrupt_soon():
            await asyncio.sleep(0.08)
            interrupt_event.set()

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            asyncio.create_task(set_interrupt_soon())
            response = await sdk_manager.execute_command(
                prompt="test",
                working_directory=tmp_path,
                interrupt_event=interrupt_event,
            )

        assert response.interrupted is True
        # Partial content from assistant message (ResultMessage never arrived)
        assert response.content == "partial"

    async def test_no_interrupt_event_normal_flow(self, sdk_manager, tmp_path):
        """Without interrupt_event, response.interrupted should be False."""
        result_msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.01,
            result="done",
        )
        client = _mock_client(result_msg)

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            response = await sdk_manager.execute_command(
                prompt="test",
                working_directory=tmp_path,
            )

        assert response.interrupted is False
        assert response.content == "done"


class TestClaudeResponseInterruptedField:
    """Test the interrupted field on ClaudeResponse."""

    def test_default_false(self):
        resp = ClaudeResponse(
            content="x", session_id="s", cost=0.0, duration_ms=0, num_turns=1
        )
        assert resp.interrupted is False

    def test_explicit_true(self):
        resp = ClaudeResponse(
            content="x",
            session_id="s",
            cost=0.0,
            duration_ms=0,
            num_turns=1,
            interrupted=True,
        )
        assert resp.interrupted is True
