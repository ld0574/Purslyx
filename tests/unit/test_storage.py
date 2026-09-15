"""私有文件路径、原子写入和容量边界测试。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app import storage
from server.app.errors import DomainError
from server.app.storage import (
    atomic_write_bytes,
    ensure_storage_capacity,
    private_path,
    safe_download_name,
    storage_key,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("../resume.pdf", "resume.pdf"),
        ("..\\resume.pdf", "resume.pdf"),
        ("...", "download.bin"),
        ("\x00bad\nname.pdf", "badname.pdf"),
        (None, "download.bin"),
    ],
)
def test_download_filename_is_safe(value: str | None, expected: str) -> None:
    assert safe_download_name(value, "download.bin") == expected


def test_private_path_and_storage_key_stay_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "private"
    path = private_path("documents/file.pdf", root)
    assert path == root / "documents/file.pdf"
    assert storage_key(path, root) == "documents/file.pdf"

    with pytest.raises(DomainError) as relative_error:
        private_path("../escape.txt", root)
    assert relative_error.value.code == "STORAGE_PATH_INVALID"
    with pytest.raises(DomainError):
        private_path(tmp_path / "outside.txt", root)


def test_atomic_write_has_exact_content_and_private_mode(tmp_path: Path) -> None:
    output = atomic_write_bytes(tmp_path / "uploads", b"private-content", ".pdf")
    assert output.read_bytes() == b"private-content"
    assert output.suffix == ".pdf"
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(output.parent.glob("*.uploading"))


def test_atomic_write_cleans_partial_files_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "uploads"

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_bytes(directory, b"payload", ".txt")
    assert list(directory.iterdir()) == []


class _CapacitySession:
    def __init__(self, used: int) -> None:
        self.used = used

    def scalar(self, statement: object) -> int:
        return self.used


def test_capacity_accounts_for_registered_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "settings", SimpleNamespace(storage_limit_bytes=100))
    db = _CapacitySession(90)
    ensure_storage_capacity(db, 10)  # type: ignore[arg-type]
    with pytest.raises(DomainError) as error:
        ensure_storage_capacity(db, 11)  # type: ignore[arg-type]
    assert error.value.code == "STORAGE_CAPACITY_LIMIT"
    assert error.value.retryable is True
    with pytest.raises(DomainError):
        ensure_storage_capacity(db, -1)  # type: ignore[arg-type]
