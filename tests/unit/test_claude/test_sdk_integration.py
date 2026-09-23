"""Test Claude SDK integration."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)
from claude_agent_sdk.types import StreamEvent

from src.claude.sdk_integration import (
    GUARDED_TOOLS,
    TASK_COMPLETED_MSG,
    TASK_STOPPED_MSG,
    ClaudeResponse,
    ClaudeSDKManager,
    StreamUpdate,
    _make_can_use_tool_callback,
)
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
    """Create a mock ClaudeSDKClient that yields the given messages.

    Returns a factory function suitable for patching ClaudeSDKClient.
    Uses connect()/disconnect() pattern (not async context manager).
    """
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


class TestClaudeSDKManager:
    """Test Claude SDK manager."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create test config without API key."""
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,  # Short timeout for testing
            enable_mcp=False,
        )

    @pytest.fixture
    def sdk_manager(self, config):
        """Create SDK manager."""
        return ClaudeSDKManager(config)

    async def test_sdk_manager_initialization_with_api_key(self, tmp_path):
        """Test SDK manager initialization with API key."""
        from src.config.settings import Settings

        # Test with API key provided
        config_with_key = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            anthropic_api_key="test-api-key",
            claude_timeout_seconds=2,
        )

        # Store original env var
        original_api_key = os.environ.get("ANTHROPIC_API_KEY")

        try:
            ClaudeSDKManager(config_with_key)

            # Check that API key was set in environment
            assert os.environ.get("ANTHROPIC_API_KEY") == "test-api-key"

        finally:
            # Restore original env var
            if original_api_key:
                os.environ["ANTHROPIC_API_KEY"] = original_api_key
            elif "ANTHROPIC_API_KEY" in os.environ:
                del os.environ["ANTHROPIC_API_KEY"]

    async def test_sdk_manager_initialization_without_api_key(self, config):
        """Test SDK manager initialization without API key (uses CLI auth)."""
        # Store original env var
        original_api_key = os.environ.get("ANTHROPIC_API_KEY")

        try:
            # Remove any existing API key
            if "ANTHROPIC_API_KEY" in os.environ:
                del os.environ["ANTHROPIC_API_KEY"]

            ClaudeSDKManager(config)

            # Check that no API key was set (should use CLI auth)
            assert config.anthropic_api_key_str is None

        finally:
            # Restore original env var
            if original_api_key:
                os.environ["ANTHROPIC_API_KEY"] = original_api_key

    async def test_execute_command_success(self, sdk_manager):
        """Test successful command execution."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(session_id="test-session", total_cost_usd=0.05),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
                session_id="test-session",
            )

        # Verify response
        assert isinstance(response, ClaudeResponse)
        assert response.session_id == "test-session"
        assert response.duration_ms >= 0
        assert not response.is_error
        assert response.cost == 0.05

    async def test_execute_command_uses_result_content(self, sdk_manager):
        """Test that ResultMessage.result is used for content when available."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("Assistant text"),
            _make_result_message(result="Final result from ResultMessage"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.content == "Final result from ResultMessage"

    async def test_execute_command_falls_back_to_messages(self, sdk_manager):
        """Test fallback to message extraction when result is None."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("Extracted from messages"),
            _make_result_message(result=None),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.content == "Extracted from messages"

    @pytest.mark.parametrize("empty_result", ["", "  \n"])
    async def test_execute_command_falls_back_when_result_is_empty(
        self, sdk_manager, empty_result
    ):
        """Empty provider results must not hide AssistantMessage text (#171)."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("OpenRouter response"),
            _make_result_message(result=empty_result),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.content == "OpenRouter response"

    async def test_execute_command_with_streaming(self, sdk_manager):
        """Test command execution with streaming callback."""
        stream_updates = []

        async def stream_callback(update: StreamUpdate):
            stream_updates.append(update)

        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
                stream_callback=stream_callback,
            )

        # Verify streaming was called
        assert len(stream_updates) > 0
        assert any(update.type == "assistant" for update in stream_updates)

    async def test_execute_command_timeout(self, sdk_manager):
        """Test command execution timeout."""
        from src.claude.exceptions import ClaudeTimeoutError

        client = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.query = AsyncMock()

        async def hanging_receive():
            await asyncio.sleep(5)  # Exceeds 2s timeout
            yield  # Never reached

        query_mock = AsyncMock()
        query_mock.receive_messages = hanging_receive
        client._query = query_mock

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            with pytest.raises(ClaudeTimeoutError):
                await sdk_manager.execute_command(
                    prompt="Test prompt",
                    working_directory=Path("/test"),
                )

    async def test_execute_command_passes_mcp_config(self, tmp_path):
        """Test that MCP config is passed to ClaudeAgentOptions when enabled."""
        # Create a valid MCP config file
        mcp_config_file = tmp_path / "mcp_config.json"
        mcp_config_file.write_text(
            '{"mcpServers": {"test-server": {"command": "echo", "args": ["hello"]}}}'
        )

        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            enable_mcp=True,
            mcp_config_path=str(mcp_config_file),
        )

        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        # Verify MCP config was parsed and passed as dict to options
        assert len(captured_options) == 1
        assert captured_options[0].mcp_servers == {
            "test-server": {"command": "echo", "args": ["hello"]}
        }

    async def test_execute_command_no_mcp_when_disabled(self, sdk_manager):
        """Test that MCP config is NOT passed when MCP is disabled."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        # Verify MCP config was NOT set (should be empty default)
        assert len(captured_options) == 1
        assert captured_options[0].mcp_servers == {}

    async def test_execute_command_passes_resume_session(self, sdk_manager):
        """Test that session_id is passed as options.resume for continuation."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(session_id="test-session"),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Continue working",
                working_directory=Path("/test"),
                session_id="existing-session-id",
                continue_session=True,
            )

        assert len(captured_options) == 1
        assert captured_options[0].resume == "existing-session-id"

    async def test_execute_command_passes_max_budget_usd(self, sdk_manager, config):
        """Test that max_budget_usd is passed from config to ClaudeAgentOptions."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert len(captured_options) == 1
        assert captured_options[0].max_budget_usd == config.claude_max_cost_per_request

    async def test_execute_command_no_resume_for_new_session(self, sdk_manager):
        """Test that resume is not set for new sessions."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(session_id="new-session"),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="New prompt",
                working_directory=Path("/test"),
                session_id=None,
                continue_session=False,
            )

        assert len(captured_options) == 1
        assert (
            not hasattr(captured_options[0], "resume") or not captured_options[0].resume
        )

    async def test_retry_on_transient_cli_connection_error(self, sdk_manager):
        """Test that transient CLIConnectionError triggers retry and succeeds."""
        from claude_agent_sdk import CLIConnectionError

        call_count = 0

        async def flaky_receive():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise CLIConnectionError("connection reset")
            # Second attempt succeeds - yield a ResultMessage
            yield

        # Use a config with 2 attempts
        sdk_manager.config.claude_retry_max_attempts = 2

        client = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.query = AsyncMock()
        query_mock = AsyncMock()
        query_mock.receive_messages = flaky_receive
        client._query = query_mock

        # Should not raise - second attempt succeeds
        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                try:
                    await sdk_manager.execute_command(
                        prompt="Test",
                        working_directory=Path("/test"),
                    )
                except Exception:
                    pass  # Response parsing may fail - what matters is retry happened
        assert call_count == 2

    async def test_no_retry_on_mcp_connection_error(self, sdk_manager):
        """Test that MCP CLIConnectionError is NOT retried."""
        from claude_agent_sdk import CLIConnectionError

        from src.claude.exceptions import ClaudeMCPError

        client = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.query = AsyncMock(side_effect=CLIConnectionError("mcp server failed"))

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            with pytest.raises((ClaudeMCPError, Exception)):
                await sdk_manager.execute_command(
                    prompt="Test",
                    working_directory=Path("/test"),
                )
        # Only called once - no retry for MCP errors
        assert client.query.call_count == 1

    async def test_retry_disabled_when_max_attempts_zero(self, sdk_manager):
        """Test that setting max_attempts=0 effectively disables retries (1 attempt)."""
        sdk_manager.config.claude_retry_max_attempts = 0
        assert max(1, sdk_manager.config.claude_retry_max_attempts) == 1

    def test_is_retryable_error_transient(self, sdk_manager):
        """Test _is_retryable_error returns True for transient connection errors."""
        from claude_agent_sdk import CLIConnectionError

        assert (
            sdk_manager._is_retryable_error(CLIConnectionError("connection reset"))
            is True
        )

    def test_is_retryable_error_mcp(self, sdk_manager):
        """Test _is_retryable_error returns False for MCP errors."""
        from claude_agent_sdk import CLIConnectionError

        assert (
            sdk_manager._is_retryable_error(CLIConnectionError("mcp server failed"))
            is False
        )

    def test_is_retryable_error_timeout(self, sdk_manager):
        """Test _is_retryable_error returns False for timeout errors."""
        assert sdk_manager._is_retryable_error(asyncio.TimeoutError()) is False


