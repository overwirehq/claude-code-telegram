"""Track working-directory changes announced in Claude's replies.

Shared by agentic mode (``src/bot/orchestrator.py``) and classic mode
(``src/bot/handlers/message.py``).  Lives here so that agentic mode does not
import from the classic handlers package.
"""

import re
from pathlib import Path

import structlog

logger = structlog.get_logger()


def _update_working_directory_from_claude_response(
    claude_response, context, settings, user_id
):
    """Update the working directory based on Claude's response content."""
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
