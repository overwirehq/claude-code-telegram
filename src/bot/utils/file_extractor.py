"""Validate arbitrary file paths for Telegram document delivery.

Used by the MCP ``send_file_to_user`` tool intercept — the stream callback
validates each path via :func:`validate_file_path` and collects
:class:`FileAttachment` objects for later Telegram delivery.

Full security validation lives here (not in the MCP tool itself) so the tool
can run without access to the bot's runtime configuration. A path that passes
the tool-side syntactic check but fails here is logged as a warning and the
user sees a short error summary; Claude has already received "queued" but the
file simply never arrives.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import structlog

from src.security.validators import SecurityValidator

logger = structlog.get_logger()

# Telegram Bot API document upload limit.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024

# Reasons returned by :func:`validate_file_path`. Only ``outside_approved`` and
# ``blocked_secret`` are surfaced to the user as bot-side rejections — the
# others (``too_large``, ``empty``, ``not_absolute``, ``not_a_file``) are
# already caught by the MCP tool itself, so Claude receives the error and
# reports the real reason in natural language.
REJECTION_SURFACE_TO_USER = frozenset({"outside_approved", "blocked_secret"})


# Telegram caption limit for photos and documents.
MAX_CAPTION_CHARS = 1024


@dataclass
class FileAttachment:
    """A file to attach to a Telegram response as a document.

    ``inode`` and ``device`` are captured at validation time so the caller
    can detect TOCTOU replacement (symlink swap, file renamed over, etc.)
    before re-opening the path for delivery.
    """

    path: Path
    original_reference: str
    size_bytes: int
    caption: str = ""
    inode: int = 0
    device: int = 0


def _is_forbidden_filename(name: str) -> bool:
    """Check whether *name* matches the SecurityValidator secrets blocklist.

    We reuse the validator's FORBIDDEN_FILENAMES and DANGEROUS_FILE_PATTERNS
    (keys/certs/credentials) but NOT the extension whitelist — arbitrary file
    types are intentionally allowed here.
    """
    lower = name.lower()
    if lower in {n.lower() for n in SecurityValidator.FORBIDDEN_FILENAMES}:
        return True
    for pattern in SecurityValidator.DANGEROUS_FILE_PATTERNS:
        if re.match(pattern, name, re.IGNORECASE):
            return True
    return False


def validate_file_path(
    file_path: str,
    approved_directory: Path,
    caption: str = "",
) -> Tuple[Optional[FileAttachment], Optional[str]]:
    """Validate a file path from an MCP ``send_file_to_user`` call.

    Returns a tuple ``(attachment, reason)``:

    - on success: ``(FileAttachment, None)``;
    - on failure: ``(None, reason)`` where *reason* is one of
      ``not_absolute``, ``outside_approved``, ``not_a_file``,
      ``blocked_secret``, ``empty``, ``too_large``.

    Callers should only surface ``reason ∈ REJECTION_SURFACE_TO_USER`` to the
    user — other reasons duplicate errors the MCP tool itself already returns.
    """
    try:
        path = Path(file_path)
        if not path.is_absolute():
            return None, "not_absolute"

        resolved = path.resolve()

        try:
            resolved.relative_to(approved_directory.resolve())
        except ValueError:
            logger.warning(
                "MCP file path outside approved directory",
                path=str(resolved),
                approved=str(approved_directory),
            )
            return None, "outside_approved"

        if not resolved.is_file():
            logger.debug("MCP file path is not a file", path=str(resolved))
            return None, "not_a_file"

        if _is_forbidden_filename(resolved.name):
            logger.warning(
                "MCP file path rejected by secrets blocklist",
                path=str(resolved),
            )
            return None, "blocked_secret"

        stat_result = resolved.stat()
        size_bytes = stat_result.st_size
        if size_bytes == 0:
            logger.debug("MCP file is empty", path=str(resolved))
            return None, "empty"
        if size_bytes > MAX_FILE_SIZE_BYTES:
            logger.warning(
                "MCP file too large",
                path=str(resolved),
                size=size_bytes,
                limit=MAX_FILE_SIZE_BYTES,
            )
            return None, "too_large"

        return (
            FileAttachment(
                path=resolved,
                original_reference=file_path,
                size_bytes=size_bytes,
                caption=caption[:MAX_CAPTION_CHARS],
                inode=stat_result.st_ino,
                device=stat_result.st_dev,
            ),
            None,
        )
    except (OSError, ValueError) as e:
        logger.debug("MCP file path validation failed", path=file_path, error=str(e))
        return None, "not_a_file"