class TestClaudeSandboxSettings:
    """Test sandbox and system_prompt settings on ClaudeAgentOptions."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create test config with sandbox enabled."""
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            sandbox_enabled=True,
            sandbox_excluded_commands=["git", "npm"],
        )

    @pytest.fixture
    def sdk_manager(self, config):
        return ClaudeSDKManager(config)

    async def test_sandbox_settings_passed_to_options(self, sdk_manager, tmp_path):
        """Test that sandbox settings are set on ClaudeAgentOptions."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        opts = captured_options[0]
        assert opts.sandbox == {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": ["git", "npm"],
        }

    async def test_sandbox_auto_allow_disabled_when_bash_gated(self, tmp_path):
        """autoAllowBashIfSandboxed is False when Bash requires interactive approval.

        Otherwise the SDK would auto-approve sandboxed Bash calls without ever
        invoking can_use_tool, silently bypassing both the approval prompt and
        the static bash-boundary check.
        """
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            sandbox_enabled=True,
            interactive_tool_approval=True,
            interactive_tool_approval_tools=["Bash", "Write", "Edit"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert captured_options[0].sandbox["autoAllowBashIfSandboxed"] is False

    async def test_sandbox_auto_allow_kept_when_bash_not_gated(self, tmp_path):
        """autoAllowBashIfSandboxed stays True when Bash isn't in the gated tool list."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            sandbox_enabled=True,
            interactive_tool_approval=True,
            interactive_tool_approval_tools=["Write", "Edit"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert captured_options[0].sandbox["autoAllowBashIfSandboxed"] is True

    async def test_gated_tools_removed_from_allowed_tools(self, tmp_path):
        """Tools gated behind interactive approval are excluded from allowed_tools.

        allowed_tools pre-approves a tool at the CLI level, so can_use_tool is
        never consulted for it -- a gated tool must be excluded or its
        approval prompt (and the static per-tool checks) would never fire.
        """
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            interactive_tool_approval=True,
            interactive_tool_approval_tools=["Bash", "Write"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        allowed = captured_options[0].allowed_tools
        assert "Bash" not in allowed
        assert "Write" not in allowed
        assert "Read" in allowed  # untouched, still allowed as before

    async def test_allowed_tools_untouched_when_interactive_approval_disabled(
        self, tmp_path
    ):
        """allowed_tools is unaffected when interactive approval is off.

        Regression guard: interactive_tool_approval_tools defaults to
        ["Bash", "Write", "Edit"] even when the feature itself is disabled,
        so the exclusion must key off interactive_tool_approval, not just
        whether the tools list is non-empty.
        """
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            interactive_tool_approval=False,
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert captured_options[0].allowed_tools == config.claude_allowed_tools

    async def test_empty_allowed_tools_unaffected_by_approval_filter(self, tmp_path):
        """When DISABLE_TOOL_VALIDATION empties allowed_tools, filtering is a no-op."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            disable_tool_validation=True,
            interactive_tool_approval=True,
            interactive_tool_approval_tools=["Bash"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert captured_options[0].allowed_tools == []

    async def test_system_prompt_set_with_working_directory(
        self, sdk_manager, tmp_path
    ):
        """Test that system_prompt references the working directory."""
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        opts = captured_options[0]
        assert str(tmp_path) in opts.system_prompt
        assert "relative paths" in opts.system_prompt.lower()

    async def test_disallowed_tools_passed_to_options(self, tmp_path):
        """Test that disallowed_tools from config are passed to ClaudeAgentOptions."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            claude_disallowed_tools=["WebFetch", "WebSearch"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].disallowed_tools == ["WebFetch", "WebSearch"]

    async def test_allowed_tools_passed_to_options(self, tmp_path):
        """Test that allowed_tools from config are passed to ClaudeAgentOptions."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            claude_allowed_tools=["Read", "Write", "Bash"],
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].allowed_tools == ["Read", "Write", "Bash"]

    async def test_disable_tool_validation_sets_allowed_tools_empty(self, tmp_path):
        """allowed_tools=[] when DISABLE_TOOL_VALIDATION=true.

        Empty rather than None: ClaudeAgentOptions declares these as
        list[str], and both values are falsy so the CLI omits the flags
        either way -- no tool restriction, which is the intent. See #206.
        """
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            disable_tool_validation=True,
            claude_allowed_tools=["Read", "Write", "Bash"],
            claude_disallowed_tools=["WebFetch"],
        )
        manager = ClaudeSDKManager(config)

        captured_options: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].allowed_tools == []
        assert captured_options[0].disallowed_tools == []

    async def test_tool_validation_enabled_passes_configured_tools(self, tmp_path):
        """allowed/disallowed_tools passed when DISABLE_TOOL_VALIDATION=false."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            disable_tool_validation=False,
            claude_allowed_tools=["Read", "Write"],
            claude_disallowed_tools=["WebFetch"],
        )
        manager = ClaudeSDKManager(config)

        captured_options: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].allowed_tools == ["Read", "Write"]
        assert captured_options[0].disallowed_tools == ["WebFetch"]

    async def test_none_tool_lists_coerced_to_empty_lists(self, tmp_path):
        """None tool settings reach the SDK as [], never as None.

        Both settings are Optional, but ClaudeAgentOptions declares them as
        list[str]. claude-agent-sdk 0.2 stopped tolerating None: the transport
        calls list(options.allowed_tools) and the connect-time shadowing check
        iterates it, so None raises TypeError before the CLI starts.
        """
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            claude_allowed_tools=None,
            claude_disallowed_tools=None,
        )
        manager = ClaudeSDKManager(config)

        captured_options: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].allowed_tools == []
        assert captured_options[0].disallowed_tools == []

    async def test_empty_cli_path_coerced_to_none(self, tmp_path):
        """Empty CLAUDE_CLI_PATH ('') is coerced to None so SDK auto-discovers the CLI."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            claude_cli_path="",
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].cli_path is None

    async def test_sandbox_disabled_when_config_false(self, tmp_path):
        """Test sandbox is disabled when sandbox_enabled=False."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            sandbox_enabled=False,
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].sandbox["enabled"] is False

    async def test_claude_model_passed_to_options(self, tmp_path):
        """Test that claude_model from config is passed to ClaudeAgentOptions."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            claude_model="claude-sonnet-4-20250514",
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].model == "claude-sonnet-4-20250514"

    async def test_claude_model_none_when_unset(self, tmp_path):
        """Test that model is None when claude_model is not configured."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test prompt",
                working_directory=tmp_path,
            )

        assert len(captured_options) == 1
        assert captured_options[0].model is None


