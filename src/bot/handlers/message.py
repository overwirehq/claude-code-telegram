"""Message handlers for non-command inputs."""

import asyncio
import os
from typing import Optional

import structlog
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from ...claude.exceptions import (
    ClaudeError,
    ClaudeMCPError,
    ClaudeParsingError,
    ClaudeProcessError,
    ClaudeSessionError,
    ClaudeTimeoutError,
)
from ...config.settings import Settings
from ...security.audit import AuditLogger
from ...security.rate_limiter import RateLimiter
from ...security.validators import SecurityValidator
from ..utils.file_extractor import (
    REJECTION_SURFACE_TO_USER,
    FileAttachment,
    validate_file_path,
)
from ..utils.html_format import escape_html
from ..utils.image_extractor import (
    ImageAttachment,
    should_send_as_photo,
    validate_image_path,
)

logger = structlog.get_logger()


async def _format_progress_update(update_obj) -> Optional[str]:
    """Format progress updates with enhanced context and visual indicators."""
    if update_obj.type == "tool_result":
        # Show tool completion status
        tool_name = "Unknown"
        if update_obj.metadata and update_obj.metadata.get("tool_use_id"):
            # Try to extract tool name from context if available
            tool_name = update_obj.metadata.get("tool_name", "Tool")

        if update_obj.is_error():
            return f"❌ <b>{tool_name} failed</b>\n\n<i>{update_obj.get_error_message()}</i>"
        else:
            execution_time = ""
            if update_obj.metadata and update_obj.metadata.get("execution_time_ms"):
                time_ms = update_obj.metadata["execution_time_ms"]
                execution_time = f" ({time_ms}ms)"
            return f"✅ <b>{tool_name} completed</b>{execution_time}"

    elif update_obj.type == "progress":
        # Handle progress updates
        progress_text = f"🔄 <b>{update_obj.content or 'Working...'}</b>"

        percentage = update_obj.get_progress_percentage()
        if percentage is not None:
            # Create a simple progress bar
            filled = int(percentage / 10)  # 0-10 scale
            bar = "█" * filled + "░" * (10 - filled)
            progress_text += f"\n\n<code>{bar}</code> {percentage}%"

        if update_obj.progress:
            step = update_obj.progress.get("step")
            total_steps = update_obj.progress.get("total_steps")
            if step and total_steps:
                progress_text += f"\n\nStep {step} of {total_steps}"

        return progress_text

    elif update_obj.type == "error":
        # Handle error messages
        return f"❌ <b>Error</b>\n\n<i>{update_obj.get_error_message()}</i>"

    elif update_obj.type == "assistant" and update_obj.tool_calls:
        # Show when tools are being called
        tool_names = update_obj.get_tool_names()
        if tool_names:
            tools_text = ", ".join(tool_names)
            return f"🔧 <b>Using tools:</b> {tools_text}"

    elif update_obj.type == "assistant" and update_obj.content:
        # Regular content updates with preview
        content_preview = (
            update_obj.content[:150] + "..."
            if len(update_obj.content) > 150
            else update_obj.content
        )
        return f"🤖 <b>Claude is working...</b>\n\n<i>{content_preview}</i>"

    elif update_obj.type == "system":
        # System initialization or other system messages
        if update_obj.metadata and update_obj.metadata.get("subtype") == "init":
            tools_count = len(update_obj.metadata.get("tools", []))
            model = update_obj.metadata.get("model", "Claude")
            return f"🚀 <b>Starting {model}</b> with {tools_count} tools available"

    return None


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


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle regular text messages as Claude prompts."""
    user_id = update.effective_user.id
    message_text = update.message.text
    settings: Settings = context.bot_data["settings"]

    # Get services
    rate_limiter: Optional[RateLimiter] = context.bot_data.get("rate_limiter")
    audit_logger: Optional[AuditLogger] = context.bot_data.get("audit_logger")

    logger.info(
        "Processing text message", user_id=user_id, message_length=len(message_text)
    )

    try:
        # Check rate limit with estimated cost for text processing
        estimated_cost = _estimate_text_processing_cost(message_text)

        if rate_limiter:
            allowed, limit_message = await rate_limiter.check_rate_limit(
                user_id, estimated_cost
            )
            if not allowed:
                await update.message.reply_text(f"⏱️ {limit_message}")
                return

        # Send typing indicator
        await update.message.chat.send_action("typing")

        # Create progress message
        progress_msg = await update.message.reply_text(
            "🤔 Processing your request...",
            reply_to_message_id=update.message.message_id,
        )

        # Get Claude integration and storage from context
        claude_integration = context.bot_data.get("claude_integration")
        storage = context.bot_data.get("storage")

        if not claude_integration:
            await update.message.reply_text(
                "❌ <b>Claude integration not available</b>\n\n"
                "The Claude Code integration is not properly configured. "
                "Please contact the administrator.",
                parse_mode="HTML",
            )
            return

        # Get current directory
        current_dir = context.user_data.get(
            "current_directory", settings.approved_directory
        )

        # Get existing session ID
        session_id = context.user_data.get("claude_session_id")

        # Check if /new was used — skip auto-resume for this first message.
        # Flag is only cleared after a successful run so retries keep the intent.
        force_new = bool(context.user_data.get("force_new_session"))

        # MCP image / file collection via stream intercept
        mcp_images: list[ImageAttachment] = []
        mcp_files: list[FileAttachment] = []
        mcp_rejected_files: list[str] = []

        # Enhanced stream updates handler with progress tracking
        async def stream_handler(update_obj):
            # Intercept send_image_to_user / send_file_to_user MCP tool calls.
            # The SDK namespaces MCP tools as "mcp__<server>__<tool>".
            if update_obj.tool_calls:
                for tc in update_obj.tool_calls:
                    tc_name = tc.get("name", "")
                    tc_input = tc.get("input", {})
                    file_path = tc_input.get("file_path", "")
                    caption = tc_input.get("caption", "")
                    if tc_name == "send_image_to_user" or tc_name.endswith(
                        "__send_image_to_user"
                    ):
                        img = validate_image_path(
                            file_path, settings.approved_directory, caption
                        )
                        if img:
                            mcp_images.append(img)
                    elif tc_name == "send_file_to_user" or tc_name.endswith(
                        "__send_file_to_user"
                    ):
                        attachment, reason = validate_file_path(
                            file_path, settings.approved_directory, caption
                        )
                        if attachment:
                            mcp_files.append(attachment)
                        elif file_path and reason in REJECTION_SURFACE_TO_USER:
                            mcp_rejected_files.append(file_path)

            try:
                progress_text = await _format_progress_update(update_obj)
                if progress_text:
                    await progress_msg.edit_text(progress_text, parse_mode="HTML")
            except Exception as e:
                logger.warning("Failed to update progress message", error=str(e))

        # Run Claude command
        try:
            claude_response = await claude_integration.run_command(
                prompt=message_text,
                working_directory=current_dir,
                user_id=user_id,
                session_id=session_id,
                on_stream=stream_handler,
                force_new=force_new,
            )

            # New session created successfully — clear the one-shot flag
            if force_new:
                context.user_data["force_new_session"] = False

            # Update session ID
            context.user_data["claude_session_id"] = claude_response.session_id

            # Check if Claude changed the working directory and update our tracking
            _update_working_directory_from_claude_response(
                claude_response, context, settings, user_id
            )

            # Log interaction to storage
            if storage:
                try:
                    await storage.save_claude_interaction(
                        user_id=user_id,
                        session_id=claude_response.session_id,
                        prompt=message_text,
                        response=claude_response,
                        ip_address=None,  # Telegram doesn't provide IP
                    )
                except Exception as e:
                    logger.warning("Failed to log interaction to storage", error=str(e))

            # Format response
            from ..utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

        except Exception as e:
            logger.error("Claude integration failed", error=str(e), user_id=user_id)
            from ..utils.formatting import FormattedMessage

            formatted_messages = [
                FormattedMessage(_format_error_message(e), parse_mode="HTML")
            ]

        # Delete progress message
        await progress_msg.delete()

        # Use MCP-collected images (from send_image_to_user tool calls)
        images: list[ImageAttachment] = mcp_images

        # Try to combine text + images when response fits in a caption
        caption_sent = False
        if images and len(formatted_messages) == 1:
            msg = formatted_messages[0]
            if msg.text and len(msg.text) <= 1024:
                photos = [i for i in images if should_send_as_photo(i.path)]
                documents = [i for i in images if not should_send_as_photo(i.path)]
                if photos and not documents:
                    try:
                        if len(photos) == 1:
                            with open(photos[0].path, "rb") as f:
                                await update.message.reply_photo(
                                    photo=f,
                                    caption=msg.text,
                                    parse_mode=msg.parse_mode,
                                    reply_to_message_id=update.message.message_id,
                                )
                            caption_sent = True
                        else:
                            media = []
                            file_handles = []
                            for idx, img in enumerate(photos[:10]):
                                fh = open(img.path, "rb")  # noqa: SIM115
                                file_handles.append(fh)
                                media.append(
                                    InputMediaPhoto(
                                        media=fh,
                                        caption=msg.text if idx == 0 else None,
                                        parse_mode=(
                                            msg.parse_mode if idx == 0 else None
                                        ),
                                    )
                                )
                            try:
                                await update.message.chat.send_media_group(
                                    media=media,
                                    reply_to_message_id=update.message.message_id,
                                )
                                caption_sent = True
                            finally:
                                for fh in file_handles:
                                    fh.close()
                    except Exception as album_err:
                        logger.warning(
                            "Failed to send photo+caption", error=str(album_err)
                        )

        if not caption_sent:
            # Send formatted responses (may be multiple messages)
            for i, message in enumerate(formatted_messages):
                try:
                    await update.message.reply_text(
                        message.text,
                        parse_mode=message.parse_mode,
                        reply_markup=message.reply_markup,
                        reply_to_message_id=(
                            update.message.message_id if i == 0 else None
                        ),
                    )
                    if i < len(formatted_messages) - 1:
                        await asyncio.sleep(0.5)
                except Exception as send_err:
                    logger.warning(
                        "Failed to send HTML response, retrying as plain text",
                        error=str(send_err),
                        message_index=i,
                    )
                    try:
                        await update.message.reply_text(
                            message.text,
                            reply_markup=message.reply_markup,
                            reply_to_message_id=(
                                update.message.message_id if i == 0 else None
                            ),
                        )
                    except Exception as plain_err:
                        logger.error(
                            "Failed to send plain text fallback response",
                            error=str(plain_err),
                        )
                        await update.message.reply_text(
                            f"Failed to deliver response "
                            f"(Telegram error: {str(plain_err)[:150]}). "
                            f"Please try again.",
                            reply_to_message_id=(
                                update.message.message_id if i == 0 else None
                            ),
                        )

            # Send images separately
            if images:
                photos = [i for i in images if should_send_as_photo(i.path)]
                documents = [i for i in images if not should_send_as_photo(i.path)]
                if photos:
                    try:
                        if len(photos) == 1:
                            with open(photos[0].path, "rb") as f:
                                await update.message.reply_photo(
                                    photo=f,
                                    reply_to_message_id=update.message.message_id,
                                )
                        else:
                            media = []
                            file_handles = []
                            for img in photos[:10]:
                                fh = open(img.path, "rb")  # noqa: SIM115
                                file_handles.append(fh)
                                media.append(InputMediaPhoto(media=fh))
                            try:
                                await update.message.chat.send_media_group(
                                    media=media,
                                    reply_to_message_id=update.message.message_id,
                                )
                            finally:
                                for fh in file_handles:
                                    fh.close()
                    except Exception as album_err:
                        logger.warning(
                            "Failed to send photo album", error=str(album_err)
                        )
                for img in documents:
                    try:
                        with open(img.path, "rb") as f:
                            await update.message.reply_document(
                                document=f,
                                filename=img.path.name,
                                reply_to_message_id=update.message.message_id,
                            )
                        await asyncio.sleep(0.5)
                    except Exception as doc_err:
                        logger.warning(
                            "Failed to send document image",
                            path=str(img.path),
                            error=str(doc_err),
                        )

        # Send MCP-collected files (from send_file_to_user tool calls) and
        # surface any paths rejected by bot-side validation so Claude's
        # "file sent" reply doesn't silently mislead the user.
        if mcp_files or mcp_rejected_files:
            from pathlib import Path as _P

            failed_files: list[str] = []
            for attachment in mcp_files:
                # TOCTOU-safe: refuse to send if path was swapped (symlink,
                # replacement) between validation and delivery.
                try:
                    fd = os.open(str(attachment.path), os.O_RDONLY | os.O_NOFOLLOW)
                except OSError as file_err:
                    logger.warning(
                        "TOCTOU-safe open failed for MCP document",
                        path=str(attachment.path),
                        error=str(file_err),
                    )
                    failed_files.append(attachment.path.name)
                    continue

                try:
                    file_stat = os.fstat(fd)
                    if (
                        file_stat.st_ino != attachment.inode
                        or file_stat.st_dev != attachment.device
                    ):
                        logger.warning(
                            "File identity changed since validation — " "refusing send",
                            path=str(attachment.path),
                        )
                        os.close(fd)
                        failed_files.append(attachment.path.name)
                        continue

                    with os.fdopen(fd, "rb") as f:
                        await update.message.reply_document(
                            document=f,
                            filename=attachment.path.name,
                            caption=attachment.caption or None,
                            reply_to_message_id=update.message.message_id,
                        )
                    await asyncio.sleep(0.5)
                except Exception as file_err:
                    logger.warning(
                        "Failed to send MCP document",
                        path=str(attachment.path),
                        error=str(file_err),
                    )
                    failed_files.append(attachment.path.name)
                    try:
                        os.close(fd)
                    except OSError:
                        pass

            summary_lines: list[str] = []
            if failed_files:
                summary_lines.append(f"⚠️ Failed to send: {', '.join(failed_files)}")
            if mcp_rejected_files:
                rejected_names = ", ".join(_P(p).name or p for p in mcp_rejected_files)
                summary_lines.append(
                    "🚫 Rejected by security policy "
                    "(outside APPROVED_DIRECTORY or blocked secret file): "
                    f"{rejected_names}"
                )
            if summary_lines:
                try:
                    await update.message.reply_text(
                        "\n".join(summary_lines),
                        reply_to_message_id=update.message.message_id,
                    )
                except Exception as summary_err:
                    logger.debug(
                        "Failed to send document error summary",
                        error=str(summary_err),
                    )

        # Update session info
        context.user_data["last_message"] = update.message.text

        # Add conversation enhancements if available
        features = context.bot_data.get("features")
        conversation_enhancer = (
            features.get_conversation_enhancer() if features else None
        )

        if conversation_enhancer and claude_response:
            try:
                # Update conversation context
                conversation_enhancer.update_context(user_id, claude_response)
                conversation_context = conversation_enhancer.get_or_create_context(
                    user_id
                )

                # Check if we should show follow-up suggestions
                if conversation_enhancer.should_show_suggestions(claude_response):
                    # Generate follow-up suggestions
                    suggestions = conversation_enhancer.generate_follow_up_suggestions(
                        claude_response,
                        conversation_context,
                    )

                    if suggestions:
                        # Create keyboard with suggestions
                        suggestion_keyboard = (
                            conversation_enhancer.create_follow_up_keyboard(suggestions)
                        )

                        # Send follow-up suggestions
                        await update.message.reply_text(
                            "💡 <b>What would you like to do next?</b>",
                            parse_mode="HTML",
                            reply_markup=suggestion_keyboard,
                        )

            except Exception as e:
                logger.warning(
                    "Conversation enhancement failed", error=str(e), user_id=user_id
                )

        # Log successful message processing
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id,
                command="text_message",
                args=[update.message.text[:100]],  # First 100 chars
                success=True,
            )

        logger.info("Text message processed successfully", user_id=user_id)

    except Exception as e:
        # Clean up progress message if it exists
        try:
            await progress_msg.delete()
        except Exception as delete_error:
            logger.debug("Failed to delete progress message", error=str(delete_error))

        await update.message.reply_text(_format_error_message(e), parse_mode="HTML")

        # Log failed processing
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id,
                command="text_message",
                args=[update.message.text[:100]],
                success=False,
            )

        logger.error("Error processing text message", error=str(e), user_id=user_id)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle file uploads."""
    user_id = update.effective_user.id
    document = update.message.document
    settings: Settings = context.bot_data["settings"]

    # Initialize prompt to avoid UnboundLocalError
    prompt: str = ""

    # Get services
    security_validator: Optional[SecurityValidator] = context.bot_data.get(
        "security_validator"
    )
    audit_logger: Optional[AuditLogger] = context.bot_data.get("audit_logger")
    rate_limiter: Optional[RateLimiter] = context.bot_data.get("rate_limiter")

    logger.info(
        "Processing document upload",
        user_id=user_id,
        filename=document.file_name,
        file_size=document.file_size,
    )

    try:
        # Validate filename using security validator
        if security_validator:
            valid, error = security_validator.validate_filename(document.file_name)
            if not valid:
                await update.message.reply_text(
                    f"❌ <b>File Upload Rejected</b>\n\n{escape_html(error)}",
                    parse_mode="HTML",
                )

                # Log security violation
                if audit_logger:
                    await audit_logger.log_security_violation(
                        user_id=user_id,
                        violation_type="invalid_file_upload",
                        details=f"Filename: {document.file_name}, Error: {error}",
                        severity="medium",
                    )
                return

        # Check file size limits
        max_size = 10 * 1024 * 1024  # 10MB
        if document.file_size > max_size:
            await update.message.reply_text(
                f"❌ <b>File Too Large</b>\n\n"
                f"Maximum file size: {max_size // 1024 // 1024}MB\n"
                f"Your file: {document.file_size / 1024 / 1024:.1f}MB",
                parse_mode="HTML",
            )
            return

        # Check rate limit for file processing
        file_cost = _estimate_file_processing_cost(document.file_size)
        if rate_limiter:
            allowed, limit_message = await rate_limiter.check_rate_limit(
                user_id, file_cost
            )
            if not allowed:
                await update.message.reply_text(f"⏱️ {limit_message}")
                return

        # Send processing indicator
        await update.message.chat.send_action("upload_document")

        progress_msg = await update.message.reply_text(
            f"📄 Processing file: <code>{document.file_name}</code>...",
            parse_mode="HTML",
        )

        # Check if enhanced file handler is available
        features = context.bot_data.get("features")
        file_handler = features.get_file_handler() if features else None

        if file_handler:
            # Use enhanced file handler
            try:
                processed_file = await file_handler.handle_document_upload(
                    document,
                    user_id,
                    update.message.caption or "Please review this file:",
                )
                prompt = processed_file.prompt

                # Update progress message with file type info
                await progress_msg.edit_text(
                    f"📄 Processing {processed_file.type} file: <code>{document.file_name}</code>...",
                    parse_mode="HTML",
                )

            except Exception as e:
                logger.warning(
                    "Enhanced file handler failed, falling back to basic handler",
                    error=str(e),
                )
                file_handler = None  # Fall back to basic handling

        if not file_handler:
            # Fall back to basic file handling
            file = await document.get_file()
            file_bytes = await file.download_as_bytearray()

            # Try to decode as text
            try:
                content = file_bytes.decode("utf-8")

                # Check content length
                max_content_length = 50000  # 50KB of text
                if len(content) > max_content_length:
                    content = (
                        content[:max_content_length]
                        + "\n... (file truncated for processing)"
                    )

                # Create prompt with file content
                caption = update.message.caption or "Please review this file:"
                prompt = f"{caption}\n\n**File:** `{document.file_name}`\n\n```\n{content}\n```"

            except UnicodeDecodeError:
                await progress_msg.edit_text(
                    "❌ <b>File Format Not Supported</b>\n\n"
                    "File must be text-based and UTF-8 encoded.\n\n"
                    "<b>Supported formats:</b>\n"
                    "• Source code files (.py, .js, .ts, etc.)\n"
                    "• Text files (.txt, .md)\n"
                    "• Configuration files (.json, .yaml, .toml)\n"
                    "• Documentation files",
                    parse_mode="HTML",
                )
                return

        # Delete progress message
        await progress_msg.delete()

        # Create a new progress message for Claude processing
        claude_progress_msg = await update.message.reply_text(
            "🤖 Processing file with Claude...", parse_mode="HTML"
        )

        # Get Claude integration from context
        claude_integration = context.bot_data.get("claude_integration")

        if not claude_integration:
            await claude_progress_msg.edit_text(
                "❌ <b>Claude integration not available</b>\n\n"
                "The Claude Code integration is not properly configured.",
                parse_mode="HTML",
            )
            return

        # Get current directory and session
        current_dir = context.user_data.get(
            "current_directory", settings.approved_directory
        )
        session_id = context.user_data.get("claude_session_id")

        # Process with Claude
        try:
            claude_response = await claude_integration.run_command(
                prompt=prompt,
                working_directory=current_dir,
                user_id=user_id,
                session_id=session_id,
            )

            # Update session ID
            context.user_data["claude_session_id"] = claude_response.session_id

            # Check if Claude changed the working directory and update our tracking
            _update_working_directory_from_claude_response(
                claude_response, context, settings, user_id
            )

            # Format and send response
            from ..utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

            # Delete progress message
            await claude_progress_msg.delete()

            # Send responses
            for i, message in enumerate(formatted_messages):
                await update.message.reply_text(
                    message.text,
                    parse_mode=message.parse_mode,
                    reply_markup=message.reply_markup,
                    reply_to_message_id=(update.message.message_id if i == 0 else None),
                )

                if i < len(formatted_messages) - 1:
                    await asyncio.sleep(0.5)

        except Exception as e:
            await claude_progress_msg.edit_text(
                _format_error_message(e), parse_mode="HTML"
            )
            logger.error("Claude file processing failed", error=str(e), user_id=user_id)

        # Log successful file processing
        if audit_logger:
            await audit_logger.log_file_access(
                user_id=user_id,
                file_path=document.file_name,
                action="upload_processed",
                success=True,
                file_size=document.file_size,
            )

    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception as delete_error:
            logger.debug("Failed to delete progress message", error=str(delete_error))

        error_msg = f"❌ <b>Error processing file</b>\n\n{escape_html(str(e))}"
        await update.message.reply_text(error_msg, parse_mode="HTML")

        # Log failed file processing
        if audit_logger:
            await audit_logger.log_file_access(
                user_id=user_id,
                file_path=document.file_name,
                action="upload_failed",
                success=False,
                file_size=document.file_size,
            )

        logger.error("Error processing document", error=str(e), user_id=user_id)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo uploads."""
    user_id = update.effective_user.id
    settings: Settings = context.bot_data["settings"]

    # Check if enhanced image handler is available
    features = context.bot_data.get("features")
    image_handler = features.get_image_handler() if features else None

    if image_handler:
        try:
            # Send processing indicator
            progress_msg = await update.message.reply_text(
                "📸 Processing image...", parse_mode="HTML"
            )

            # Get the largest photo size
            photo = update.message.photo[-1]

            # Process image with enhanced handler
            processed_image = await image_handler.process_image(
                photo, update.message.caption
            )

            # Delete progress message
            await progress_msg.delete()

            # Create Claude progress message
            claude_progress_msg = await update.message.reply_text(
                "🤖 Analyzing image with Claude...", parse_mode="HTML"
            )

            # Get Claude integration
            claude_integration = context.bot_data.get("claude_integration")

            if not claude_integration:
                await claude_progress_msg.edit_text(
                    "❌ <b>Claude integration not available</b>\n\n"
                    "The Claude Code integration is not properly configured.",
                    parse_mode="HTML",
                )
                return

            # Get current directory and session
            current_dir = context.user_data.get(
                "current_directory", settings.approved_directory
            )
            session_id = context.user_data.get("claude_session_id")

            # Process with Claude
            try:
                claude_response = await claude_integration.run_command(
                    prompt=processed_image.prompt,
                    working_directory=current_dir,
                    user_id=user_id,
                    session_id=session_id,
                )

                # Update session ID
                context.user_data["claude_session_id"] = claude_response.session_id

                # Format and send response
                from ..utils.formatting import ResponseFormatter

                formatter = ResponseFormatter(settings)
                formatted_messages = formatter.format_claude_response(
                    claude_response.content
                )

                # Delete progress message
                await claude_progress_msg.delete()

                # Send responses
                for i, message in enumerate(formatted_messages):
                    await update.message.reply_text(
                        message.text,
                        parse_mode=message.parse_mode,
                        reply_markup=message.reply_markup,
                        reply_to_message_id=(
                            update.message.message_id if i == 0 else None
                        ),
                    )

                    if i < len(formatted_messages) - 1:
                        await asyncio.sleep(0.5)

            except Exception as e:
                await claude_progress_msg.edit_text(
                    _format_error_message(e), parse_mode="HTML"
                )
                logger.error(
                    "Claude image processing failed", error=str(e), user_id=user_id
                )

        except Exception as e:
            logger.error("Image processing failed", error=str(e), user_id=user_id)
            await update.message.reply_text(
                _format_error_message(e),
                parse_mode="HTML",
            )
    else:
        # Fall back to unsupported message
        await update.message.reply_text(
            "📸 <b>Photo Upload</b>\n\n"
            "Photo processing is not yet supported.\n\n"
            "<b>Currently supported:</b>\n"
            "• Text files (.py, .js, .md, etc.)\n"
            "• Configuration files\n"
            "• Documentation files\n\n"
            "<b>Coming soon:</b>\n"
            "• Image analysis\n"
            "• Screenshot processing\n"
            "• Diagram interpretation",
            parse_mode="HTML",
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice message uploads."""
    user_id = update.effective_user.id
    settings: Settings = context.bot_data["settings"]

    features = context.bot_data.get("features")
    voice_handler = features.get_voice_handler() if features else None

    if not voice_handler:
        if settings.voice_provider == "local":
            await update.message.reply_text(
                "🎙️ <b>Voice Messages</b>\n\n"
                "Voice transcription is not available.\n"
                "Provider: <code>Local whisper.cpp</code>\n"
                "Ensure whisper.cpp is installed and model file exists.\n"
                "Set <code>WHISPER_CPP_BINARY_PATH</code> and "
                "<code>WHISPER_CPP_MODEL_PATH</code> if needed.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "🎙️ <b>Voice Messages</b>\n\n"
                "Voice transcription is not available.\n"
                f"Provider: <code>{settings.voice_provider_display_name}</code>\n"
                f"Set <code>{settings.voice_provider_api_key_env}</code> to enable.\n"
                "Install optional voice deps with "
                '<code>pip install "claude-code-telegram[voice]"</code>.',
                parse_mode="HTML",
            )
        return

    try:
        progress_msg = await update.message.reply_text(
            "🎙️ Transcribing voice message...", parse_mode="HTML"
        )

        voice = update.message.voice
        processed_voice = await voice_handler.process_voice_message(
            voice, update.message.caption
        )

        await progress_msg.edit_text(
            "🤖 Processing transcription with Claude...", parse_mode="HTML"
        )

        claude_integration = context.bot_data.get("claude_integration")
        if not claude_integration:
            await progress_msg.edit_text(
                "❌ <b>Claude integration not available</b>\n\n"
                "The Claude Code integration is not properly configured.",
                parse_mode="HTML",
            )
            return

        current_dir = context.user_data.get(
            "current_directory", settings.approved_directory
        )
        session_id = context.user_data.get("claude_session_id")

        try:
            # Keep classic mode aligned with handle_photo: single progress message,
            # no streaming callback or typing heartbeat.
            claude_response = await claude_integration.run_command(
                prompt=processed_voice.prompt,
                working_directory=current_dir,
                user_id=user_id,
                session_id=session_id,
            )

            context.user_data["claude_session_id"] = claude_response.session_id

            _update_working_directory_from_claude_response(
                claude_response, context, settings, user_id
            )

            from ..utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

            await progress_msg.delete()

            for i, message in enumerate(formatted_messages):
                await update.message.reply_text(
                    message.text,
                    parse_mode=message.parse_mode,
                    reply_markup=message.reply_markup,
                    reply_to_message_id=(update.message.message_id if i == 0 else None),
                )
                if i < len(formatted_messages) - 1:
                    await asyncio.sleep(0.5)

        except Exception as e:
            await progress_msg.edit_text(_format_error_message(e), parse_mode="HTML")
            logger.error(
                "Claude voice processing failed", error=str(e), user_id=user_id
            )

    except Exception as e:
        logger.error("Voice processing failed", error=str(e), user_id=user_id)
        await update.message.reply_text(
            _format_error_message(e),
            parse_mode="HTML",
        )


