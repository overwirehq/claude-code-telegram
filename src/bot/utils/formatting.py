"""Format bot responses for optimal display."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ...config.settings import Settings
from .html_format import escape_html, markdown_to_telegram_html

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from ...claude.sdk_integration import ClaudeResponse


# Longest tool argument shown inside the blocked-calls list.
DENIAL_ARG_MAX_LEN = 40
# Blocked calls listed in full before the rest are summarised as "and N more".
DENIAL_LIST_MAX = 5
# Longest CLI error string echoed into the footer.
STOP_DETAIL_MAX_LEN = 200
# Shown in place of a stop reason when the user pressed Stop themselves.
INTERRUPTED_NOTE = "_(Interrupted by user)_"

# ResultMessage.subtype -> the clause that follows "Stopped: ". Only
# non-success subtypes appear; the CLI's vocabulary is open-ended, so anything
# unrecognised falls back to a generic clause naming the raw value.
SUBTYPE_STOP_REASONS = {
    "error_max_turns": "turn limit reached",
    "error_max_budget_usd": "cost budget reached",
    "error_during_execution": "the run hit an error and could not continue",
}

# ResultMessage.terminal_reason -> the same clause, preferred over the subtype
# when it is one we recognise. The SDK types this ``str | None`` with no enum,
# so unknown values are ignored rather than guessed at.
TERMINAL_STOP_REASONS = {
    "max_turns": "turn limit reached",
    "aborted_streaming": "the run was cancelled",
    "aborted_tools": "the run was cancelled while a tool was running",
    "api_error": "the API returned an error",
    "budget_exceeded": "cost budget reached",
    "max_budget": "cost budget reached",
}

# Clauses that already say everything the CLI's own prose would; echoing
# errors[] under one of these just repeats the sentence above it.
SELF_EXPLANATORY_STOP_REASONS = {
    "turn limit reached",
    "cost budget reached",
    "the run was cancelled",
    "the run was cancelled while a tool was running",
}


def _shorten(text: str, limit: int) -> str:
    """Collapse whitespace and clip to ``limit`` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _inline_code(text: str) -> str:
    """Wrap machine output so the Markdown pass leaves it alone.

    The footer is appended to Claude's reply and goes through
    ``markdown_to_telegram_html`` with it, which italicises ``_like this_``.
    That mangles paths and shell commands, and on a line listing several
    denials the italics bleed from one entry into the next. Inline code is
    extracted before any Markdown conversion and escaped verbatim, so it is
    the one wrapper that survives.

    Backticks in the value are kept. Deleting them would silently rewrite the
    thing being reported -- ``echo `whoami`` runs a command, ``echo whoami``
    prints a word -- and the reader cannot tell a rewrite from the real
    argument. Clipping for length is visible, because it leaves an ellipsis;
    this would not be. So the delimiter is a run one backtick longer than the
    longest run inside the value, which closes only on a run of its own
    length, and a value that begins or ends with a backtick is padded with a
    space at each end for the Markdown pass to strip back off.
    """
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest_run + 1)
    if text.startswith("`") or text.endswith("`"):
        text = f" {text} "
    return f"{fence}{text}{fence}"