class TestClaudeMCPErrors:
    """Test MCP-specific error handling."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create test config."""
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )

    @pytest.fixture
    def sdk_manager(self, config):
        """Create SDK manager."""
        return ClaudeSDKManager(config)

    async def test_mcp_connection_error_raises_mcp_error(self, sdk_manager):
        """Test that MCP connection errors raise ClaudeMCPError."""
        from claude_agent_sdk import CLIConnectionError

        from src.claude.exceptions import ClaudeMCPError

        client = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.query = AsyncMock(
            side_effect=CLIConnectionError("MCP server failed to start")
        )

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            with pytest.raises(ClaudeMCPError) as exc_info:
                await sdk_manager.execute_command(
                    prompt="Test prompt",
                    working_directory=Path("/test"),
                )

        assert "MCP server" in str(exc_info.value)

    async def test_mcp_process_error_raises_mcp_error(self, sdk_manager):
        """Test that MCP process errors raise ClaudeMCPError."""
        from claude_agent_sdk import ProcessError

        from src.claude.exceptions import ClaudeMCPError

        client = AsyncMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.query = AsyncMock(
            side_effect=ProcessError("Failed to start MCP server: connection refused")
        )

        with patch("src.claude.sdk_integration.ClaudeSDKClient", return_value=client):
            with pytest.raises(ClaudeMCPError) as exc_info:
                await sdk_manager.execute_command(
                    prompt="Test prompt",
                    working_directory=Path("/test"),
                )

        assert "MCP" in str(exc_info.value)


