"""Tests for arbitrary file validation for ``send_file_to_user``."""

from os import stat_result
from pathlib import Path
from unittest.mock import patch

import pytest

from src.bot.utils.file_extractor import (
    MAX_CAPTION_CHARS,
    MAX_FILE_SIZE_BYTES,
    REJECTION_SURFACE_TO_USER,
    FileAttachment,
    validate_file_path,
)


@pytest.fixture
def approved_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


def _ok(result):
    """Assert success: attachment set, reason is None."""
    attachment, reason = result
    assert isinstance(attachment, FileAttachment)
    assert reason is None
    return attachment


def _fail(result, expected_reason: str):
    """Assert failure with expected reason code."""
    attachment, reason = result
    assert attachment is None
    assert reason == expected_reason


class TestValidateFilePath:
    def test_valid_pdf(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "report.pdf"
        f.write_bytes(b"%PDF-1.4\n" + b"x" * 1024)
        attachment = _ok(validate_file_path(str(f), approved_dir, caption="hello"))
        assert attachment.path == f.resolve()
        assert attachment.caption == "hello"
        assert attachment.size_bytes == f.stat().st_size
        assert attachment.original_reference == str(f)

    def test_valid_zip(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "logs.zip"
        f.write_bytes(b"PK" + b"x" * 100)
        _ok(validate_file_path(str(f), approved_dir))

    def test_valid_csv_no_caption(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "data.csv"
        f.write_text("a,b,c\n1,2,3\n")
        attachment = _ok(validate_file_path(str(f), approved_dir))
        assert attachment.caption == ""

    def test_unicode_filename(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "файл с пробелами.pdf"
        f.write_bytes(b"%PDF" + b"x" * 50)
        attachment = _ok(validate_file_path(str(f), approved_dir))
        assert attachment.path.name == "файл с пробелами.pdf"

    def test_unicode_filename_cyrillic(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "отчёт.zip"
        f.write_bytes(b"PK" + b"x" * 100)
        attachment = _ok(validate_file_path(str(f), approved_dir))
        assert attachment.path.name == "отчёт.zip"

    def test_relative_path_rejected(self, approved_dir: Path):
        _fail(validate_file_path("report.pdf", approved_dir), "not_absolute")

    def test_nonexistent_file(self, approved_dir: Path):
        _fail(
            validate_file_path(str(approved_dir / "missing.pdf"), approved_dir),
            "not_a_file",
        )

    def test_empty_file_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "empty.pdf"
        f.write_bytes(b"")
        _fail(validate_file_path(str(f), approved_dir), "empty")

    def test_too_large_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "huge.bin"
        f.write_bytes(b"x" * 100)
        real_stat = f.stat()
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
            _fail(validate_file_path(str(f), approved_dir), "too_large")

    def test_path_traversal_rejected(self, tmp_path: Path):
        approved = tmp_path / "project"
        approved.mkdir()
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"%PDF" + b"x" * 50)
        _fail(validate_file_path(str(outside), approved), "outside_approved")

    def test_env_file_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / ".env"
        f.write_text("SECRET=xxx\n")
        _fail(validate_file_path(str(f), approved_dir), "blocked_secret")

    def test_env_production_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / ".env.production"
        f.write_text("SECRET=xxx\n")
        _fail(validate_file_path(str(f), approved_dir), "blocked_secret")

    def test_id_rsa_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "id_rsa"
        f.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\n")
        _fail(validate_file_path(str(f), approved_dir), "blocked_secret")

    def test_pem_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "cert.pem"
        f.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
        _fail(validate_file_path(str(f), approved_dir), "blocked_secret")

    def test_exe_rejected(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "malware.exe"
        f.write_bytes(b"MZ" + b"x" * 100)
        _fail(validate_file_path(str(f), approved_dir), "blocked_secret")


class TestRejectionSurfaceContract:
    def test_security_reasons_surfaced(self):
        assert "outside_approved" in REJECTION_SURFACE_TO_USER
        assert "blocked_secret" in REJECTION_SURFACE_TO_USER

    def test_tool_duplicates_not_surfaced(self):
        # These are already reported by the MCP tool itself, so the bot must
        # not surface a duplicate summary.
        for reason in ("too_large", "empty", "not_absolute", "not_a_file"):
            assert reason not in REJECTION_SURFACE_TO_USER


class TestSymlinkEscape:
    """Symlink inside approved_dir pointing outside must be rejected."""

    def test_symlink_to_outside_rejected(self, tmp_path: Path):
        approved = tmp_path / "project"
        approved.mkdir()
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"%PDF" + b"x" * 50)
        link = approved / "innocent.pdf"
        link.symlink_to(outside)
        # resolve() follows the symlink, then relative_to catches the escape.
        _fail(validate_file_path(str(link), approved), "outside_approved")

    def test_symlink_within_approved_ok(self, tmp_path: Path):
        approved = tmp_path
        real = approved / "real.pdf"
        real.write_bytes(b"%PDF" + b"x" * 50)
        link = approved / "alias.pdf"
        link.symlink_to(real)
        attachment = _ok(validate_file_path(str(link), approved))
        # resolved path points to the real file, and identity captured.
        assert attachment.path == real.resolve()
        assert attachment.inode == real.stat().st_ino


class TestIdentityCaptured:
    def test_inode_and_device_recorded(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "report.pdf"
        f.write_bytes(b"%PDF" + b"x" * 100)
        attachment = _ok(validate_file_path(str(f), approved_dir))
        st = f.stat()
        assert attachment.inode == st.st_ino
        assert attachment.device == st.st_dev


class TestCaptionTruncation:
    def test_long_caption_truncated_to_telegram_limit(
        self, work_dir: Path, approved_dir: Path
    ):
        f = work_dir / "report.pdf"
        f.write_bytes(b"%PDF" + b"x" * 100)
        long_caption = "x" * (MAX_CAPTION_CHARS + 500)
        attachment = _ok(validate_file_path(str(f), approved_dir, caption=long_caption))
        assert len(attachment.caption) == MAX_CAPTION_CHARS

    def test_short_caption_preserved(self, work_dir: Path, approved_dir: Path):
        f = work_dir / "report.pdf"
        f.write_bytes(b"%PDF" + b"x" * 100)
        attachment = _ok(validate_file_path(str(f), approved_dir, caption="short"))
        assert attachment.caption == "short"