def _estimate_text_processing_cost(text: str) -> float:
    """Estimate cost for processing text message."""
    # Base cost
    base_cost = 0.001

    # Additional cost based on length
    length_cost = len(text) * 0.00001

    # Additional cost for complex requests
    complex_keywords = [
        "analyze",
        "generate",
        "create",
        "build",
        "implement",
        "refactor",
        "optimize",
        "debug",
        "explain",
        "document",
    ]

    text_lower = text.lower()
    complexity_multiplier = 1.0

    for keyword in complex_keywords:
        if keyword in text_lower:
            complexity_multiplier += 0.5

    return (base_cost + length_cost) * min(complexity_multiplier, 3.0)


def _estimate_file_processing_cost(file_size: int) -> float:
    """Estimate cost for processing uploaded file."""
    # Base cost for file handling
    base_cost = 0.005

    # Additional cost based on file size (per KB)
    size_cost = (file_size / 1024) * 0.0001

    return base_cost + size_cost


async def _generate_placeholder_response(
    message_text: str, context: ContextTypes.DEFAULT_TYPE
) -> dict:
    """Generate placeholder response until Claude integration is implemented."""
    settings: Settings = context.bot_data["settings"]
    current_dir = getattr(
        context.user_data, "current_directory", settings.approved_directory
    )
    relative_path = current_dir.relative_to(settings.approved_directory)

    # Analyze the message for intent
    message_lower = message_text.lower()

    if any(
        word in message_lower for word in ["list", "show", "see", "directory", "files"]
    ):
        response_text = (
            f"🤖 <b>Claude Code Response</b> <i>(Placeholder)</i>\n\n"
            f"I understand you want to see files. Try using the /ls command to list files "
            f"in your current directory (<code>{relative_path}/</code>).\n\n"
            f"<b>Available commands:</b>\n"
            f"• /ls - List files\n"
            f"• /cd &lt;dir&gt; - Change directory\n"
            f"• /projects - Show projects\n\n"
            f"<i>Note: Full Claude Code integration will be available in the next phase.</i>"
        )

    elif any(word in message_lower for word in ["create", "generate", "make", "build"]):
        response_text = (
            f"🤖 <b>Claude Code Response</b> <i>(Placeholder)</i>\n\n"
            f"I understand you want to create something! Once the Claude Code integration "
            f"is complete, I'll be able to:\n\n"
            f"• Generate code files\n"
            f"• Create project structures\n"
            f"• Write documentation\n"
            f"• Build complete applications\n\n"
            f"<b>Current directory:</b> <code>{relative_path}/</code>\n\n"
            f"<i>Full functionality coming soon!</i>"
        )

    elif any(word in message_lower for word in ["help", "how", "what", "explain"]):
        response_text = (
            "🤖 <b>Claude Code Response</b> <i>(Placeholder)</i>\n\n"
            "I'm here to help! Try using /help for available commands.\n\n"
            "<b>What I can do now:</b>\n"
            "• Navigate directories (/cd, /ls, /pwd)\n"
            "• Show projects (/projects)\n"
            "• Manage sessions (/new, /status)\n\n"
            "<b>Coming soon:</b>\n"
            "• Full Claude Code integration\n"
            "• Code generation and editing\n"
            "• File operations\n"
            "• Advanced programming assistance"
        )

    else:
        response_text = (
            f"🤖 <b>Claude Code Response</b> <i>(Placeholder)</i>\n\n"
            f"I received your message: \"{message_text[:100]}{'...' if len(message_text) > 100 else ''}\"\n\n"
            f"<b>Current Status:</b>\n"
            f"• Directory: <code>{relative_path}/</code>\n"
            f"• Bot core: ✅ Active\n"
            f"• Claude integration: 🔄 Coming soon\n\n"
            f"Once Claude Code integration is complete, I'll be able to process your "
            f"requests fully and help with coding tasks!\n\n"
            f"For now, try the available commands like /ls, /cd, and /help."
        )

    return {"text": response_text, "parse_mode": "HTML"}