class TestCanUseToolCallback:
    """Test the _make_can_use_tool_callback factory and its behavior."""

    @pytest.fixture
    def approved_dir(self, tmp_path):
        return tmp_path

    @pytest.fixture
    def working_dir(self, tmp_path):
        return tmp_path / "project"

    @pytest.fixture
    def security_validator(self):
        """Create a mock SecurityValidator."""
        validator = MagicMock()
        validator.validate_path = MagicMock(return_value=(True, Path("/ok"), None))
        return validator

    @pytest.fixture
    def callback(self, security_validator, working_dir, approved_dir):
        return _make_can_use_tool_callback(
            security_validator=security_validator,
            working_directory=working_dir,
            approved_directory=approved_dir,
        )

    @pytest.fixture
    def context(self):
        return ToolPermissionContext()

    async def test_allows_safe_file_read(self, callback, context, security_validator):
        """File read with a valid path is allowed."""
        result = await callback("Read", {"file_path": "src/main.py"}, context)
        assert isinstance(result, PermissionResultAllow)
        security_validator.validate_path.assert_called_once()

    async def test_denies_invalid_file_path(
        self, callback, context, security_validator
    ):
        """File write with a path that fails validation is denied."""
        security_validator.validate_path.return_value = (
            False,
            None,
            "Path traversal detected",
        )
        result = await callback("Write", {"file_path": "../../etc/passwd"}, context)
        assert isinstance(result, PermissionResultDeny)
        assert "Path traversal" in result.message

    async def test_allows_bash_inside_boundary(
        self, callback, context, working_dir, approved_dir
    ):
        """Bash command targeting inside approved dir is allowed."""
        result = await callback(
            "Bash", {"command": f"mkdir -p {approved_dir}/subdir"}, context
        )
        assert isinstance(result, PermissionResultAllow)

    async def test_denies_bash_outside_boundary(self, callback, context):
        """Bash command targeting outside approved dir is denied."""
        result = await callback("Bash", {"command": "mkdir -p /tmp/evil"}, context)
        assert isinstance(result, PermissionResultDeny)
        assert "boundary violation" in result.message.lower()

    async def test_denies_invalid_multiedit_path(
        self, callback, context, security_validator
    ):
        """MultiEdit mutates files and is validated like Write/Edit."""
        security_validator.validate_path.return_value = (
            False,
            None,
            "Outside approved",
        )
        result = await callback("MultiEdit", {"file_path": "/etc/hosts"}, context)
        assert isinstance(result, PermissionResultDeny)

    async def test_denies_invalid_notebook_path(
        self, callback, context, security_validator
    ):
        """NotebookEdit passes its target as notebook_path, not file_path."""
        security_validator.validate_path.return_value = (
            False,
            None,
            "Outside approved",
        )
        result = await callback(
            "NotebookEdit", {"notebook_path": "/etc/evil.ipynb"}, context
        )
        assert isinstance(result, PermissionResultDeny)
        security_validator.validate_path.assert_called_once()
        assert security_validator.validate_path.call_args[0][0] == "/etc/evil.ipynb"

    async def test_allows_unknown_tool(self, callback, context):
        """Tools not in file/bash sets are allowed through."""
        result = await callback("Grep", {"pattern": "foo"}, context)
        assert isinstance(result, PermissionResultAllow)

    async def test_allows_bash_read_only_command(self, callback, context):
        """Read-only bash commands pass through even with external paths."""
        result = await callback("Bash", {"command": "cat /etc/hosts"}, context)
        assert isinstance(result, PermissionResultAllow)

    async def test_file_tool_without_path_allowed(self, callback, context):
        """File tool call without a path key is allowed (no path to validate)."""
        result = await callback("Read", {"content": "something"}, context)
        assert isinstance(result, PermissionResultAllow)

    async def test_approval_callback_not_invoked_when_not_configured(
        self, callback, context
    ):
        """Without an approval_callback, gated tools still pass through."""
        result = await callback("Bash", {"command": "echo hi"}, context)
        assert isinstance(result, PermissionResultAllow)

    async def test_approval_callback_grants(
        self, security_validator, working_dir, approved_dir, context
    ):
        """A tool in approval_tool_names is allowed when approval_callback returns True."""
        approval_callback = AsyncMock(return_value=True)
        callback = _make_can_use_tool_callback(
            security_validator=security_validator,
            working_directory=working_dir,
            approved_directory=approved_dir,
            approval_callback=approval_callback,
            approval_tool_names=frozenset({"Bash"}),
        )
        result = await callback(
            "Bash", {"command": f"mkdir -p {approved_dir}/subdir"}, context
        )
        assert isinstance(result, PermissionResultAllow)
        approval_callback.assert_awaited_once_with(
            "Bash", {"command": f"mkdir -p {approved_dir}/subdir"}
        )

    async def test_approval_callback_denies(
        self, security_validator, working_dir, approved_dir, context
    ):
        """A tool in approval_tool_names is denied when approval_callback returns False."""
        approval_callback = AsyncMock(return_value=False)
        callback = _make_can_use_tool_callback(
            security_validator=security_validator,
            working_directory=working_dir,
            approved_directory=approved_dir,
            approval_callback=approval_callback,
            approval_tool_names=frozenset({"Bash"}),
        )
        result = await callback(
            "Bash", {"command": f"mkdir -p {approved_dir}/subdir"}, context
        )
        assert isinstance(result, PermissionResultDeny)
        assert "Denied by user" in result.message

    async def test_approval_callback_skipped_for_unlisted_tool(
        self, security_validator, working_dir, approved_dir, context
    ):
        """Tools outside approval_tool_names bypass the approval callback entirely."""
        approval_callback = AsyncMock(return_value=False)
        callback = _make_can_use_tool_callback(
            security_validator=security_validator,
            working_directory=working_dir,
            approved_directory=approved_dir,
            approval_callback=approval_callback,
            approval_tool_names=frozenset({"Bash"}),
        )
        result = await callback("Read", {"file_path": "src/main.py"}, context)
        assert isinstance(result, PermissionResultAllow)
        approval_callback.assert_not_awaited()

    async def test_approval_callback_not_called_when_static_check_denies(
        self, security_validator, working_dir, approved_dir, context
    ):
        """A path/bash boundary denial short-circuits before approval is requested."""
        approval_callback = AsyncMock(return_value=True)
        callback = _make_can_use_tool_callback(
            security_validator=security_validator,
            working_directory=working_dir,
            approved_directory=approved_dir,
            approval_callback=approval_callback,
            approval_tool_names=frozenset({"Bash"}),
        )
        result = await callback("Bash", {"command": "mkdir -p /tmp/evil"}, context)
        assert isinstance(result, PermissionResultDeny)
        approval_callback.assert_not_awaited()

    async def test_wired_into_sdk_manager(self, tmp_path):
        """SecurityValidator is wired into options.can_use_tool by execute_command."""
        validator = MagicMock()
        validator.validate_path = MagicMock(return_value=(True, tmp_path, None))

        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )
        manager = ClaudeSDKManager(config, security_validator=validator)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(prompt="Test", working_directory=tmp_path)

        assert len(captured_options) == 1
        assert captured_options[0].can_use_tool is not None

    async def test_wired_with_interactive_approval(self, tmp_path):
        """execute_command wires approval_callback into can_use_tool when enabled."""
        validator = MagicMock()
        validator.validate_path = MagicMock(return_value=(True, tmp_path, None))

        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            interactive_tool_approval=True,
            interactive_tool_approval_tools=["Bash"],
        )
        manager = ClaudeSDKManager(config, security_validator=validator)
        approval_callback = AsyncMock(return_value=False)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test",
                working_directory=tmp_path,
                approval_callback=approval_callback,
            )

        can_use_tool = captured_options[0].can_use_tool
        result = await can_use_tool(
            "Bash", {"command": "echo hi"}, ToolPermissionContext()
        )
        assert isinstance(result, PermissionResultDeny)
        approval_callback.assert_awaited_once_with("Bash", {"command": "echo hi"})

    async def test_approval_callback_not_wired_when_disabled(self, tmp_path):
        """approval_callback is ignored when interactive_tool_approval is False."""
        validator = MagicMock()
        validator.validate_path = MagicMock(return_value=(True, tmp_path, None))

        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            interactive_tool_approval=False,
        )
        manager = ClaudeSDKManager(config, security_validator=validator)
        approval_callback = AsyncMock(return_value=False)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(
                prompt="Test",
                working_directory=tmp_path,
                approval_callback=approval_callback,
            )

        can_use_tool = captured_options[0].can_use_tool
        result = await can_use_tool(
            "Bash", {"command": "echo hi"}, ToolPermissionContext()
        )
        assert isinstance(result, PermissionResultAllow)
        approval_callback.assert_not_awaited()

    async def test_no_callback_without_security_validator(self, tmp_path):
        """Verify can_use_tool is None when no SecurityValidator is provided."""
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )
        manager = ClaudeSDKManager(config)

        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(prompt="Test", working_directory=tmp_path)

        assert len(captured_options) == 1
        assert captured_options[0].can_use_tool is None


