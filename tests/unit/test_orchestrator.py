"""Tests for the MessageOrchestrator."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.orchestrator import MessageOrchestrator, _redact_secrets
from src.config import create_test_config


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def agentic_settings(tmp_dir):
    return create_test_config(approved_directory=str(tmp_dir), agentic_mode=True)


@pytest.fixture
def classic_settings(tmp_dir):
    return create_test_config(approved_directory=str(tmp_dir), agentic_mode=False)


@pytest.fixture
def group_thread_settings(tmp_dir):
    project_dir = tmp_dir / "project_a"
    project_dir.mkdir()
    config_file = tmp_dir / "projects.yaml"
    config_file.write_text(
        "projects:\n"
        "  - slug: project_a\n"
        "    name: Project A\n"
        "    path: project_a\n",
        encoding="utf-8",
    )
    return create_test_config(
        approved_directory=str(tmp_dir),
        agentic_mode=False,
        enable_project_threads=True,
        project_threads_mode="group",
        project_threads_chat_id=-1001234567890,
        projects_config_path=str(config_file),
    )


@pytest.fixture
def private_thread_settings(tmp_dir):
    project_dir = tmp_dir / "project_a"
    project_dir.mkdir()
    config_file = tmp_dir / "projects.yaml"
    config_file.write_text(
        "projects:\n"
        "  - slug: project_a\n"
        "    name: Project A\n"
        "    path: project_a\n",
        encoding="utf-8",
    )
    return create_test_config(
        approved_directory=str(tmp_dir),
        agentic_mode=False,
        enable_project_threads=True,
        project_threads_mode="private",
        projects_config_path=str(config_file),
    )


@pytest.fixture
def deps():
    return {
        "claude_integration": MagicMock(),
        "storage": MagicMock(),
        "security_validator": MagicMock(),
        "rate_limiter": MagicMock(),
        "audit_logger": MagicMock(),
    }


def test_agentic_registers_7_commands(agentic_settings, deps):
    """Agentic mode registers its seven supported commands."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    # Collect all CommandHandler registrations
    from telegram.ext import CommandHandler

    cmd_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CommandHandler)
    ]
    commands = [h[0][0].commands for h in cmd_handlers]

    assert len(cmd_handlers) == 7
    assert frozenset({"start"}) in commands
    assert frozenset({"new"}) in commands
    assert frozenset({"status"}) in commands
    assert frozenset({"verbose"}) in commands
    assert frozenset({"effort"}) in commands
    assert frozenset({"repo"}) in commands
    assert frozenset({"restart"}) in commands


def test_classic_registers_15_commands(classic_settings, deps):
    """Classic mode registers all 15 commands."""
    orchestrator = MessageOrchestrator(classic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CommandHandler

    cmd_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CommandHandler)
    ]

    assert len(cmd_handlers) == 15


def test_agentic_registers_text_document_photo_handlers(agentic_settings, deps):
    """Agentic mode registers text, document, photo, and voice message handlers."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CallbackQueryHandler, MessageHandler

    msg_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], MessageHandler)
    ]
    cb_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CallbackQueryHandler)
    ]

    # 5 message handlers (text, document, photo, voice, unknown commands passthrough)
    assert len(msg_handlers) == 5
    # 3 callback handlers (stop: + tapv: + cd:)
    assert len(cb_handlers) == 3


async def test_agentic_bot_commands(agentic_settings, deps):
    """Agentic mode returns 7 bot commands."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    commands = await orchestrator.get_bot_commands()

    assert len(commands) == 7
    cmd_names = [c.command for c in commands]
    assert cmd_names == [
        "start",
        "new",
        "status",
        "verbose",
        "effort",
        "repo",
        "restart",
    ]


async def test_classic_bot_commands(classic_settings, deps):
    """Classic mode returns 15 bot commands."""
    orchestrator = MessageOrchestrator(classic_settings, deps)
    commands = await orchestrator.get_bot_commands()

    assert len(commands) == 15
    cmd_names = [c.command for c in commands]
    assert "start" in cmd_names
    assert "help" in cmd_names
    assert "git" in cmd_names
    assert "effort" in cmd_names
    assert "restart" in cmd_names


async def test_restart_command_sends_sigterm(deps):
    """restart_command sends SIGTERM to the current process."""
    from unittest.mock import patch

    from src.bot.handlers.command import restart_command

    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"audit_logger": None}

    with patch("src.bot.handlers.command.os.kill") as mock_kill:
        await restart_command(update, context)

    import os
    import signal

    mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    # Verify confirmation message was sent
    update.message.reply_text.assert_called_once()
    msg = update.message.reply_text.call_args[0][0]
    assert "Restarting" in msg


