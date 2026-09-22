"""The classic-mode callbacks build HTML by hand, so they carry the footer too."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.bot.handlers.callback import (
    TRUNCATION_NOTE,
    _clip_escaped,
    _compose_reply,
    _handle_continue_action,
    _stop_reason_html,
    handle_quick_action_callback,
)
from src.claude.sdk_integration import ClaudeResponse


def _response(**kwargs) -> ClaudeResponse:
    defaults = {
        "content": "Ran the checks.",
        "session_id": "s1",
        "cost": 0.01,
        "duration_ms": 100,
        "num_turns": 10,
    }
    defaults.update(kwargs)
    return ClaudeResponse(**defaults)


class TestStopReasonHtml:
    """format_stop_reason output converted for a hand-built HTML message."""

    def test_empty_for_a_clean_run(self):
        assert _stop_reason_html(_response(result_subtype="success")) == ""

    def test_turn_limit_renders_as_html(self):
        html = _stop_reason_html(_response(result_subtype="error_max_turns"))

        assert "turn limit reached after 10 turns" in html
        assert "<" not in html.replace("<code>", "").replace("</code>", "")

    def test_blocked_call_is_escaped_and_monospaced(self):
        html = _stop_reason_html(
            _response(
                result_subtype="success",
                permission_denials=[
                    {"tool_name": "Bash", "tool_input": {"command": "cd / && ls"}}
                ],
            )
        )

        assert "<code>cd / &amp;&amp; ls</code>" in html


async def _run_quick_action(claude_response, tmp_path):
    """Drive handle_quick_action_callback and return the text it replied with."""
    action = MagicMock()
    action.icon = "🔍"
    action.name = "Run tests"
    action.prompt = "run the tests"

    quick_actions = MagicMock()
    quick_actions.get_action = MagicMock(return_value=action)

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=claude_response)

    settings = MagicMock()
    settings.approved_directory = tmp_path

    query = MagicMock()
    query.from_user.id = 123
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"current_directory": tmp_path}
    context.bot_data = {
        "quick_actions": quick_actions,
        "claude_integration": claude_integration,
        "settings": settings,
    }

    await handle_quick_action_callback(query, "test", context)

    assert query.message.reply_text.call_args is not None, "no reply was sent"
    return query.message.reply_text.call_args.args[0]


class TestQuickActionHeading:
    """The heading said "Complete" whatever the run did — #172 in a header."""

    async def test_clean_run_still_says_complete(self, tmp_path):
        text = await _run_quick_action(
            _response(result_subtype="success"), Path(tmp_path)
        )

        assert "Complete" in text
        assert "Stopped" not in text

    async def test_truncated_run_says_stopped_and_explains(self, tmp_path):
        text = await _run_quick_action(
            _response(result_subtype="error_max_turns", terminal_reason="max_turns"),
            Path(tmp_path),
        )

        assert "Complete" not in text
        assert "Stopped" in text
        assert "turn limit reached after 10 turns" in text


class TestQuickActionMessageLength:
    """Telegram caps a message at 4096 characters and reply_text will not split.

    Appending the footer after a fixed-size clip pushed a long reply past the
    cap; the send raised, the handler's except reported "Action Error", and
    the run that most needed its stop reason was the one that lost it.
    """

    TELEGRAM_LIMIT = 4096

    async def test_long_stopped_reply_with_denials_still_fits(self, tmp_path):
        response = _response(
            content="x" * 10000,
            result_subtype="error_during_execution",
            errors=["y" * 500],
            permission_denials=[
                {"tool_name": f"Tool{i}", "tool_input": {"file_path": "z" * 100}}
                for i in range(8)
            ],
        )

        text = await _run_quick_action(response, Path(tmp_path))

        assert len(text) <= self.TELEGRAM_LIMIT

    async def test_the_footer_is_kept_and_the_body_is_what_gives(self, tmp_path):
        response = _response(
            content="x" * 10000,
            result_subtype="error_max_turns",
            terminal_reason="max_turns",
            permission_denials=[
                {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}}
            ],
        )

        text = await _run_quick_action(response, Path(tmp_path))

        assert len(text) <= self.TELEGRAM_LIMIT
        assert "Stopped" in text
        assert "turn limit reached after 10 turns" in text
        assert "1 tool call was blocked" in text
        assert "(Response truncated)" in text

    async def test_a_short_reply_is_not_truncated(self, tmp_path):
        text = await _run_quick_action(
            _response(content="Short.", result_subtype="success"), Path(tmp_path)
        )

        assert "Short." in text
        assert "truncated" not in text