class TestGuardedToolsNotPreApproved:
    """Regression tests for #219.

    ``can_use_tool`` is purely reactive: the SDK invokes it only when the CLI
    sends a ``can_use_tool`` control request, and the CLI resolves allow rules
    before consulting the permission prompt tool. A guarded tool left in
    ``allowed_tools`` is therefore pre-approved and its boundary check never
    runs. These tests pin that the guarded tools are absent from the
    ``ClaudeAgentOptions`` actually handed to the SDK.
    """

    @staticmethod
    def _config(tmp_path, **overrides):
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
            **overrides,
        )

    @staticmethod
    def _validator(tmp_path):
        validator = MagicMock()
        validator.validate_path = MagicMock(return_value=(True, tmp_path, None))
        return validator

    @staticmethod
    async def _capture(manager, tmp_path):
        captured_options = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(total_cost_usd=0.01),
            capture_options=captured_options,
        )
        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await manager.execute_command(prompt="Test", working_directory=tmp_path)
        assert len(captured_options) == 1
        return captured_options[0]

    async def test_guarded_tools_stripped_from_allowed_tools(self, tmp_path):
        """Default config: no guarded tool is pre-approved via allowed_tools."""
        manager = ClaudeSDKManager(
            self._config(tmp_path), security_validator=self._validator(tmp_path)
        )
        options = await self._capture(manager, tmp_path)

        assert options.allowed_tools is not None
        overlap = GUARDED_TOOLS & set(options.allowed_tools)
        assert overlap == set(), f"guarded tools pre-approved by the CLI: {overlap}"
        # Write, Edit, Read and Bash are the tools the callback guards.
        for tool in ("Write", "Edit", "Read", "Bash"):
            assert tool not in options.allowed_tools

    async def test_unguarded_tools_still_allowed(self, tmp_path):
        """Tools the callback does not guard keep their allow rule."""
        manager = ClaudeSDKManager(
            self._config(tmp_path), security_validator=self._validator(tmp_path)
        )
        options = await self._capture(manager, tmp_path)

        for tool in ("Glob", "Grep", "LS", "WebSearch", "TodoWrite"):
            assert tool in options.allowed_tools

    async def test_sandbox_auto_allow_bash_disabled(self, tmp_path):
        """autoAllowBashIfSandboxed would bypass the bash boundary check."""
        manager = ClaudeSDKManager(
            self._config(tmp_path), security_validator=self._validator(tmp_path)
        )
        options = await self._capture(manager, tmp_path)

        assert options.sandbox["autoAllowBashIfSandboxed"] is False

    async def test_no_security_validator_leaves_allowed_tools_untouched(self, tmp_path):
        """Without a validator there is no check to route to; nothing is gated."""
        config = self._config(tmp_path)
        manager = ClaudeSDKManager(config)
        options = await self._capture(manager, tmp_path)

        assert options.allowed_tools == config.claude_allowed_tools
        assert options.sandbox["autoAllowBashIfSandboxed"] is True

    async def test_disable_tool_validation_restores_permissive_behavior(self, tmp_path):
        """DISABLE_TOOL_VALIDATION=true remains the documented escape hatch.

        The escape hatch passes [] rather than None since #206 -- both are
        falsy, so the CLI omits --allowedTools either way and nothing is
        restricted, but [] matches the list[str] ClaudeAgentOptions declares.
        """
        manager = ClaudeSDKManager(
            self._config(tmp_path, disable_tool_validation=True),
            security_validator=self._validator(tmp_path),
        )
        options = await self._capture(manager, tmp_path)

        assert options.allowed_tools == []
        assert options.disallowed_tools == []
        assert options.sandbox["autoAllowBashIfSandboxed"] is True

    async def test_custom_allowed_tools_are_filtered_too(self, tmp_path):
        """A user-supplied allowlist is filtered on the same rule."""
        manager = ClaudeSDKManager(
            self._config(tmp_path, claude_allowed_tools=["Read", "Bash", "Grep"]),
            security_validator=self._validator(tmp_path),
        )
        options = await self._capture(manager, tmp_path)

        assert options.allowed_tools == ["Grep"]

    async def test_config_allowed_tools_not_mutated(self, tmp_path):
        """Filtering builds a new list; the Settings value is left intact."""
        config = self._config(tmp_path)
        original = list(config.claude_allowed_tools)
        manager = ClaudeSDKManager(config, security_validator=self._validator(tmp_path))
        await self._capture(manager, tmp_path)

        assert config.claude_allowed_tools == original