async def test_agentic_start_no_keyboard(agentic_settings, deps):
    """Agentic /start sends brief message without inline keyboard."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.first_name = "Alice"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {"settings": agentic_settings}
    for k, v in deps.items():
        context.bot_data[k] = v

    await orchestrator.agentic_start(update, context)

    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    # No reply_markup argument (no keyboard)
    assert (
        "reply_markup" not in call_kwargs.kwargs
        or call_kwargs.kwargs.get("reply_markup") is None
    )
    # Contains user name
    assert "Alice" in call_kwargs.args[0]


async def test_agentic_new_resets_session(agentic_settings, deps):
    """Agentic /new clears session and sends brief confirmation."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"claude_session_id": "old-session-123"}

    await orchestrator.agentic_new(update, context)

    assert context.user_data["claude_session_id"] is None
    update.message.reply_text.assert_called_once_with("Session reset. What's next?")


async def test_agentic_status_compact(agentic_settings, deps):
    """Agentic /status returns compact one-line status."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {"rate_limiter": None}

    await orchestrator.agentic_status(update, context)

    call_args = update.message.reply_text.call_args
    text = call_args.args[0]
    assert "Session: none" in text
    assert "Effort: default" in text


async def test_agentic_text_calls_claude(agentic_settings, deps):
    """Agentic text handler calls Claude and returns response without keyboard."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    # Mock Claude response
    mock_response = MagicMock()
    mock_response.session_id = "session-abc"
    mock_response.content = "Hello, I can help with that!"
    mock_response.tools_used = []

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=mock_response)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.text = "Help me with this code"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    # Progress message mock
    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": None,
    }

    await orchestrator.agentic_text(update, context)

    # Claude was called
    claude_integration.run_command.assert_called_once()

    # Session ID updated
    assert context.user_data["claude_session_id"] == "session-abc"

    # Progress message deleted
    progress_msg.delete.assert_called_once()

    # Response sent without keyboard (reply_markup=None)
    response_calls = [
        c
        for c in update.message.reply_text.call_args_list
        if c != update.message.reply_text.call_args_list[0]
    ]
    for call in response_calls:
        assert call.kwargs.get("reply_markup") is None


async def test_agentic_callback_scoped_to_cd_pattern(agentic_settings, deps):
    """Agentic callback handler is registered with cd: pattern filter."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CallbackQueryHandler

    cb_handlers = [
        call[0][0]
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CallbackQueryHandler)
    ]

    assert len(cb_handlers) == 3
    # Find the cd: handler by pattern
    cd_handler = [h for h in cb_handlers if h.pattern and h.pattern.match("cd:x")]
    assert len(cd_handler) == 1
    assert cd_handler[0].pattern.match("cd:my_project")
    # Also has a stop: handler
    stop_handler = [h for h in cb_handlers if h.pattern and h.pattern.match("stop:1")]
    assert len(stop_handler) == 1
    # Also has a tapv: handler
    tapv_handler = [
        h for h in cb_handlers if h.pattern and h.pattern.match("tapv:allow:x")
    ]
    assert len(tapv_handler) == 1


async def test_agentic_document_rejects_large_files(agentic_settings, deps):
    """Agentic document handler rejects files over 10MB."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.document.file_name = "big.bin"
    update.message.document.file_size = 20 * 1024 * 1024  # 20MB
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"security_validator": None}

    await orchestrator.agentic_document(update, context)

    call_args = update.message.reply_text.call_args
    assert "too large" in call_args.args[0].lower()


