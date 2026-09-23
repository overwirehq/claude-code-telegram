"""MCP server exposing Telegram-specific tools to Claude.

Runs as a stdio transport server. The ``send_file_to_user`` tool validates
file existence and size, then returns a success string; ``send_image_to_user``
is kept as a deprecated image-only alias. Actual Telegram delivery is handled
by the bot's stream callback which intercepts the tool call and applies full
security checks (approved directory, secrets blocklist).
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Telegram Bot API document upload limit.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

mcp = FastMCP("telegram")


@mcp.tool()
async def send_file_to_user(file_path: str, caption: str = "") -> str:
    """Send a file of any type to the Telegram user as a document.

    Preferred tool for delivering files (PDF, zip, csv, logs, images, etc.)
    back to the user. Full security validation (approved directory, secrets
    blocklist) happens on the bot side; this tool only performs basic syntax
    checks so it can run without access to the bot's runtime configuration.

    Args:
        file_path: Absolute path to the file.
        caption: Optional caption to display with the file.

    Returns:
        Confirmation string when the file is queued for delivery.
    """
    path = Path(file_path)

    if not path.is_absolute():
        return f"Error: path must be absolute, got '{file_path}'"

    if not path.is_file():
        return f"Error: file not found: {file_path}"

    size = path.stat().st_size
    if size == 0:
        return f"Error: file is empty: {file_path}"
    if size > MAX_FILE_SIZE_BYTES:
        return (
            f"Error: file too large ({size} bytes). "
            f"Telegram Bot API limit is {MAX_FILE_SIZE_BYTES} bytes (50 MB)."
        )

    return f"File queued for delivery: {path.name}"


@mcp.tool()
async def send_image_to_user(file_path: str, caption: str = "") -> str:
    """DEPRECATED: use ``send_file_to_user`` instead.

    Kept for backward compatibility with existing prompts and MCP configs.
    Accepts only image extensions; ``send_file_to_user`` accepts any file type
    and is the preferred tool.

    Args:
        file_path: Absolute path to the image file.
        caption: Optional caption to display with the image.

    Returns:
        Confirmation string when the image is queued for delivery.
    """
    path = Path(file_path)

    if not path.is_absolute():
        return f"Error: path must be absolute, got '{file_path}'"

    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return (
            f"Error: unsupported image extension '{path.suffix}'. "
            f"Supported: {', '.join(sorted(IMAGE_EXTENSIONS))}"
        )

    if not path.is_file():
        return f"Error: file not found: {file_path}"

    return f"Image queued for delivery: {path.name}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