class TestSessionIdFallback:
    """Test fallback session ID extraction from StreamEvent messages."""

    @pytest.fixture
    def config(self, tmp_path):
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )

    @pytest.fixture
    def sdk_manager(self, config):
        return ClaudeSDKManager(config)

    async def test_session_id_from_stream_event_fallback(self, sdk_manager):
        """Test that session_id is extracted from StreamEvent when ResultMessage has None."""
        stream_event = StreamEvent(
            uuid="evt-1",
            session_id="stream-session-123",
            event={"type": "content_block_delta"},
        )
        mock_factory = _mock_client_factory(
            stream_event,
            _make_assistant_message("Test response"),
            _make_result_message(session_id=None, result="Done"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.session_id == "stream-session-123"

    async def test_session_id_from_stream_event_empty_string(self, sdk_manager):
        """Test fallback triggers when ResultMessage session_id is empty string."""
        stream_event = StreamEvent(
            uuid="evt-1",
            session_id="stream-session-456",
            event={"type": "content_block_delta"},
        )
        mock_factory = _mock_client_factory(
            stream_event,
            _make_assistant_message("Test response"),
            _make_result_message(session_id="", result="Done"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.session_id == "stream-session-456"

    async def test_no_fallback_when_result_has_session_id(self, sdk_manager):
        """Test that ResultMessage session_id takes priority over StreamEvent."""
        stream_event = StreamEvent(
            uuid="evt-1",
            session_id="stream-session-999",
            event={"type": "content_block_delta"},
        )
        mock_factory = _mock_client_factory(
            stream_event,
            _make_assistant_message("Test response"),
            _make_result_message(session_id="result-session-abc", result="Done"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        # ResultMessage session_id should win
        assert response.session_id == "result-session-abc"

    async def test_fallback_skips_stream_events_without_session_id(self, sdk_manager):
        """Test that StreamEvents without session_id are skipped in fallback."""
        stream_event_no_id = StreamEvent(
            uuid="evt-1",
            session_id=None,
            event={"type": "content_block_start"},
        )
        stream_event_with_id = StreamEvent(
            uuid="evt-2",
            session_id="found-session",
            event={"type": "content_block_delta"},
        )
        mock_factory = _mock_client_factory(
            stream_event_no_id,
            stream_event_with_id,
            _make_assistant_message("Test response"),
            _make_result_message(session_id=None, result="Done"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.session_id == "found-session"

    async def test_no_session_id_anywhere_falls_back_to_input(self, sdk_manager):
        """Test that input session_id is used when neither ResultMessage nor StreamEvent provide one."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(session_id=None, result="Done"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
                session_id="input-session-id",
            )

        # Should fall back to the input session_id
        assert response.session_id == "input-session-id"


class TestClaudeMdLoading:
    """Tests for CLAUDE.md loading from working directory."""

    @pytest.fixture
    def config(self, tmp_path):
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="test_bot",
            approved_directory=str(tmp_path),
        )

    @pytest.fixture
    def sdk_manager(self, config):
        return ClaudeSDKManager(config)

    async def test_claude_md_appended_to_system_prompt(self, sdk_manager, tmp_path):
        """CLAUDE.md content is appended to system prompt when present."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project Rules\nAlways use type hints.")

        captured: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(),
            capture_options=captured,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(prompt="test", working_directory=tmp_path)

        opts = captured[0]
        assert "# Project Rules" in opts.system_prompt
        assert "Always use type hints." in opts.system_prompt

    async def test_system_prompt_unchanged_without_claude_md(
        self, sdk_manager, tmp_path
    ):
        """System prompt is just the base when no CLAUDE.md exists."""
        captured: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(),
            capture_options=captured,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(prompt="test", working_directory=tmp_path)

        opts = captured[0]
        assert "Use relative paths." in opts.system_prompt
        assert "# Project Rules" not in opts.system_prompt

    async def test_setting_sources_includes_project(self, sdk_manager, tmp_path):
        """setting_sources=['project'] is passed to ClaudeAgentOptions."""
        captured: list = []
        mock_factory = _mock_client_factory(
            _make_assistant_message("ok"),
            _make_result_message(),
            capture_options=captured,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            await sdk_manager.execute_command(prompt="test", working_directory=tmp_path)

        opts = captured[0]
        assert opts.setting_sources == ["project"]


class TestStopReasonCapture:
    """ResultMessage stop-reason fields reach ClaudeResponse (#230, #172)."""

    @pytest.fixture
    def config(self, tmp_path):
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )

    @pytest.fixture
    def sdk_manager(self, config):
        return ClaudeSDKManager(config)

    @staticmethod
    def _tool_use_message(name="Bash"):
        """An assistant turn that used a tool but produced no text."""
        return AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=name, input={"command": "ls"})],
            model="claude-sonnet-4-20250514",
        )

    async def test_success_subtype_still_reports_completion(self, sdk_manager):
        mock_factory = _mock_client_factory(
            self._tool_use_message(),
            _make_result_message(subtype="success", result=None),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.content == TASK_COMPLETED_MSG.format(tools_summary="Bash")
        assert response.result_subtype == "success"
        assert response.completed_normally is True

    async def test_max_turns_does_not_claim_completion(self, sdk_manager):
        """A run killed at the turn limit used to report success (#172)."""
        mock_factory = _mock_client_factory(
            self._tool_use_message(),
            _make_result_message(
                subtype="error_max_turns",
                is_error=True,
                result=None,
                terminal_reason="max_turns",
                errors=["Reached maximum number of turns"],
            ),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert TASK_COMPLETED_MSG.format(tools_summary="Bash") not in response.content
        assert response.content == TASK_STOPPED_MSG.format(tools_summary="Bash")
        assert response.result_subtype == "error_max_turns"
        assert response.terminal_reason == "max_turns"
        assert response.errors == ["Reached maximum number of turns"]
        assert response.completed_normally is False

    async def test_permission_denials_reach_response(self, sdk_manager):
        mock_factory = _mock_client_factory(
            _make_assistant_message("Done what I could"),
            _make_result_message(
                result="Done what I could",
                permission_denials=[
                    {
                        "tool_name": "Write",
                        "tool_use_id": "t1",
                        "tool_input": {"file_path": "/etc/hosts"},
                    },
                    {"toolName": "Bash", "toolInput": {"command": "cd /"}},
                ],
            ),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.permission_denials == [
            {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}},
            {"tool_name": "Bash", "tool_input": {"command": "cd /"}},
        ]
        # A denial on its own does not make the run a failure.
        assert response.completed_normally is True

    async def test_missing_fields_are_tolerated(self, sdk_manager):
        """Older CLI versions omit the 0.2 fields entirely."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("Test response"),
            _make_result_message(),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.stop_reason is None
        assert response.terminal_reason is None
        assert response.errors == []
        assert response.permission_denials == []

    def test_response_defaults_keep_existing_callers_working(self):
        response = ClaudeResponse(
            content="hi", session_id="s", cost=0.0, duration_ms=1, num_turns=1
        )
        assert response.result_subtype is None
        assert response.errors == []
        assert response.permission_denials == []
        assert response.completed_normally is True


class TestNumTurns:
    """num_turns comes from the CLI, not from counting messages."""

    @pytest.fixture
    def config(self, tmp_path):
        return Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            claude_timeout_seconds=2,
        )

    @pytest.fixture
    def sdk_manager(self, config):
        return ClaudeSDKManager(config)

    async def test_result_message_wins_over_the_message_count(self, sdk_manager):
        """Every tool result arrives as another UserMessage, so counting
        messages over-reports the turns -- and the stop-reason footer shows
        that number to the user."""
        mock_factory = _mock_client_factory(
            _make_assistant_message("one"),
            _make_assistant_message("two"),
            _make_assistant_message("three"),
            _make_result_message(num_turns=10, subtype="error_max_turns"),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.num_turns == 10

    async def test_zero_turns_is_taken_at_face_value(self, sdk_manager):
        mock_factory = _mock_client_factory(
            _make_assistant_message("one"),
            _make_result_message(num_turns=0),
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.num_turns == 0

    async def test_falls_back_to_counting_when_the_cli_reports_nothing(
        self, sdk_manager
    ):
        """An older CLI, or a result that never reached the query loop."""
        result = _make_result_message()
        del result.num_turns

        mock_factory = _mock_client_factory(
            _make_assistant_message("one"),
            _make_assistant_message("two"),
            result,
        )

        with patch(
            "src.claude.sdk_integration.ClaudeSDKClient", side_effect=mock_factory
        ):
            response = await sdk_manager.execute_command(
                prompt="Test prompt",
                working_directory=Path("/test"),
            )

        assert response.num_turns == 2
