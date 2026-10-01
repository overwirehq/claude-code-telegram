"""Tests for proxy wiring and credential redaction in bot core."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.bot.core as core_module
from src.bot.core import ClaudeCodeBot, _redact_proxy_url
from src.config import create_test_config


@pytest.fixture
def bot_with_builder(monkeypatch):
    """Create a bot with mocked Application builder plumbing."""
    settings = create_test_config()
    deps = {
        "storage": MagicMock(),
        "security": MagicMock(),
    }
    bot = ClaudeCodeBot(settings, deps)

    builder = MagicMock()

    app = MagicMock()
    app.bot = MagicMock()
    app.bot.set_my_commands = AsyncMock()
    app.initialize = AsyncMock()
    builder.build.return_value = app

    monkeypatch.setattr(
        core_module.Application,
        "builder",
        MagicMock(return_value=builder),
    )
    monkeypatch.setattr(
        core_module,
        "FeatureRegistry",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(bot, "_set_bot_commands", AsyncMock())
    monkeypatch.setattr(bot, "_register_handlers", MagicMock())
    monkeypatch.setattr(bot, "_add_middleware", MagicMock())

    return bot, builder


# --- redaction helper ---


class TestRedactProxyUrl:
    """_redact_proxy_url must hide the password and nothing else."""

    def test_password_is_masked(self):
        assert (
            _redact_proxy_url("http://alice:s3cret@proxy.internal:3128")
            == "http://alice:***@proxy.internal:3128"
        )

    def test_username_scheme_host_and_port_survive(self):
        redacted = _redact_proxy_url("https://svc-bot:hunter2@10.0.0.9:8080")
        assert redacted.startswith("https://svc-bot:***@")
        assert "10.0.0.9:8080" in redacted
        assert "hunter2" not in redacted

    def test_url_without_credentials_is_untouched(self):
        url = "http://proxy.internal:3128"
        assert _redact_proxy_url(url) == url

    def test_url_with_username_only_is_untouched(self):
        url = "http://alice@proxy.internal:3128"
        assert _redact_proxy_url(url) == url

    def test_password_containing_at_sign_is_masked(self):
        """rpartition on '@' must split on the userinfo separator, not the password."""
        redacted = _redact_proxy_url("http://alice:p@ss@proxy.internal:3128")
        assert "p@ss" not in redacted
        assert redacted == "http://alice:***@proxy.internal:3128"

    def test_unparsable_url_is_not_echoed(self):
        """A URL we cannot parse must never be logged verbatim."""
        assert _redact_proxy_url("http://[::1") == "<unparsable proxy URL>"


# --- builder wiring ---


@pytest.mark.asyncio
async def test_initialize_configures_proxy_from_environment(
    bot_with_builder, monkeypatch
):
    """HTTPS_PROXY must reach both PTB request clients."""
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:s3cret@proxy.internal:3128")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    bot, builder = bot_with_builder

    await bot.initialize()

    builder.proxy.assert_called_once_with("http://alice:s3cret@proxy.internal:3128")
    builder.get_updates_proxy.assert_called_once_with(
        "http://alice:s3cret@proxy.internal:3128"
    )


@pytest.mark.asyncio
async def test_initialize_does_not_log_proxy_password(
    bot_with_builder, monkeypatch, caplog
):
    """Regression: the proxy password must not reach the logs."""
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:s3cret@proxy.internal:3128")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    bot, _builder = bot_with_builder

    logged = []
    monkeypatch.setattr(
        core_module.logger,
        "info",
        lambda event, **kw: logged.append((event, kw)),
    )

    await bot.initialize()

    proxy_events = [kw for event, kw in logged if event == "Proxy configured"]
    assert proxy_events, "expected a 'Proxy configured' log entry"
    assert proxy_events[0]["proxy"] == "http://alice:***@proxy.internal:3128"
    assert "s3cret" not in str(logged)


@pytest.mark.asyncio
async def test_initialize_skips_proxy_when_unset(bot_with_builder, monkeypatch):
    """No proxy env vars means builder.proxy() is never called."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    bot, builder = bot_with_builder

    await bot.initialize()

    builder.proxy.assert_not_called()
    builder.get_updates_proxy.assert_not_called()
