"""Tests for the per-conversation /effort command and propagation contract."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import src.bot as bot_package
from src.bot.handlers.command import effort_command
from src.bot.utils.effort import get_effort


def _command_context(args, user_data=None):
    context = MagicMock()
    context.args = args
    context.user_data = user_data if user_data is not None else {}
    context.bot_data = {"audit_logger": None}
    return context


def _update():
    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()
    return update


async def test_effort_without_argument_shows_sdk_default():
    update = _update()
    context = _command_context([])

    await effort_command(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "SDK default" in text
    assert "low|medium|high|xhigh|max|default" in text


async def test_effort_sets_valid_level_case_insensitively():
    update = _update()
    context = _command_context(["XHIGH"])

    await effort_command(update, context)

    assert get_effort(context) == "xhigh"
    assert "xhigh" in update.message.reply_text.call_args.args[0]


async def test_effort_default_removes_override():
    update = _update()
    context = _command_context(["default"], {"claude_effort": "max"})

    await effort_command(update, context)

    assert "claude_effort" not in context.user_data
    assert "SDK default" in update.message.reply_text.call_args.args[0]


async def test_effort_rejects_invalid_or_extra_arguments():
    for args in (["turbo"], ["high", "extra"]):
        update = _update()
        context = _command_context(args, {"claude_effort": "medium"})

        await effort_command(update, context)

        assert get_effort(context) == "medium"
        assert "Invalid effort level" in update.message.reply_text.call_args.args[0]


def test_invalid_stored_effort_falls_back_to_default():
    context = _command_context([], {"claude_effort": "turbo"})

    assert get_effort(context) is None


def test_every_bot_claude_call_passes_effort():
    """All user-triggered Claude entry points must carry the override."""
    offenders = []
    for path in sorted(Path(bot_package.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in {"run_command", "continue_session"}:
                continue
            if not any(keyword.arg == "effort" for keyword in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], f"Claude calls missing effort override: {offenders}"