def _update_working_directory_from_claude_response(
    claude_response, context, settings, user_id
):
    """Update the working directory based on Claude's response content."""
    import re
    from pathlib import Path

    # Look for directory changes in Claude's response
    # This searches for common patterns that indicate directory changes
    patterns = [
        r"(?:^|\n).*?cd\s+([^\s\n]+)",  # cd command
        r"(?:^|\n).*?Changed directory to:?\s*([^\s\n]+)",  # explicit directory change
        r"(?:^|\n).*?Current directory:?\s*([^\s\n]+)",  # current directory indication
        r"(?:^|\n).*?Working directory:?\s*([^\s\n]+)",  # working directory indication
    ]

    content = claude_response.content.lower()
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    for pattern in patterns:
        matches = re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)
        for match in matches:
            try:
                # Clean up the path
                new_path = match.strip().strip("\"'`")

                # Handle relative paths
                if new_path.startswith("./") or new_path.startswith("../"):
                    new_path = (current_dir / new_path).resolve()
                elif not new_path.startswith("/"):
                    # Relative path without ./
                    new_path = (current_dir / new_path).resolve()
                else:
                    # Absolute path
                    new_path = Path(new_path).resolve()

                # Validate that the new path is within the approved directory
                if (
                    new_path.is_relative_to(settings.approved_directory)
                    and new_path.exists()
                ):
                    context.user_data["current_directory"] = new_path
                    logger.info(
                        "Updated working directory from Claude response",
                        old_dir=str(current_dir),
                        new_dir=str(new_path),
                        user_id=user_id,
                    )
                    return  # Take the first valid match

            except (ValueError, OSError) as e:
                # Invalid path, skip this match
                logger.debug(
                    "Invalid path in Claude response", path=match, error=str(e)
                )
                continue
