"""Tests for build_user_prompt — reply/quote context extraction."""

from types import SimpleNamespace

from src.bot.utils.quote_prompt import build_user_prompt


def _make_message(text=None, caption=None, quote=None, reply_to_message=None):
    return SimpleNamespace(
        text=text,
        caption=caption,
        quote=quote,
        reply_to_message=reply_to_message,
    )


def test_plain_message_returns_text_unchanged():
    msg = _make_message(text="hello")
    assert build_user_prompt(msg) == "hello"


def test_message_without_text_or_caption_returns_empty_string():
    msg = _make_message()
    assert build_user_prompt(msg) == ""


def test_partial_quote_takes_priority_over_reply_text():
    """When user highlights a fragment (Bot API 7.0+ partial quote), that
    fragment — not the whole replied-to message — is put in the blockquote."""
    quote = SimpleNamespace(text="only this fragment")
    reply = _make_message(text="the whole long original message")
    msg = _make_message(
        text="tell me more about it", quote=quote, reply_to_message=reply
    )

    result = build_user_prompt(msg)

    assert result == "> only this fragment\n\ntell me more about it"
    assert "whole long original" not in result


def test_reply_without_partial_quote_uses_full_reply_text():
    reply = _make_message(text="original text")
    msg = _make_message(text="my reply", reply_to_message=reply)

    assert build_user_prompt(msg) == "> original text\n\nmy reply"


def test_reply_to_media_uses_caption():
    """If the replied-to message is a photo/document (no `.text`), fall back
    to its caption."""
    reply = _make_message(text=None, caption="photo caption")
    msg = _make_message(text="what is this?", reply_to_message=reply)

    assert build_user_prompt(msg) == "> photo caption\n\nwhat is this?"


def test_multiline_quote_renders_every_line_with_prefix():
    reply = _make_message(text="first line\nsecond line\nthird line")
    msg = _make_message(text="comment", reply_to_message=reply)

    assert build_user_prompt(msg) == (
        "> first line\n> second line\n> third line\n\ncomment"
    )


def test_blank_line_inside_quote_stays_as_bare_gt():
    """Markdown blockquote convention — blank lines in the quote stay as `>`."""
    reply = _make_message(text="para1\n\npara2")
    msg = _make_message(text="x", reply_to_message=reply)

    assert build_user_prompt(msg) == "> para1\n>\n> para2\n\nx"


def test_reply_with_empty_text_and_caption_falls_through_to_plain():
    reply = _make_message(text=None, caption=None)
    msg = _make_message(text="standalone", reply_to_message=reply)

    assert build_user_prompt(msg) == "standalone"


def test_quote_without_reply_to_message_still_works():
    """Edge: Telegram does send `quote` alongside `reply_to_message` in practice,
    but the helper must not assume that."""
    quote = SimpleNamespace(text="fragment")
    msg = _make_message(text="ack", quote=quote, reply_to_message=None)

    assert build_user_prompt(msg) == "> fragment\n\nack"


def test_user_text_only_quote_no_new_text_returns_quote_alone():
    """Edge: a reply with empty user text but a quoted fragment — still send
    the quote so Claude has the context."""
    quote = SimpleNamespace(text="just the quote")
    msg = _make_message(text="", quote=quote)

    assert build_user_prompt(msg) == "> just the quote"