async def test_agentic_voice_calls_claude(agentic_settings, deps):
    """Agentic voice handler transcribes and routes prompt to Claude."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    mock_response = MagicMock()
    mock_response.session_id = "voice-session-123"
    mock_response.content = "Voice response from Claude"
    mock_response.tools_used = []

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=mock_response)

    processed_voice = MagicMock()
    processed_voice.prompt = "Voice prompt text"

    voice_handler = MagicMock()
    voice_handler.process_voice_message = AsyncMock(return_value=processed_voice)

    features = MagicMock()
    features.get_voice_handler.return_value = voice_handler

    update = MagicMock()
    update.effective_user.id = 123
    update.message.voice = MagicMock()
    update.message.caption = "please summarize"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "features": features,
        "claude_integration": claude_integration,
    }

    await orchestrator.agentic_voice(update, context)

    voice_handler.process_voice_message.assert_awaited_once_with(
        update.message.voice, "please summarize"
    )
    claude_integration.run_command.assert_awaited_once()
    assert context.user_data["claude_session_id"] == "voice-session-123"


async def test_agentic_voice_missing_handler_is_provider_aware(tmp_path, deps):
    """Missing voice handler guidance references the configured provider key."""
    settings = create_test_config(
        approved_directory=str(tmp_path),
        agentic_mode=True,
        voice_provider="openai",
    )
    orchestrator = MessageOrchestrator(settings, deps)

    features = MagicMock()
    features.get_voice_handler.return_value = None

    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"features": features}
    context.user_data = {}

    await orchestrator.agentic_voice(update, context)

    call_args = update.message.reply_text.call_args
    assert "OPENAI_API_KEY" in call_args.args[0]


async def test_agentic_voice_transcription_failure_surfaces_user_error(
    agentic_settings, deps
):
    """Transcription failures are shown to users and do not call Claude."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    voice_handler = MagicMock()
    voice_handler.process_voice_message = AsyncMock(
        side_effect=RuntimeError("Mistral transcription request failed: boom")
    )

    features = MagicMock()
    features.get_voice_handler.return_value = voice_handler

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 123
    update.message.voice = MagicMock()
    update.message.caption = None
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.edit_text = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "features": features,
        "claude_integration": claude_integration,
    }

    await orchestrator.agentic_voice(update, context)

    progress_msg.edit_text.assert_awaited_once()
    error_text = progress_msg.edit_text.call_args.args[0]
    assert "Mistral transcription request failed" in error_text
    assert progress_msg.edit_text.call_args.kwargs["parse_mode"] == "HTML"
    claude_integration.run_command.assert_not_awaited()


async def test_agentic_start_escapes_html_in_name(agentic_settings, deps):
    """Names with HTML-special characters are escaped safely."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.first_name = "A<B>&C"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}

    await orchestrator.agentic_start(update, context)

    call_kwargs = update.message.reply_text.call_args
    text = call_kwargs.args[0]
    # HTML-special characters should be escaped
    assert "A&lt;B&gt;&amp;C" in text
    # parse_mode is HTML
    assert call_kwargs.kwargs.get("parse_mode") == "HTML"


async def test_agentic_text_logs_failure_on_error(agentic_settings, deps):
    """Failed Claude runs are logged with success=False."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(side_effect=Exception("Claude broke"))

    audit_logger = AsyncMock()
    audit_logger.log_command = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 123
    update.message.text = "do something"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": audit_logger,
    }

    await orchestrator.agentic_text(update, context)

    # Audit logged with success=False
    audit_logger.log_command.assert_called_once()
    call_kwargs = audit_logger.log_command.call_args
    assert call_kwargs.kwargs["success"] is False


# --- _redact_secrets / _summarize_tool_input tests ---


class TestRedactSecrets:
    """Ensure sensitive substrings are redacted from Bash command summaries."""

    def test_safe_command_unchanged(self):
        assert (
            _redact_secrets("poetry run pytest tests/ -v")
            == "poetry run pytest tests/ -v"
        )

    def test_anthropic_api_key_redacted(self):
        key = "sk-ant-api03-abc123def456ghi789jkl012mno345"
        cmd = f"ANTHROPIC_API_KEY={key}"
        result = _redact_secrets(cmd)
        assert key not in result
        assert "***" in result

    def test_sk_key_redacted(self):
        cmd = "curl -H 'Authorization: Bearer sk-1234567890abcdefghijklmnop'"
        result = _redact_secrets(cmd)
        assert "sk-1234567890abcdefghijklmnop" not in result
        assert "***" in result

    def test_github_pat_redacted(self):
        cmd = "git clone https://ghp_abcdefghijklmnop1234@github.com/user/repo"
        result = _redact_secrets(cmd)
        assert "ghp_abcdefghijklmnop1234" not in result
        assert "***" in result

    def test_aws_key_redacted(self):
        cmd = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = _redact_secrets(cmd)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "***" in result

    def test_flag_token_redacted(self):
        cmd = "mycli --token=supersecretvalue123"
        result = _redact_secrets(cmd)
        assert "supersecretvalue123" not in result
        assert "--token=" in result or "--token" in result

    def test_password_env_redacted(self):
        cmd = "PASSWORD=MyS3cretP@ss! ./run.sh"
        result = _redact_secrets(cmd)
        assert "MyS3cretP@ss!" not in result
        assert "***" in result

    def test_bearer_token_redacted(self):
        cmd = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig'"
        result = _redact_secrets(cmd)
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in result

    def test_connection_string_redacted(self):
        cmd = "psql postgresql://admin:secret_password@db.host:5432/mydb"
        result = _redact_secrets(cmd)
        assert "secret_password" not in result

    def test_summarize_tool_input_bash_redacts(self, agentic_settings, deps):
        """_summarize_tool_input applies redaction to Bash commands."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Bash",
            {"command": "curl --token=mysupersecrettoken123 https://api.example.com"},
        )
        assert "mysupersecrettoken123" not in result
        assert "***" in result

    def test_summarize_tool_input_non_bash_unchanged(self, agentic_settings, deps):
        """Non-Bash tools don't go through redaction."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Read", {"file_path": "/home/user/.env"}
        )
        assert result == ".env"


