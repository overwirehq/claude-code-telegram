"""Regression tests for #244: scoped allow rules for guarded tools.

``ClaudeSDKManager.execute_command`` strips guarded tools (and tools gated
behind interactive approval) from ``allowed_tools`` so the CLI routes those
calls through ``can_use_tool`` instead of pre-approving them (#219). The
strip used to compare entries by exact name, so a scoped rule such as
``Bash(git:*)`` never matched ``Bash`` and was passed through to the CLI,
which pre-approved every call it matched. These tests pin that allow rules
are matched by the tool they name -- the part before ``(``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from src.claude.sdk_integration import ClaudeSDKManager
from src.config.settings import Settings


@pytest.fixture(autouse=True)
def _patch_parse_message():
    """Patch parse_message as identity so mocks can yield typed Message objects."""
    with patch("src.claude.sdk_integration.parse_message", side_effect=lambda x: x):
        yield


def _make_assistant_message(text="Test response"):
    """Create an AssistantMessage with proper structure for current SDK version."""
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="claude-sonnet-4-20250514",
    )


def _make_result_message(**kwargs):
    """Create a ResultMessage with sensible defaults."""
    defaults = {
        "subtype": "success",
        "duration_ms": 1000,
        "duration_api_ms": 800,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test-session",
        "total_cost_usd": 0.05,
        "result": "Success",
    }
    defaults.update(kwargs)
    return ResultMessage(**defaults)


def _mock_client(*messages):
    """Create a mock ClaudeSDKClient that yields the given messages."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def receive_raw_messages():
        for msg in messages:
            yield msg

    query_mock = AsyncMock()
    query_mock.receive_messages = receive_raw_messages
    client._query = query_mock

    return client


def _mock_client_factory(*messages, capture_options=None):
    """Create a factory that returns a mock client, optionally capturing options."""

    def factory(options):
        if capture_options is not None:
            capture_options.append(options)
        return _mock_client(*messages)

    return factory


async def _capture_options(manager, tmp_path):
    """Run execute_command against a mock client and return the options used."""
    captured_options = []
    mock_factory = _mock_client_factory(
        _make_assistant_message("ok"),
        _make_result_message(total_cost_usd=0.01),
        capture_options=captured_options,
    )
    with patch("src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory):
        await manager.execute_command(prompt="Test", working_directory=tmp_path)
    assert len(captured_options) == 1
    return captured_options[0]


def _config(tmp_path, **overrides):
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
        claude_timeout_seconds=2,
        **overrides,
    )


def _validator(tmp_path):
    validator = MagicMock()
    validator.validate_path = MagicMock(return_value=(True, tmp_path, None))
    return validator


class TestScopedAllowRules:
    """Scoped allow rules must not pre-approve guarded or gated tools."""

    async def test_scoped_rules_for_guarded_tools_are_stripped(self, tmp_path):
        """Bash(git:*), Read(//etc/**) and Write(*) name guarded tools."""
        manager = ClaudeSDKManager(
            _config(
                tmp_path,
                claude_allowed_tools=[
                    "Bash(git:*)",
                    "Read(//etc/**)",
                    "Write(*)",
                    "Grep",
                ],
            ),
            security_validator=_validator(tmp_path),
        )
        options = await _capture_options(manager, tmp_path)

        assert options.allowed_tools == ["Grep"]

    async def test_scoped_rule_for_unguarded_tool_is_kept(self, tmp_path):
        """A scoped rule for a tool the callback does not guard stays."""
        manager = ClaudeSDKManager(
            _config(
                tmp_path,
                claude_allowed_tools=["WebFetch(domain:example.com)", "Grep"],
            ),
            security_validator=_validator(tmp_path),
        )
        options = await _capture_options(manager, tmp_path)

        assert "WebFetch(domain:example.com)" in options.allowed_tools
        assert "Grep" in options.allowed_tools

    async def test_scoped_rules_for_approval_gated_tools_are_stripped(self, tmp_path):
        """The interactive approval strip matches by base tool name too."""
        manager = ClaudeSDKManager(
            _config(
                tmp_path,
                interactive_tool_approval=True,
                interactive_tool_approval_tools=["Bash", "Write"],
                claude_allowed_tools=["Bash(git:*)", "Write(*)", "Read"],
            )
        )
        options = await _capture_options(manager, tmp_path)

        assert options.allowed_tools == ["Read"]
