"""Render Claude errors as user-facing Telegram messages.

Shared by agentic mode (``src/bot/orchestrator.py``) and classic mode
(``src/bot/handlers/message.py``).  Lives here so that agentic mode does not
import from the classic handlers package.
"""

from ...claude.exceptions import (
    ClaudeError,
    ClaudeMCPError,
    ClaudeParsingError,
    ClaudeProcessError,
    ClaudeSessionError,
    ClaudeTimeoutError,
)
from .html_format import escape_html


def _format_error_message(error: Exception | str) -> str:
    """Format error messages for user-friendly display.

    Accepts an exception object (preferred) or a string for backward
    compatibility.  When an exception is provided, the error type is used
    to produce a specific, actionable message.
    """
    # Normalise: keep both the object and a string representation.
    if isinstance(error, str):
        error_str = error
        error_obj: Exception | None = None
    else:
        error_str = str(error)
        error_obj = error

    # --- Dispatch on exception type first (most specific) ---

    if isinstance(error_obj, ClaudeTimeoutError):
        return (
            "⏰ <b>Request Timeout</b>\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Try breaking your request into smaller parts\n"
            "• Avoid asking for very large file operations in one go\n"
            "• Try again — transient slowdowns happen"
        )

    if isinstance(error_obj, ClaudeMCPError):
        server_hint = ""
        if error_obj.server_name:
            server_hint = f" (<code>{escape_html(error_obj.server_name)}</code>)"
        return (
            f"🔌 <b>MCP Server Error</b>{server_hint}\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Check that the MCP server is running and reachable\n"
            "• Verify <code>MCP_CONFIG_PATH</code> points to a valid config\n"
            "• Ask the administrator to check MCP server logs"
        )

    if isinstance(error_obj, ClaudeParsingError):
        return (
            "📄 <b>Response Parsing Error</b>\n\n"
            f"Claude returned a response that could not be parsed:\n"
            f"<code>{escape_html(error_str[:300])}</code>\n\n"
            "<b>What you can do:</b>\n"
            "• Try your request again\n"
            "• Rephrase your prompt if the problem persists"
        )

    if isinstance(error_obj, ClaudeSessionError):
        return (
            "🔄 <b>Session Error</b>\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Use /new to start a fresh session\n"
            "• Try your request again\n"
            "• Use /status to check your current session"
        )

    if isinstance(error_obj, ClaudeProcessError):
        return _format_process_error(error_str)

    # Any future ClaudeError subtypes not explicitly handled above —
    # preserve their existing message as-is rather than downgrading
    # to a generic "process error".
    if isinstance(error_obj, ClaudeError):
        safe_error = escape_html(error_str)
        if len(safe_error) > 500:
            safe_error = safe_error[:500] + "..."
        return (
            f"❌ <b>Claude Error</b>\n\n"
            f"{safe_error}\n\n"
            f"Try again or use /new to start a fresh session."
        )

    # --- Fall back to keyword matching (for string-only callers) --------
    # These patterns match the known error prefixes produced by
    # sdk_integration.py and facade.py, NOT arbitrary user content.

    error_lower = error_str.lower()

    if "usage limit reached" in error_lower or "usage limit" in error_lower:
        return error_str  # Already user-friendly

    if "tool not allowed" in error_lower:
        return error_str  # Already formatted by facade.py

    if "no conversation found" in error_lower:
        return (
            "🔄 <b>Session Not Found</b>\n\n"
            "The previous Claude session could not be found or has expired.\n\n"
            "<b>What you can do:</b>\n"
            "• Use /new to start a fresh session\n"
            "• Try your request again\n"
            "• Use /status to check your current session"
        )

    if "rate limit" in error_lower:
        return (
            "⏱️ <b>Rate Limit Reached</b>\n\n"
            "Too many requests in a short time period.\n\n"
            "<b>What you can do:</b>\n"
            "• Wait a moment before trying again\n"
            "• Use simpler requests\n"
            "• Check your current usage with /status"
        )

    if "timed out after" in error_lower or "claude sdk timed out" in error_lower:
        return (
            "⏰ <b>Request Timeout</b>\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Try breaking your request into smaller parts\n"
            "• Avoid asking for very large file operations in one go\n"
            "• Try again — transient slowdowns happen"
        )

    if "overloaded" in error_lower:
        return (
            "🏗️ <b>Claude is Overloaded</b>\n\n"
            "The Claude API is currently experiencing high demand.\n\n"
            "<b>What you can do:</b>\n"
            "• Wait a moment and try again\n"
            "• Shorter prompts may succeed more easily"
        )

    if "invalid api key" in error_lower or "authentication_error" in error_lower:
        return (
            "🔑 <b>API Authentication Error</b>\n\n"
            "The API key used to connect to Claude is invalid or expired.\n\n"
            "<b>What you can do:</b>\n"
            "• Ask the administrator to verify the "
            "<code>ANTHROPIC_API_KEY</code> setting\n"
            "• Check that the API key has not been revoked"
        )

    # Match known SDK prefixes: "Failed to connect to Claude: ..."
    # and "MCP server connection failed: ..."
    if error_lower.startswith("failed to connect to claude"):
        return (
            "🌐 <b>Connection Error</b>\n\n"
            f"Could not connect to Claude:\n"
            f"<code>{escape_html(error_str[:300])}</code>\n\n"
            "<b>What you can do:</b>\n"
            "• Check your network / firewall settings\n"
            "• Verify the Claude CLI is installed and accessible\n"
            "• Try again in a moment"
        )

    # Match known SDK prefix: "Claude Code not found. ..."
    if error_lower.startswith("claude code not found"):
        return (
            "🔍 <b>Claude CLI Not Found</b>\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Ensure Claude Code is installed: "
            "<code>npm install -g @anthropic-ai/claude-code</code>\n"
            "• Set the <code>CLAUDE_CLI_PATH</code> environment variable"
        )

    # Match known SDK prefixes: "MCP server error: ..." and
    # "MCP server connection failed: ..."
    if error_lower.startswith("mcp server"):
        return (
            "🔌 <b>MCP Server Error</b>\n\n"
            f"{escape_html(error_str)}\n\n"
            "<b>What you can do:</b>\n"
            "• Check that the MCP server is running\n"
            "• Verify MCP configuration\n"
            "• Ask the administrator to check MCP server logs"
        )

    # --- No match — show the raw error as-is ---
    safe_error = escape_html(error_str)
    if len(safe_error) > 500:
        safe_error = safe_error[:500] + "..."

    return f"❌ {safe_error}"


def _format_process_error(error_str: str) -> str:
    """Format a Claude process/SDK error with the actual details."""
    safe_error = escape_html(error_str)
    if len(safe_error) > 500:
        safe_error = safe_error[:500] + "..."

    return (
        f"❌ <b>Claude Process Error</b>\n\n"
        f"{safe_error}\n\n"
        "<b>What you can do:</b>\n"
        "• Try your request again\n"
        "• Use /new to start a fresh session if the problem persists\n"
        "• Check /status for current session state"
    )
