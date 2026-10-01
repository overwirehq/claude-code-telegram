"""Build prompts from Telegram messages, including reply/quote context.

When a user replies to a message (especially with a highlighted fragment —
Telegram's partial-quote feature, Bot API 7.0+), the bot's default behaviour
of reading only `update.message.text` drops the quoted context. Claude then
has to guess what the user is referring to.

This helper extracts the quoted fragment (partial quote has priority over
the full replied-to message) and renders it as a markdown blockquote above
the user's new text.
"""

from typing import Any, Optional


def build_user_prompt(message: Any) -> str:
    """Return the prompt text to send to Claude for a given user message.

    Shape, when reply/quote context is present::

        > quoted fragment line 1
        > quoted fragment line 2

        new user text

    When no reply/quote is present, returns just the user's text (or caption).
    """
    user_text = _safe_str(getattr(message, "text", None)) or _safe_str(
        getattr(message, "caption", None)
    )
    quoted = _extract_quoted_text(message)

    if not quoted:
        return user_text

    blockquote = "\n".join(f"> {line}" if line else ">" for line in quoted.split("\n"))
    if not user_text:
        return blockquote

    return f"{blockquote}\n\n{user_text}"


def _extract_quoted_text(message: Any) -> Optional[str]:
    """Return the text the user is referring to, or None.

    Priority:
    1. `message.quote.text` — Telegram partial-quote (user highlighted a
       fragment of the replied-to message). Bot API 7.0+.
    2. `message.reply_to_message.text` or `.caption` — plain reply with no
       partial highlight; fall back to the whole replied message.
    """
    quote = getattr(message, "quote", None)
    if quote is not None:
        quote_text = _safe_str(getattr(quote, "text", None))
        if quote_text:
            return quote_text

    reply = getattr(message, "reply_to_message", None)
    if reply is not None:
        reply_text = _safe_str(getattr(reply, "text", None)) or _safe_str(
            getattr(reply, "caption", None)
        )
        if reply_text:
            return reply_text

    return None


def _safe_str(value: Any) -> str:
    """Return value if it's a string, else empty string.

    Guards against MagicMock attributes in tests that set up
    `update.message` without specifying `text`/`caption`/`quote`/
    `reply_to_message` — those attributes return mock objects rather than
    None, which would otherwise crash downstream string handling.
    """
    return value if isinstance(value, str) else ""