class TestSummarizeToolInputForApproval:
    """_summarize_tool_input_for_approval shows full detail for security decisions."""

    def test_write_shows_full_path_not_just_filename(self, agentic_settings, deps):
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input_for_approval(
            "Write", {"file_path": "/home/user/project/settings.py"}
        )
        assert result == "/home/user/project/settings.py"

    def test_edit_shows_full_path(self, agentic_settings, deps):
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input_for_approval(
            "Edit", {"file_path": "/etc/secrets/config.yaml"}
        )
        assert result == "/etc/secrets/config.yaml"

    def test_bash_short_command_not_truncated(self, agentic_settings, deps):
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input_for_approval(
            "Bash", {"command": "echo hi"}
        )
        assert result == "echo hi"

    def test_bash_long_command_truncated_at_1000_with_marker(
        self, agentic_settings, deps
    ):
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        long_cmd = "echo " + ("a" * 2000)
        result = orchestrator._summarize_tool_input_for_approval(
            "Bash", {"command": long_cmd}
        )
        assert len(result) == 1001  # 1000 chars + the "…" marker
        assert result.endswith("…")

    def test_bash_redacts_secrets(self, agentic_settings, deps):
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input_for_approval(
            "Bash",
            {"command": "curl --token=mysupersecrettoken123 https://api.example.com"},
        )
        assert "mysupersecrettoken123" not in result
        assert "***" in result


# --- Typing heartbeat tests ---


class TestTypingHeartbeat:
    """Verify typing indicator stays alive independently of stream events."""

    async def test_heartbeat_sends_typing_action(self, agentic_settings, deps):
        """Heartbeat sends typing actions at the configured interval."""
        chat = AsyncMock()
        chat.send_action = AsyncMock()

        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        # Let the heartbeat fire a few times
        await asyncio.sleep(0.2)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        # Should have been called multiple times
        assert chat.send_action.call_count >= 2
        chat.send_action.assert_called_with("typing")

    async def test_heartbeat_cancels_cleanly(self, agentic_settings, deps):
        """Cancelling the heartbeat task does not raise."""
        chat = AsyncMock()
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        heartbeat.cancel()
        # Should not raise
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        assert heartbeat.cancelled() or heartbeat.done()

    async def test_heartbeat_survives_send_action_errors(self, agentic_settings, deps):
        """Heartbeat keeps running even if send_action raises."""
        chat = AsyncMock()
        call_count = [0]

        async def flaky_send_action(action: str) -> None:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("Network error")

        chat.send_action = flaky_send_action

        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        await asyncio.sleep(0.3)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        # Should have called send_action more than 2 times (survived errors)
        assert call_count[0] >= 3

    async def test_stream_callback_independent_of_typing(self, agentic_settings, deps):
        """Stream callback no longer sends typing — that's the heartbeat's job."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)

        progress_msg = AsyncMock()
        tool_log: list = []  # type: ignore[type-arg]
        callback = orchestrator._make_stream_callback(
            verbose_level=1,
            progress_msg=progress_msg,
            tool_log=tool_log,
            start_time=0.0,
        )
        assert callback is not None

        # Verify the callback signature doesn't accept a 'chat' parameter
        # (typing is no longer handled by the stream callback)
        import inspect

        sig = inspect.signature(orchestrator._make_stream_callback)
        assert "chat" not in sig.parameters


async def test_group_thread_mode_rejects_non_forum_chat(group_thread_settings, deps):
    """Strict thread mode rejects updates outside configured forum chat."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    called = {"value": False}

    async def dummy_handler(update, context):
        called["value"] = True

    wrapped = orchestrator._inject_deps(dummy_handler)

    update = MagicMock()
    update.effective_chat.id = -1002222222
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is False
    update.effective_message.reply_text.assert_called_once()