class TestComposeReply:
    """Both hand-built HTML messages compose through here."""

    TELEGRAM_LIMIT = 4096

    def test_short_reply_is_untouched(self):
        text = _compose_reply(
            "<b>Heading</b>",
            _response(content="Short.", result_subtype="success"),
            body_limit=500,
        )

        assert text == "<b>Heading</b>\n\nShort."

    def test_body_limit_is_respected_below_the_cap(self):
        text = _compose_reply(
            "<b>Heading</b>",
            _response(content="x" * 2000, result_subtype="success"),
            body_limit=500,
        )

        assert len(text) < 600
        assert "(Response truncated)" in text

    def test_html_escaping_cannot_push_it_past_the_cap(self):
        """500 raw characters of & escape to 2500, and the footer adds more."""
        response = _response(
            content="&" * 500,
            result_subtype="error_during_execution",
            errors=["&" * 500],
            permission_denials=[
                {"tool_name": "Bash", "tool_input": {"command": "&" * 100}}
                for _ in range(8)
            ],
        )

        text = _compose_reply("⚠️ <b>Session Continued</b>", response, body_limit=500)

        assert len(text) <= self.TELEGRAM_LIMIT
        assert text.startswith("⚠️ <b>Session Continued</b>")
        assert "the run hit an error" in text
        assert "8 tool calls were blocked" in text


async def _run_continue_action(claude_response, tmp_path):
    """Drive _handle_continue_action and return the text it replied with."""
    claude_integration = AsyncMock()
    claude_integration.continue_session = AsyncMock(return_value=claude_response)

    settings = MagicMock()
    settings.approved_directory = tmp_path

    query = MagicMock()
    query.from_user.id = 123
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"current_directory": tmp_path}
    context.bot_data = {
        "claude_integration": claude_integration,
        "settings": settings,
    }

    await _handle_continue_action(query, context)

    assert query.message.reply_text.call_args is not None, "no reply was sent"
    return query.message.reply_text.call_args.args[0]


class TestContinueSessionHeading:
    """A green tick above a "Stopped" footer is the #172 contradiction again."""

    async def test_clean_run_keeps_the_tick(self, tmp_path):
        text = await _run_continue_action(
            _response(result_subtype="success"), Path(tmp_path)
        )

        assert text.startswith("✅ <b>Session Continued</b>")

    async def test_stopped_run_loses_the_tick_but_keeps_the_words(self, tmp_path):
        """The session did continue; it is the tick that would be false."""
        text = await _run_continue_action(
            _response(result_subtype="error_max_turns", terminal_reason="max_turns"),
            Path(tmp_path),
        )

        assert text.startswith("⚠️ <b>Session Continued</b>")
        assert "✅" not in text
        assert "turn limit reached after 10 turns" in text


class TestEntitySafeClipping:
    """A cut inside &amp; leaves &am, which Telegram rejects outright.

    The handler's except would then report a generic failure, losing the
    reply for exactly the stopped run the footer exists to explain.
    """

    @staticmethod
    def _dangling(text: str) -> bool:
        """True when the text ends in a half-written HTML entity."""
        opener = text.rfind("&")
        return opener != -1 and ";" not in text[opener:]

    def test_cut_inside_an_entity_drops_it(self):
        assert _clip_escaped("ab&amp;", 5) == "ab"

    def test_cut_on_the_entity_boundary_keeps_it(self):
        assert _clip_escaped("ab&amp;cd", 7) == "ab&amp;"

    def test_a_complete_entity_earlier_is_not_disturbed(self):
        assert _clip_escaped("&amp;xyz", 6) == "&amp;x"

    def test_nothing_to_clip(self):
        assert _clip_escaped("abc", 10) == "abc"

    def test_no_cut_point_leaves_it_empty(self):
        assert _clip_escaped("&amp;", 2) == ""

    def test_compose_reply_never_emits_a_half_entity(self):
        """Walk the cut across every offset in a run of escaping characters."""
        for pad in range(64):
            response = _response(
                content="x" * pad + "&<>" * 200, result_subtype="success"
            )

            text = _compose_reply("<b>H</b>", response, body_limit=200)

            body = text[len("<b>H</b>\n\n") :]
            if body.endswith(TRUNCATION_NOTE):
                body = body[: -len(TRUNCATION_NOTE)]
            assert not self._dangling(body), f"pad={pad}: {body[-10:]!r}"

    async def test_quick_action_reply_never_emits_a_half_entity(self, tmp_path):
        text = await _run_quick_action(
            _response(content="&" * 5000, result_subtype="error_max_turns"),
            Path(tmp_path),
        )

        head, _, tail = text.rpartition("...")
        assert not self._dangling(head or text)