def _denial_argument(tool_input: Dict[str, Any]) -> str:
    """Pick the most identifying argument of a blocked tool call."""
    if not isinstance(tool_input, dict):
        return ""

    for key in ("file_path", "path", "notebook_path", "command", "pattern", "url"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _shorten(value, DENIAL_ARG_MAX_LEN)
    return ""


def format_permission_denials(denials: List[Dict[str, Any]]) -> Optional[str]:
    """Summarise the tool calls a run had blocked, or None if there were none.

    This bot generates denials itself -- every APPROVED_DIRECTORY rejection,
    every Bash boundary violation, every Deny on an interactive approval
    prompt -- and until now the only account of them a user saw was Claude's
    own narration, which is not authoritative.
    """
    if not denials or not isinstance(denials, list):
        return None

    # Filtered before slicing: a run of malformed entries at the front would
    # otherwise swallow the whole list and report nothing was blocked.
    usable = [denial for denial in denials if isinstance(denial, dict)]

    described: List[str] = []
    for denial in usable[:DENIAL_LIST_MAX]:
        name = str(denial.get("tool_name") or "unknown")
        argument = _denial_argument(denial.get("tool_input") or {})
        described.append(f"{name}({_inline_code(argument)})" if argument else name)

    if not described:
        return None

    remaining = len(denials) - len(described)
    if remaining > 0:
        described.append(f"and {remaining} more")

    count = len(denials)
    noun = "tool call was" if count == 1 else "tool calls were"
    return f"🚫 {count} {noun} blocked: " + ", ".join(described)


def format_stop_reason(response: "ClaudeResponse") -> Optional[str]:
    """Build the footer explaining why a run ended, or None if it ended cleanly.

    A run killed at the turn limit produces no final text, so without this the
    bot falls through to its "Task completed" placeholder and reports a
    truncated run as a success (#172).

    The footer is appended to Claude's reply and renders with it, so the parts
    that carry machine output -- tool arguments, the CLI's own error prose --
    are wrapped as inline code to survive the Markdown pass unchanged. See
    :func:`_inline_code`.
    """
    lines: List[str] = []

    if response.interrupted:
        # The user pressed Stop, so they know why this one ended; naming the
        # cancellation a second time would only be noise. The blocked calls
        # below are still worth having.
        lines.append(INTERRUPTED_NOTE)
    elif not response.completed_normally:
        terminal = (response.terminal_reason or "").strip().lower()
        subtype = (response.result_subtype or "").strip().lower()
        reason = TERMINAL_STOP_REASONS.get(terminal) or SUBTYPE_STOP_REASONS.get(
            subtype
        )
        if reason is None:
            reason = f"the run ended early ({subtype or terminal or 'unknown reason'})"

        sentence = f"⚠️ Stopped: {reason}"
        if response.num_turns:
            turns = "turn" if response.num_turns == 1 else "turns"
            sentence += f" after {response.num_turns} {turns}"
        lines.append(sentence + ". Send a message to continue.")

        # A terminal error carries its prose in errors[]; for anything the
        # clause above does not already explain, that is the only place the
        # actual cause appears.
        if reason not in SELF_EXPLANATORY_STOP_REASONS:
            detail = next((e.strip() for e in response.errors if e and e.strip()), None)
            if detail:
                lines.append(_inline_code(_shorten(detail, STOP_DETAIL_MAX_LEN)))

    denials = format_permission_denials(response.permission_denials)
    if denials:
        lines.append(denials)

    if not lines:
        return None
    return "\n\n" + "\n".join(lines)


def with_stop_reason(response: "ClaudeResponse") -> str:
    """Claude's reply plus its stop-reason footer.

    Every place the bot renders a Claude reply goes through this, so a run
    truncated at the turn limit cannot read as a success through one entry
    point while saying so through another (#172, #230).
    """
    return (response.content or "") + (format_stop_reason(response) or "")


@dataclass
class FormattedMessage:
    """Represents a formatted message for Telegram."""

    text: str
    parse_mode: str = "HTML"
    reply_markup: Optional[InlineKeyboardMarkup] = None

    def __len__(self) -> int:
        """Return length of message text."""
        return len(self.text)


class ResponseFormatter:
    """Format Claude responses for Telegram display."""

    def __init__(self, settings: Settings):
        """Initialize formatter with settings."""
        self.settings = settings
        self.max_message_length = 4000  # Telegram limit is 4096, leave some buffer
        self.max_code_block_length = (
            15000  # Max length for individual code blocks before splitting
        )

    def format_claude_response(
        self, text: str, context: Optional[dict] = None
    ) -> List[FormattedMessage]:
        """Enhanced formatting with context awareness and semantic chunking."""
        # Clean and prepare text
        text = self._clean_text(text)

        # Check if we need semantic chunking (for complex content)
        if self._should_use_semantic_chunking(text):
            # Use enhanced semantic chunking for complex content
            chunks = self._semantic_chunk(text, context)
            messages = []
            for chunk in chunks:
                formatted = self._format_chunk(chunk)
                messages.extend(formatted)
        else:
            # Use original simple formatting for basic content
            text = self._format_code_blocks(text)
            messages = self._split_message(text)

        # Add context-aware quick actions to the last message
        if messages and self.settings.enable_quick_actions:
            messages[-1].reply_markup = self._get_contextual_keyboard(context)

        # Filter out any empty messages produced by formatting/splitting
        messages = [m for m in messages if m.text and m.text.strip()]

        return (
            messages
            if messages
            else [FormattedMessage("<i>(No content to display)</i>")]
        )

    def _should_use_semantic_chunking(self, text: str) -> bool:
        """Determine if semantic chunking is needed."""
        # Use semantic chunking for complex content with multiple code blocks,
        # file operations, or very long text
        code_block_count = text.count("```")
        has_file_operations = any(
            indicator in text
            for indicator in [
                "Creating file",
                "Editing file",
                "Reading file",
                "Writing to",
                "Modified file",
                "Deleted file",
                "File created",
                "File updated",
            ]
        )
        is_very_long = len(text) > self.max_message_length * 2

        return code_block_count > 2 or has_file_operations or is_very_long

    def format_error_message(
        self, error: str, error_type: str = "Error"
    ) -> FormattedMessage:
        """Format error message with appropriate styling."""
        icon = {
            "Error": "❌",
            "Warning": "⚠️",
            "Info": "ℹ️",
            "Security": "🛡️",
            "Rate Limit": "⏱️",
        }.get(error_type, "❌")

        text = f"{icon} <b>{escape_html(error_type)}</b>\n\n{escape_html(error)}"

        return FormattedMessage(text, parse_mode="HTML")

    def format_success_message(
        self, message: str, title: str = "Success"
    ) -> FormattedMessage:
        """Format success message with appropriate styling."""
        text = f"✅ <b>{escape_html(title)}</b>\n\n{escape_html(message)}"
        return FormattedMessage(text, parse_mode="HTML")

    def format_info_message(
        self, message: str, title: str = "Info"
    ) -> FormattedMessage:
        """Format info message with appropriate styling."""
        text = f"ℹ️ <b>{escape_html(title)}</b>\n\n{escape_html(message)}"
        return FormattedMessage(text, parse_mode="HTML")

    def format_code_output(
        self, output: str, language: str = "", title: str = "Output"
    ) -> List[FormattedMessage]:
        """Format code output with syntax highlighting."""
        if not output.strip():
            return [
                FormattedMessage(
                    f"📄 <b>{escape_html(title)}</b>\n\n<i>(empty output)</i>"
                )
            ]

        escaped_output = escape_html(output)

        # Check if the code block is too long
        if len(escaped_output) > self.max_code_block_length:
            escaped_output = (
                escape_html(output[: self.max_code_block_length - 100])
                + "\n... (output truncated)"
            )

        if language:
            code_block = f'<pre><code class="language-{escape_html(language)}">{escaped_output}</code></pre>'
        else:
            code_block = f"<pre><code>{escaped_output}</code></pre>"

        text = f"📄 <b>{escape_html(title)}</b>\n\n{code_block}"

        return self._split_message(text)

    def format_file_list(
        self, files: List[str], directory: str = ""
    ) -> FormattedMessage:
        """Format file listing with appropriate icons."""
        safe_dir = escape_html(directory)
        if not files:
            text = f"📂 <b>{safe_dir}</b>\n\n<i>(empty directory)</i>"
        else:
            file_lines = []
            for file in files[:50]:  # Limit to 50 items
                safe_file = escape_html(file)
                if file.endswith("/"):
                    file_lines.append(f"📁 {safe_file}")
                else:
                    file_lines.append(f"📄 {safe_file}")

            file_text = "\n".join(file_lines)
            if len(files) > 50:
                file_text += f"\n\n<i>... and {len(files) - 50} more items</i>"

            text = f"📂 <b>{safe_dir}</b>\n\n{file_text}"

        return FormattedMessage(text, parse_mode="HTML")

    def format_progress_message(
        self, message: str, percentage: Optional[float] = None
    ) -> FormattedMessage:
        """Format progress message with optional progress bar."""
        safe_msg = escape_html(message)
        if percentage is not None:
            # Create simple progress bar
            filled = int(percentage / 10)
            empty = 10 - filled
            progress_bar = "▓" * filled + "░" * empty
            text = f"🔄 <b>{safe_msg}</b>\n\n{progress_bar} {percentage:.0f}%"
        else:
            text = f"🔄 <b>{safe_msg}</b>"

        return FormattedMessage(text, parse_mode="HTML")

    def _semantic_chunk(self, text: str, context: Optional[dict]) -> List[dict]:
        """Split text into semantic chunks based on content type."""
        chunks = []

        # Identify different content sections
        sections = self._identify_sections(text)

        for section in sections:
            if section["type"] == "code_block":
                chunks.extend(self._chunk_code_block(section))
            elif section["type"] == "explanation":
                chunks.extend(self._chunk_explanation(section))
            elif section["type"] == "file_operations":
                chunks.append(self._format_file_operations_section(section))
            elif section["type"] == "mixed":
                chunks.extend(self._chunk_mixed_content(section))
            else:
                # Default text chunking
                chunks.extend(self._chunk_text(section))

        return chunks

    def _identify_sections(self, text: str) -> List[dict]:
        """Identify different content types in the text."""
        sections = []
        lines = text.split("\n")
        current_section = {"type": "text", "content": "", "start_line": 0}
        in_code_block = False

        for i, line in enumerate(lines):
            # Check for code block markers
            if line.strip().startswith("```"):
                if not in_code_block:
                    # Start of code block
                    if current_section["content"].strip():
                        sections.append(current_section)
                    in_code_block = True
                    current_section = {
                        "type": "code_block",
                        "content": line + "\n",
                        "start_line": i,
                    }
                else:
                    # End of code block
                    current_section["content"] += line + "\n"
                    sections.append(current_section)
                    in_code_block = False
                    current_section = {
                        "type": "text",
                        "content": "",
                        "start_line": i + 1,
                    }
            elif in_code_block:
                current_section["content"] += line + "\n"
            else:
                # Check for file operation patterns
                if self._is_file_operation_line(line):
                    if current_section["type"] != "file_operations":
                        if current_section["content"].strip():
                            sections.append(current_section)
                        current_section = {
                            "type": "file_operations",
                            "content": line + "\n",
                            "start_line": i,
                        }
                    else:
                        current_section["content"] += line + "\n"
                else:
                    # Regular text
                    if current_section["type"] != "text":
                        if current_section["content"].strip():
                            sections.append(current_section)
                        current_section = {
                            "type": "text",
                            "content": line + "\n",
                            "start_line": i,
                        }
                    else:
                        current_section["content"] += line + "\n"

        # Add the last section
        if current_section["content"].strip():
            sections.append(current_section)

        return sections

    def _is_file_operation_line(self, line: str) -> bool:
        """Check if a line indicates file operations."""
        file_indicators = [
            "Creating file",
            "Editing file",
            "Reading file",
            "Writing to",
            "Modified file",
            "Deleted file",
            "File created",
            "File updated",
        ]
        return any(indicator in line for indicator in file_indicators)

    def _chunk_code_block(self, section: dict) -> List[dict]:
        """Handle code block chunking."""
        content = section["content"]
        if len(content) <= self.max_code_block_length:
            return [{"type": "code_block", "content": content, "format": "single"}]

        # Split large code blocks
        chunks = []
        lines = content.split("\n")
        current_chunk = lines[0] + "\n"  # Start with the ``` line

        for line in lines[1:-1]:  # Skip first and last ``` lines
            if len(current_chunk + line + "\n```\n") > self.max_code_block_length:
                current_chunk += "```"
                chunks.append(
                    {"type": "code_block", "content": current_chunk, "format": "split"}
                )
                current_chunk = "```\n" + line + "\n"
            else:
                current_chunk += line + "\n"

        current_chunk += lines[-1]  # Add the closing ```
        chunks.append(
            {"type": "code_block", "content": current_chunk, "format": "split"}
        )

        return chunks

    def _chunk_explanation(self, section: dict) -> List[dict]:
        """Handle explanation text chunking."""
        content = section["content"]
        if len(content) <= self.max_message_length:
            return [{"type": "explanation", "content": content}]

        # Split by paragraphs first
        paragraphs = content.split("\n\n")
        chunks = []
        current_chunk = ""

        for paragraph in paragraphs:
            if len(current_chunk + paragraph + "\n\n") > self.max_message_length:
                if current_chunk:
                    chunks.append(
                        {"type": "explanation", "content": current_chunk.strip()}
                    )
                current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"

        if current_chunk:
            chunks.append({"type": "explanation", "content": current_chunk.strip()})

        return chunks

    def _chunk_mixed_content(self, section: dict) -> List[dict]:
        """Handle mixed content sections."""
        # For now, treat as regular text
        return self._chunk_text(section)

    def _chunk_text(self, section: dict) -> List[dict]:
        """Handle regular text chunking."""
        content = section["content"]
        if len(content) <= self.max_message_length:
            return [{"type": "text", "content": content}]

        # Split at natural break points
        chunks = []
        current_chunk = ""

        sentences = content.split(". ")
        for sentence in sentences:
            test_chunk = current_chunk + sentence + ". "
            if len(test_chunk) > self.max_message_length:
                if current_chunk:
                    chunks.append({"type": "text", "content": current_chunk.strip()})
                current_chunk = sentence + ". "
            else:
                current_chunk = test_chunk

        if current_chunk:
            chunks.append({"type": "text", "content": current_chunk.strip()})

        return chunks

    def _format_file_operations_section(self, section: dict) -> dict:
        """Format file operations section."""
        return {"type": "file_operations", "content": section["content"]}

    def _format_chunk(self, chunk: dict) -> List[FormattedMessage]:
        """Format individual chunks into FormattedMessage objects."""
        chunk_type = chunk["type"]
        content = chunk["content"]

        if chunk_type == "code_block":
            # Format code blocks with proper styling
            if chunk.get("format") == "split":
                title = (
                    "📄 <b>Code (continued)</b>"
                    if "continued" in content
                    else "📄 <b>Code</b>"
                )
            else:
                title = "📄 <b>Code</b>"

            text = f"{title}\n\n{content}"

        elif chunk_type == "file_operations":
            text = f"📁 <b>File Operations</b>\n\n{content}"

        elif chunk_type == "explanation":
            text = content

        else:
            text = content

        # Split if still too long
        return self._split_message(text)

    def _get_contextual_keyboard(
        self, context: Optional[dict]
    ) -> Optional[InlineKeyboardMarkup]:
        """Get context-aware quick action keyboard."""
        if not context:
            return self._get_quick_actions_keyboard()

        buttons = []

        # Add context-specific buttons
        if context.get("has_code"):
            buttons.append(
                [InlineKeyboardButton("💾 Save Code", callback_data="save_code")]
            )

        if context.get("has_file_operations"):
            buttons.append(
                [InlineKeyboardButton("📁 Show Files", callback_data="show_files")]
            )

        if context.get("has_errors"):
            buttons.append([InlineKeyboardButton("🔧 Debug", callback_data="debug")])

        # Add default actions
        default_buttons = [
            [InlineKeyboardButton("🔄 Continue", callback_data="continue")],
            [InlineKeyboardButton("💡 Explain", callback_data="explain")],
        ]
        buttons.extend(default_buttons)

        return InlineKeyboardMarkup(buttons) if buttons else None

    def _clean_text(self, text: str) -> str:
        """Clean text for Telegram display."""
        # Remove excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Convert markdown to Telegram HTML
        text = markdown_to_telegram_html(text)

        return text.strip()

    def _format_code_blocks(self, text: str) -> str:
        """Ensure code blocks are properly formatted for Telegram.

        With HTML mode, markdown_to_telegram_html already handles code blocks.
        This method now just truncates oversized code blocks.
        """

        def _truncate_code(m: re.Match) -> str:  # type: ignore[type-arg]
            full = m.group(0)
            if len(full) > self.max_code_block_length:
                # Re-extract and truncate the inner content
                inner = m.group(1)
                truncated = inner[: self.max_code_block_length - 80]
                return (
                    f"<pre><code>{escape_html(truncated)}\n... (truncated)</code></pre>"
                )
            return full

        return re.sub(
            r"<pre><code[^>]*>(.*?)</code></pre>",
            _truncate_code,
            text,
            flags=re.DOTALL,
        )

    def _split_message(self, text: str) -> List[FormattedMessage]:
        """Split long messages while preserving formatting."""
        if not text or not text.strip():
            return []
        if len(text) <= self.max_message_length:
            return [FormattedMessage(text)]

        messages = []
        current_lines: List[str] = []
        current_length = 0
        in_code_block = False

        lines = text.split("\n")

        for line in lines:
            line_length = len(line) + 1  # +1 for newline

            # Track HTML <pre> code block state
            if "<pre>" in line or "<pre><code" in line:
                in_code_block = True
            if "</pre>" in line:
                in_code_block = False

            # If this is a very long line that exceeds limit by itself, split it
            if line_length > self.max_message_length:
                chunks = []
                for i in range(0, len(line), self.max_message_length - 100):
                    chunks.append(line[i : i + self.max_message_length - 100])

                for chunk in chunks:
                    chunk_length = len(chunk) + 1

                    if (
                        current_length + chunk_length > self.max_message_length
                        and current_lines
                    ):
                        if in_code_block:
                            current_lines.append("</code></pre>")
                        messages.append(FormattedMessage("\n".join(current_lines)))

                        current_lines = []
                        current_length = 0
                        if in_code_block:
                            current_lines.append("<pre><code>")
                            current_length = 12

                    current_lines.append(chunk)
                    current_length += chunk_length
                continue

            # Check if adding this line would exceed the limit
            if current_length + line_length > self.max_message_length and current_lines:
                if in_code_block:
                    current_lines.append("</code></pre>")

                messages.append(FormattedMessage("\n".join(current_lines)))

                current_lines = []
                current_length = 0

                if in_code_block:
                    current_lines.append("<pre><code>")
                    current_length = 12

            current_lines.append(line)
            current_length += line_length

        # Add remaining content
        if current_lines:
            messages.append(FormattedMessage("\n".join(current_lines)))

        return messages

    def _get_quick_actions_keyboard(self) -> InlineKeyboardMarkup:
        """Get quick actions inline keyboard."""
        keyboard = [
            [
                InlineKeyboardButton("🧪 Test", callback_data="quick:test"),
                InlineKeyboardButton("📦 Install", callback_data="quick:install"),
                InlineKeyboardButton("🎨 Format", callback_data="quick:format"),
            ],
            [
                InlineKeyboardButton("🔍 Find TODOs", callback_data="quick:find_todos"),
                InlineKeyboardButton("🔨 Build", callback_data="quick:build"),
                InlineKeyboardButton("📊 Git Status", callback_data="quick:git_status"),
            ],
        ]

        return InlineKeyboardMarkup(keyboard)

    def create_confirmation_keyboard(
        self, confirm_data: str, cancel_data: str = "confirm:no"
    ) -> InlineKeyboardMarkup:
        """Create a confirmation keyboard."""
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes", callback_data=confirm_data),
                InlineKeyboardButton("❌ No", callback_data=cancel_data),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_navigation_keyboard(self, options: List[tuple]) -> InlineKeyboardMarkup:
        """Create navigation keyboard from options list.

        Args:
            options: List of (text, callback_data) tuples
        """
        keyboard = []
        current_row = []

        for text, callback_data in options:
            current_row.append(InlineKeyboardButton(text, callback_data=callback_data))

            # Create rows of 2 buttons
            if len(current_row) == 2:
                keyboard.append(current_row)
                current_row = []

        # Add remaining button if any
        if current_row:
            keyboard.append(current_row)

        return InlineKeyboardMarkup(keyboard)


class ProgressIndicator:
    """Helper for creating progress indicators."""

    @staticmethod
    def create_bar(
        percentage: float,
        length: int = 10,
        filled_char: str = "▓",
        empty_char: str = "░",
    ) -> str:
        """Create a progress bar."""
        filled = int((percentage / 100) * length)
        empty = length - filled
        return filled_char * filled + empty_char * empty

    @staticmethod
    def create_spinner(step: int) -> str:
        """Create a spinning indicator."""
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        return spinners[step % len(spinners)]

    @staticmethod
    def create_dots(step: int) -> str:
        """Create a dots indicator."""
        dots = ["", ".", "..", "..."]
        return dots[step % len(dots)]


class CodeHighlighter:
    """Simple code highlighting for common languages."""

    # Language file extensions mapping
    LANGUAGE_EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".sql": "sql",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
    }

    @classmethod
    def detect_language(cls, filename: str) -> str:
        """Detect programming language from filename."""
        from pathlib import Path

        ext = Path(filename).suffix.lower()
        return cls.LANGUAGE_EXTENSIONS.get(ext, "")

    @classmethod
    def format_code(cls, code: str, language: str = "", filename: str = "") -> str:
        """Format code with language detection, using HTML tags."""
        if not language and filename:
            language = cls.detect_language(filename)

        escaped_code = escape_html(code)
        if language:
            return f'<pre><code class="language-{escape_html(language)}">{escaped_code}</code></pre>'
        else:
            return f"<pre><code>{escaped_code}</code></pre>"