async def test_thread_mode_loads_and_persists_thread_state(group_thread_settings, deps):
    """Thread mode loads per-thread context and writes updates back."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    project_path = group_thread_settings.approved_directory / "project_a"
    project = SimpleNamespace(
        slug="project_a",
        name="Project A",
        absolute_path=project_path,
    )

    project_threads_manager = MagicMock()
    project_threads_manager.resolve_project = AsyncMock(return_value=project)
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    async def dummy_handler(update, context):
        assert context.user_data["claude_session_id"] == "old-session"
        assert context.user_data["claude_effort"] == "high"
        context.user_data["claude_session_id"] = "new-session"
        context.user_data["claude_effort"] = "max"

    wrapped = orchestrator._inject_deps(dummy_handler)

    update = MagicMock()
    update.effective_chat.id = -1001234567890
    update.effective_message.message_thread_id = 777
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {
        "thread_state": {
            "-1001234567890:777": {
                "current_directory": str(project_path),
                "claude_session_id": "old-session",
                "claude_effort": "high",
            }
        }
    }

    await wrapped(update, context)

    assert (
        context.user_data["thread_state"]["-1001234567890:777"]["claude_session_id"]
        == "new-session"
    )
    assert (
        context.user_data["thread_state"]["-1001234567890:777"]["claude_effort"]
        == "max"
    )


async def test_sync_threads_bypasses_thread_gate(group_thread_settings, deps):
    """sync_threads command bypasses strict thread routing gate."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    called = {"value": False}

    async def sync_threads(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(sync_threads)

    update = MagicMock()
    update.effective_chat.id = -1002222222
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is True


async def test_private_mode_start_bypasses_thread_gate(private_thread_settings, deps):
    """Private mode allows /start outside topics."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    called = {"value": False}

    async def start_command(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(start_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_chat.is_forum = False
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is True
    project_threads_manager.resolve_project.assert_not_called()


async def test_private_mode_start_inside_topic_uses_thread_context(
    private_thread_settings, deps
):
    """/start in private topic should load mapped thread context."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    project_path = private_thread_settings.approved_directory / "project_a"
    project = SimpleNamespace(
        slug="project_a",
        name="Project A",
        absolute_path=project_path,
    )
    project_threads_manager = MagicMock()
    project_threads_manager.resolve_project = AsyncMock(return_value=project)
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    captured = {"dir": None}

    async def start_command(update, context):
        captured["dir"] = context.user_data.get("current_directory")

    wrapped = orchestrator._inject_deps(start_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_message.message_thread_id = 777
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {
        "thread_state": {
            "12345:777": {
                "current_directory": str(project_path),
                "claude_session_id": "old",
            }
        }
    }

    await wrapped(update, context)

    project_threads_manager.resolve_project.assert_awaited_once_with(12345, 777)
    assert captured["dir"] == project_path


async def test_private_mode_rejects_help_outside_topics(private_thread_settings, deps):
    """Private mode rejects non-allowed commands outside mapped topics."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    called = {"value": False}

    async def help_command(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(help_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_chat.is_forum = False
    update.effective_message.message_thread_id = None
    update.effective_message.direct_messages_topic = None
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is False
    update.effective_message.reply_text.assert_called_once()


async def test_known_command_not_forwarded_to_claude(agentic_settings, deps):
    """Known commands must NOT be forwarded to agentic_text."""
    from unittest.mock import AsyncMock, MagicMock, patch

    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()
    orchestrator.register_handlers(app)

    update = MagicMock()
    update.effective_message.text = "/start"
    context = MagicMock()

    with patch.object(
        orchestrator, "agentic_text", new_callable=AsyncMock
    ) as mock_claude:
        await orchestrator._handle_unknown_command(update, context)
        mock_claude.assert_not_called()


async def test_unknown_command_forwarded_to_claude(agentic_settings, deps):
    """Unknown slash commands must be forwarded to agentic_text."""
    from unittest.mock import AsyncMock, MagicMock, patch

    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()
    orchestrator.register_handlers(app)

    update = MagicMock()
    update.effective_message.text = "/workflow activate job-hunter"
    context = MagicMock()

    with patch.object(
        orchestrator, "agentic_text", new_callable=AsyncMock
    ) as mock_claude:
        await orchestrator._handle_unknown_command(update, context)
        mock_claude.assert_called_once_with(update, context)


async def test_bot_suffixed_command_not_forwarded(agentic_settings, deps):
    """Bot-suffixed known commands like /start@mybot must not reach Claude."""
    from unittest.mock import AsyncMock, MagicMock, patch

    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()
    orchestrator.register_handlers(app)

    update = MagicMock()
    update.effective_message.text = "/start@mybot"
    context = MagicMock()

    with patch.object(
        orchestrator, "agentic_text", new_callable=AsyncMock
    ) as mock_claude:
        await orchestrator._handle_unknown_command(update, context)
        mock_claude.assert_not_called()
