"""Webhook and scheduled runs carry the stop reason too.

Nobody is watching these. A scheduled job that dies at the turn limit sends
"No final response. Tools used: ..." and, without the footer, nothing at all
about why -- the #172 ambiguity, on the one path where no user was there to
notice the run was short.
"""

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.claude.sdk_integration import ClaudeResponse
from src.events.bus import EventBus
from src.events.handlers import AgentHandler
from src.events.types import AgentResponseEvent, ScheduledEvent, WebhookEvent


def _stopped(**kwargs: Any) -> ClaudeResponse:
    defaults: Dict[str, Any] = {
        "content": "No final response. Tools used: Bash, Read",
        "session_id": "s1",
        "cost": 0.02,
        "duration_ms": 900,
        "num_turns": 10,
        "result_subtype": "error_max_turns",
        "terminal_reason": "max_turns",
    }
    defaults.update(kwargs)
    return ClaudeResponse(**defaults)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def mock_claude() -> AsyncMock:
    mock = AsyncMock()
    mock.run_command = AsyncMock()
    return mock


@pytest.fixture
def handler(event_bus: EventBus, mock_claude: AsyncMock) -> AgentHandler:
    return AgentHandler(
        event_bus=event_bus,
        claude_integration=mock_claude,
        default_working_directory=Path("/tmp/test"),
        default_user_id=42,
    )


def _capture(event_bus: EventBus) -> List[AgentResponseEvent]:
    published: List[AgentResponseEvent] = []
    original = event_bus.publish

    async def capture(event: Any) -> None:
        if isinstance(event, AgentResponseEvent):
            published.append(event)
        await original(event)

    event_bus.publish = capture  # type: ignore[assignment]
    return published


class TestWebhookNotifications:
    async def test_a_truncated_webhook_run_says_so(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        mock_claude.run_command.return_value = _stopped()
        published = _capture(event_bus)

        await handler.handle_webhook(
            WebhookEvent(
                provider="github",
                event_type_name="push",
                payload={},
                delivery_id="d1",
            )
        )

        assert len(published) == 1
        assert "turn limit reached after 10 turns" in published[0].text

    async def test_a_clean_webhook_run_reads_as_before(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        mock_claude.run_command.return_value = _stopped(
            content="All good.", result_subtype="success", terminal_reason="completed"
        )
        published = _capture(event_bus)

        await handler.handle_webhook(
            WebhookEvent(
                provider="github",
                event_type_name="push",
                payload={},
                delivery_id="d2",
            )
        )

        assert [e.text for e in published] == ["All good."]

    async def test_blocked_calls_reach_the_notification(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        mock_claude.run_command.return_value = _stopped(
            content="Done.",
            result_subtype="success",
            terminal_reason="completed",
            permission_denials=[
                {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}}
            ],
        )
        published = _capture(event_bus)

        await handler.handle_webhook(
            WebhookEvent(
                provider="github",
                event_type_name="push",
                payload={},
                delivery_id="d3",
            )
        )

        assert "1 tool call was blocked" in published[0].text
        assert "/etc/hosts" in published[0].text


class TestScheduledNotifications:
    async def _drain(self, handler: AgentHandler) -> None:
        for task in list(handler._background_tasks):
            await task

    async def test_a_truncated_scheduled_run_says_so(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        mock_claude.run_command.return_value = _stopped()
        published = _capture(event_bus)

        await handler.handle_scheduled(
            ScheduledEvent(
                job_name="standup", prompt="summarise", target_chat_ids=[100, 200]
            )
        )
        await self._drain(handler)

        assert [e.chat_id for e in published] == [100, 200]
        for event in published:
            assert "turn limit reached after 10 turns" in event.text

    async def test_the_default_broadcast_carries_it_too(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        mock_claude.run_command.return_value = _stopped()
        published = _capture(event_bus)

        await handler.handle_scheduled(
            ScheduledEvent(job_name="nightly", prompt="run", target_chat_ids=[])
        )
        await self._drain(handler)

        assert len(published) == 1
        assert published[0].chat_id == 0
        assert "turn limit reached" in published[0].text

    async def test_nothing_is_published_when_there_is_nothing_to_say(
        self, event_bus: EventBus, mock_claude: AsyncMock, handler: AgentHandler
    ) -> None:
        """An empty reply from a clean run still publishes nothing."""
        mock_claude.run_command.return_value = _stopped(
            content="", result_subtype="success", terminal_reason="completed"
        )
        published = _capture(event_bus)

        await handler.handle_scheduled(
            ScheduledEvent(job_name="quiet", prompt="run", target_chat_ids=[100])
        )
        await self._drain(handler)

        assert published == []
