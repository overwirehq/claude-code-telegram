"""Tests for the Telegram MCP server tool functions."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.mcp.telegram_server import (
    MAX_FILE_SIZE_BYTES,
    send_file_to_user,
    send_image_to_user,
)


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    """Create a sample image file."""
    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return img


class TestSendImageToUser:
    async def test_valid_image(self, image_file: Path) -> None:
        result = await send_image_to_user(str(image_file))
        assert "Image queued for delivery" in result
        assert "chart.png" in result

    async def test_valid_image_with_caption(self, image_file: Path) -> None:
        result = await send_image_to_user(str(image_file), caption="My chart")
        assert "Image queued for delivery" in result

    async def test_relative_path_rejected(self, image_file: Path) -> None:
        result = await send_image_to_user("relative/path/chart.png")
        assert "Error" in result
        assert "absolute" in result

    async def test_missing_file_rejected(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.png"
        result = await send_image_to_user(str(missing))
        assert "Error" in result
        assert "not found" in result

    async def test_non_image_extension_rejected(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("hello")
        result = await send_image_to_user(str(txt_file))
        assert "Error" in result
        assert "unsupported" in result

    async def test_all_supported_extensions(self, tmp_path: Path) -> None:
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"]:
            img = tmp_path / f"test{ext}"
            img.write_bytes(b"\x00" * 10)
            result = await send_image_to_user(str(img))
            assert "Image queued for delivery" in result, f"Failed for {ext}"

    async def test_case_insensitive_extension(self, tmp_path: Path) -> None:
        img = tmp_path / "photo.JPG"
        img.write_bytes(b"\x00" * 10)
        result = await send_image_to_user(str(img))
        assert "Image queued for delivery" in result


class TestSendFileToUser:
    async def test_valid_pdf(self, tmp_path: Path) -> None:
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4\n" + b"x" * 200)
        result = await send_file_to_user(str(f))
        assert "File queued for delivery" in result
        assert "report.pdf" in result

    async def test_valid_zip_with_caption(self, tmp_path: Path) -> None:
        f = tmp_path / "logs.zip"
        f.write_bytes(b"PK" + b"x" * 100)
        result = await send_file_to_user(str(f), caption="logs")
        assert "File queued for delivery" in result

    async def test_any_extension_accepted(self, tmp_path: Path) -> None:
        for name in ["data.csv", "notes.txt", "archive.tar.gz", "binary.bin"]:
            f = tmp_path / name
            f.write_bytes(b"x" * 64)
            result = await send_file_to_user(str(f))
            assert "File queued for delivery" in result, f"Failed for {name}"

    async def test_relative_path_rejected(self) -> None:
        result = await send_file_to_user("relative/path/report.pdf")
        assert "Error" in result
        assert "absolute" in result

    async def test_missing_file_rejected(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.pdf"
        result = await send_file_to_user(str(missing))
        assert "Error" in result
        assert "not found" in result

    async def test_empty_file_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        result = await send_file_to_user(str(f))
        assert "Error" in result
        assert "empty" in result

    async def test_too_large_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 100)
        real_stat = f.stat()
        from os import stat_result

        fake = stat_result(
            (
                real_stat.st_mode,
                real_stat.st_ino,
                real_stat.st_dev,
                real_stat.st_nlink,
                real_stat.st_uid,
                real_stat.st_gid,
                MAX_FILE_SIZE_BYTES + 1,
                real_stat.st_atime,
                real_stat.st_mtime,
                real_stat.st_ctime,
            )
        )
        with patch.object(Path, "stat", return_value=fake):
            result = await send_file_to_user(str(f))
        assert "Error" in result
        assert "too large" in result
