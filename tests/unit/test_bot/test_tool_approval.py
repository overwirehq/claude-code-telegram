"""Tests for interactive tool-approval (Allow/Deny) feature.

Covers:
- _make_tool_approval_callback sends a prompt and resolves once answered
- Timeout auto-denies (fail closed) and cleans up pending state
- _handle_tool_approval_callback routing (owner-only, double-answer, unknown id)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import ToolPermissionContext

from src.bot.orchestrator import MessageOrchestrator, PendingToolApproval
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
        interactive_tool_approval=True,
        interactive_tool_approval_tools=["Bash"],
    )


@pytest.fixture
def orchestrator(settings):
    deps: dict = {}
    return MessageOrchestrator(settings, deps)


def _make_bot():
    bot = AsyncMock()
    sent_message = AsyncMock()
    bot.send_message = AsyncMock(return_value=sent_message)
    return bot


# ---------------------------------------------------------------------------
# _make_tool_approval_callback
# ---------------------------------------------------------------------------


class TestMakeToolApprovalCallback:
    async def test_sends_prompt_and_grants_on_allow(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        task = asyncio.ensure_future(
            request_approval("Bash", {"command": "echo hi"}, ToolPermissionContext())
        )
        await asyncio.sleep(0)  # let it register + send the prompt

        bot.send_message.assert_awaited_once()
        assert len(orchestrator._pending_tool_approvals) == 1
        request_id = next(iter(orchestrator._pending_tool_approvals))
        pending = orchestrator._pending_tool_approvals[request_id]
        assert pending.user_id == 100

        pending.future.set_result(True)
        result = await task

        assert result is True
        assert request_id not in orchestrator._pending_tool_approvals

    async def test_denies_on_deny(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        task = asyncio.ensure_future(
            request_approval("Bash", {"command": "echo hi"}, ToolPermissionContext())
        )
        await asyncio.sleep(0)

        request_id = next(iter(orchestrator._pending_tool_approvals))
        orchestrator._pending_tool_approvals[request_id].future.set_result(False)

        assert await task is False

    async def test_uses_rich_permission_context(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )
        permission_context = ToolPermissionContext(
            tool_use_id="tool-use-123",
            agent_id="agent-7",
            blocked_path="/outside/<config>.py",
            decision_reason="Outside the approved directory",
            title="Claude wants to edit <config>.py",
            display_name="Edit file",
            description="Update the project configuration",
        )

        task = asyncio.ensure_future(
            request_approval("Edit", {"file_path": "/fallback.py"}, permission_context)
        )
        await asyncio.sleep(0)

        message = bot.send_message.await_args.kwargs["text"]
        assert "Claude wants to edit &lt;config&gt;.py" in message
        assert "Update the project configuration" in message
        assert "Blocked path: <code>/outside/&lt;config&gt;.py</code>" in message
        assert "Reason: Outside the approved directory" in message
        assert "Sub-agent: <code>agent-7</code>" in message
        assert "/fallback.py" not in message

        request_id = next(iter(orchestrator._pending_tool_approvals))
        assert len(request_id) == 12
        pending = orchestrator._pending_tool_approvals[request_id]
        assert pending.tool_use_id == "tool-use-123"
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].callback_data == (
            f"tapv:allow:{request_id}"
        )

        pending.future.set_result(True)
        assert await task is True

    async def test_repeated_tool_use_id_keeps_requests_independent(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )
        permission_context = ToolPermissionContext(tool_use_id="replayed-tool-use")

        first = asyncio.ensure_future(
            request_approval("Bash", {"command": "echo first"}, permission_context)
        )
        second = asyncio.ensure_future(
            request_approval("Bash", {"command": "echo second"}, permission_context)
        )
        await asyncio.sleep(0)

        assert len(orchestrator._pending_tool_approvals) == 2
        pending = list(orchestrator._pending_tool_approvals.values())
        assert all(item.tool_use_id == "replayed-tool-use" for item in pending)
        pending[0].future.set_result(True)
        pending[1].future.set_result(False)
        assert await first is True
        assert await second is False

    async def test_truncates_oversized_permission_context(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )
        oversized = "<&>" * 4000
        permission_context = ToolPermissionContext(
            title=oversized,
            description=oversized,
            blocked_path=oversized,
            decision_reason=oversized,
            agent_id=oversized,
        )

        task = asyncio.ensure_future(
            request_approval("Bash", {"command": oversized}, permission_context)
        )
        await asyncio.sleep(0)

        message = bot.send_message.await_args.kwargs["text"]
        assert len(message) < 4096
        assert "…" in message
        assert message.count("<b>") == message.count("</b>")
        assert message.count("<code>") == message.count("</code>")

        pending = next(iter(orchestrator._pending_tool_approvals.values()))
        pending.future.set_result(True)
        assert await task is True

    async def test_falls_back_to_tool_name_and_input_summary(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        task = asyncio.ensure_future(
            request_approval(
                "Bash",
                {"command": "echo fallback"},
                ToolPermissionContext(),
            )
        )
        await asyncio.sleep(0)

        message = bot.send_message.await_args.kwargs["text"]
        assert "Claude wants to run <b>Bash</b>" in message
        assert "<code>echo fallback</code>" in message

        pending = next(iter(orchestrator._pending_tool_approvals.values()))
        pending.future.set_result(False)
        assert await task is False

    async def test_uses_display_name_when_title_is_missing(self, orchestrator):
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        task = asyncio.ensure_future(
            request_approval(
                "Bash",
                {"command": "echo hi"},
                ToolPermissionContext(display_name="Run command"),
            )
        )
        await asyncio.sleep(0)

        message = bot.send_message.await_args.kwargs["text"]
        assert "Claude wants to run <b>Run command</b>" in message

        pending = next(iter(orchestrator._pending_tool_approvals.values()))
        pending.future.set_result(True)
        assert await task is True

    async def test_timeout_denies_and_cleans_up(self, orchestrator):
        """With no response, wait_for(timeout=0) times out immediately -> deny (default)."""
        orchestrator.settings.interactive_tool_approval_timeout_seconds = 0
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        result = await request_approval(
            "Bash", {"command": "echo hi"}, ToolPermissionContext()
        )

        assert result is False
        assert orchestrator._pending_tool_approvals == {}
        bot.send_message.return_value.edit_text.assert_awaited_once()
        edit_text_args = bot.send_message.return_value.edit_text.await_args
        assert "denied" in edit_text_args.args[0].lower()

    async def test_pending_entry_registered_before_send_message(self, orchestrator):
        """The request must be resolvable the instant send_message returns.

        Regression test: previously the pending entry was registered *after*
        send_message, so a click landing in that window found nothing,
        answered "Already handled.", and left the future unresolved until
        the full timeout elapsed. Registering first closes the window.
        """
        registered_before_send = False

        async def fake_send_message(**kwargs):
            nonlocal registered_before_send
            registered_before_send = len(orchestrator._pending_tool_approvals) == 1
            return AsyncMock()

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=fake_send_message)

        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        task = asyncio.ensure_future(
            request_approval("Bash", {"command": "echo hi"}, ToolPermissionContext())
        )
        await asyncio.sleep(0)

        assert registered_before_send is True

        request_id = next(iter(orchestrator._pending_tool_approvals))
        orchestrator._pending_tool_approvals[request_id].future.set_result(True)
        assert await task is True

    async def test_pending_entry_removed_if_send_message_fails(self, orchestrator):
        """A failed send must not leave a dangling pending entry."""
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=RuntimeError("network error"))

        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        with pytest.raises(RuntimeError):
            await request_approval(
                "Bash", {"command": "echo hi"}, ToolPermissionContext()
            )

        assert orchestrator._pending_tool_approvals == {}

    async def test_timeout_allows_when_configured(self, orchestrator):
        """timeout_action='allow' makes an unanswered request resolve to True."""
        orchestrator.settings.interactive_tool_approval_timeout_seconds = 0
        orchestrator.settings.interactive_tool_approval_timeout_action = "allow"
        bot = _make_bot()
        request_approval = orchestrator._make_tool_approval_callback(
            user_id=100, chat_id=555, bot=bot, message_thread_id=None
        )

        result = await request_approval(
            "Bash", {"command": "echo hi"}, ToolPermissionContext()
        )

        assert result is True
        assert orchestrator._pending_tool_approvals == {}
        edit_text_args = bot.send_message.return_value.edit_text.await_args
        assert "auto-allowed" in edit_text_args.args[0].lower()


# ---------------------------------------------------------------------------
# _handle_tool_approval_callback
# ---------------------------------------------------------------------------


class TestHandleToolApprovalCallback:
    def _query(self, data, user_id):
        query = AsyncMock()
        query.data = data
        query.from_user = MagicMock()
        query.from_user.id = user_id
        query.message = AsyncMock()
        query.message.text_html = "⚠️ Claude wants to run <b>Bash</b>"
        return query

    async def test_owner_can_allow(self, orchestrator):
        future: "asyncio.Future[bool]" = asyncio.get_event_loop().create_future()
        orchestrator._pending_tool_approvals["abc123"] = PendingToolApproval(
            user_id=100, future=future
        )

        query = self._query("tapv:allow:abc123", 100)
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_tool_approval_callback(update, context)

        assert future.result() is True
        query.answer.assert_awaited_once_with("Allowed", show_alert=False)

    async def test_owner_can_deny(self, orchestrator):
        future: "asyncio.Future[bool]" = asyncio.get_event_loop().create_future()
        orchestrator._pending_tool_approvals["abc123"] = PendingToolApproval(
            user_id=100, future=future
        )

        query = self._query("tapv:deny:abc123", 100)
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_tool_approval_callback(update, context)

        assert future.result() is False
        query.answer.assert_awaited_once_with("Denied", show_alert=False)

    async def test_non_owner_blocked(self, orchestrator):
        future: "asyncio.Future[bool]" = asyncio.get_event_loop().create_future()
        orchestrator._pending_tool_approvals["abc123"] = PendingToolApproval(
            user_id=100, future=future
        )

        query = self._query("tapv:allow:abc123", 999)
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_tool_approval_callback(update, context)

        assert not future.done()
        query.answer.assert_awaited_once_with(
            "Only the requesting user can respond.", show_alert=True
        )

    async def test_unknown_request_id(self, orchestrator):
        query = self._query("tapv:allow:does-not-exist", 100)
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_tool_approval_callback(update, context)

        query.answer.assert_awaited_once_with("Already handled.", show_alert=False)

    async def test_double_answer_is_noop(self, orchestrator):
        future: "asyncio.Future[bool]" = asyncio.get_event_loop().create_future()
        future.set_result(True)
        orchestrator._pending_tool_approvals["abc123"] = PendingToolApproval(
            user_id=100, future=future
        )

        query = self._query("tapv:deny:abc123", 100)
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.bot_data = {}

        await orchestrator._handle_tool_approval_callback(update, context)

        # First result stands -- not overwritten by the second (late) click
        assert future.result() is True
        query.answer.assert_awaited_once_with("Already handled.", show_alert=False)
