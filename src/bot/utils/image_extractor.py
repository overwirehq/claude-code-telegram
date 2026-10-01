"""Validate image file paths and prepare them for Telegram delivery.

Used by the MCP ``send_image_to_user`` tool intercept — the stream callback
validates each path via :func:`validate_image_path` and collects
:class:`ImageAttachment` objects for later Telegram delivery.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()

# Supported image extensions -> MIME types
IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}

# Raster formats that can be sent via reply_photo() (Telegram supports these natively)
TELEGRAM_PHOTO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Safety caps
MAX_IMAGES_PER_RESPONSE = 10
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
PHOTO_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB — Telegram photo API limit

_IMAGE_EXT_PATTERN = "png|jpe?g|gif|webp|bmp|svg"
_ABSOLUTE_IMAGE_RE = re.compile(
    rf"(?P<path>/[^\s`<>\"']+?\.(?:{_IMAGE_EXT_PATTERN}))",
    re.IGNORECASE,
)
_QUOTED_IMAGE_RE = re.compile(
    rf"[`'\"](?P<path>[^`'\"]+?\.(?:{_IMAGE_EXT_PATTERN}))[`'\"]",
    re.IGNORECASE,
)
_BARE_IMAGE_RE = re.compile(
    rf"(?<![\w/.-])(?P<path>[\w./-]+\.(?:{_IMAGE_EXT_PATTERN}))(?![\w/.-])",
    re.IGNORECASE,
)


@dataclass
class ImageAttachment:
    """An image file to attach to a Telegram response."""

    path: Path
    mime_type: str
    original_reference: str


def validate_image_path(
    file_path: str,
    approved_directory: Path,
    caption: str = "",
) -> Optional[ImageAttachment]:
    """Validate a single image path from an MCP ``send_image_to_user`` call.

    Returns an :class:`ImageAttachment` if the path is a valid, existing image
    inside *approved_directory*, or ``None`` otherwise.
    """
    try:
        path = Path(file_path)
        if not path.is_absolute():
            return None

        resolved = path.resolve()

        # Security: must be within approved directory
        try:
            resolved.relative_to(approved_directory.resolve())
        except ValueError:
            logger.debug(
                "MCP image path outside approved directory",
                path=str(resolved),
                approved=str(approved_directory),
            )
            return None

        if not resolved.is_file():
            return None

        file_size = resolved.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            logger.debug("MCP image file too large", path=str(resolved), size=file_size)
            return None

        ext = resolved.suffix.lower()
        mime_type = IMAGE_EXTENSIONS.get(ext)
        if not mime_type:
            return None

        return ImageAttachment(
            path=resolved,
            mime_type=mime_type,
            original_reference=caption or file_path,
        )
    except (OSError, ValueError) as e:
        logger.debug("MCP image path validation failed", path=file_path, error=str(e))
        return None


def extract_image_paths_from_text(
    text: str,
    approved_directory: Path,
    working_directory: Path,
    limit: int = MAX_IMAGES_PER_RESPONSE,
) -> list[ImageAttachment]:
    """Extract existing image paths from agent text for Telegram delivery.

    OpenCode may answer with a path like ``/workspace/foo.png`` or simply
    ``foo.png``. Only existing image files inside *approved_directory* are
    returned. Bare filenames are resolved relative to *working_directory*, then
    searched recursively under it as a convenience for prompts like "send it".
    """
    if not text.strip():
        return []

    approved_directory = approved_directory.resolve()
    working_directory = working_directory.resolve()
    candidates: list[str] = []

    for regex in (_ABSOLUTE_IMAGE_RE, _QUOTED_IMAGE_RE, _BARE_IMAGE_RE):
        for match in regex.finditer(text):
            candidate = match.group("path").strip().rstrip(".,;:)]}")
            if candidate and candidate not in candidates:
                candidates.append(candidate)

    images: list[ImageAttachment] = []
    seen: set[Path] = set()

    def add_candidate(path: Path, reference: str) -> None:
        if len(images) >= limit:
            return
        img = validate_image_path(str(path), approved_directory, reference)
        if img and img.path not in seen:
            seen.add(img.path)
            images.append(img)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_absolute():
            add_candidate(path, candidate)
            continue

        direct_path = working_directory / path
        add_candidate(direct_path, candidate)
        if len(images) >= limit:
            break

        if path.parent == Path(".") and not direct_path.is_file():
            try:
                for match in working_directory.rglob(path.name):
                    add_candidate(match, candidate)
                    if len(images) >= limit:
                        break
            except OSError as e:
                logger.debug(
                    "Image filename search failed",
                    filename=path.name,
                    working_directory=str(working_directory),
                    error=str(e),
                )

    return images


def should_send_as_photo(path: Path) -> bool:
    """Return True if the image should be sent via reply_photo().

    Raster images ≤ 10 MB are sent as photos (inline preview).
    SVGs and large files are sent as documents.
    """
    ext = path.suffix.lower()
    if ext not in TELEGRAM_PHOTO_EXTENSIONS:
        return False

    try:
        return path.stat().st_size <= PHOTO_SIZE_LIMIT
    except OSError:
        return False
