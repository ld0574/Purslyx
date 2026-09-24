"""上传和解析错误日志可定位，同时不保存用户资料正文。"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter
from starlette.requests import Request

from server.app import api as api_module
from server.app.errors import DomainError
from server.app.main import _error, validation_error_handler
from server.app.parsing import extract_file_text


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "scheme": "https", "server": ("example.test", 443), "path": "/api/v1/documents", "headers": [], "query_string": b""})
    request.state.request_id = "req-test-123"
    return request


def test_domain_error_log_contains_lookup_fields_but_not_private_cause(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        try:
            raise ValueError("PRIVATE_RESUME_BODY")
        except ValueError as cause:
            error = DomainError("DOCUMENT_CONTENT_UNREADABLE", "文件无法读取", 422)
            error.__cause__ = cause
            response = _error(_request(), error)

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert "request_id=req-test-123" in caplog.text
    assert "error_code=DOCUMENT_CONTENT_UNREADABLE" in caplog.text
    assert "cause_type=ValueError" in caplog.text
    assert "PRIVATE_RESUME_BODY" not in caplog.text


def test_dashboard_days_accepts_http_query_strings() -> None:
    annotation = get_type_hints(api_module.dashboard)["days"]

    assert TypeAdapter(annotation).validate_python("7") == "7"
    assert TypeAdapter(annotation).validate_python("14") == "14"
    assert TypeAdapter(annotation).validate_python("30") == "30"


def test_request_validation_log_records_field_code_without_input(caplog: pytest.LogCaptureFixture) -> None:
    request = Request({"type": "http", "method": "GET", "scheme": "https", "server": ("example.test", 443), "path": "/api/v1/dashboard", "headers": [], "query_string": b"days=PRIVATE_VALUE"})
    request.state.request_id = "req-dashboard-422"
    error = RequestValidationError(
        [{"type": "literal_error", "loc": ("query", "days"), "msg": "private", "input": "PRIVATE_VALUE"}]
    )

    with caplog.at_level(logging.WARNING):
        response = asyncio.run(validation_error_handler(request, error))

    assert response.status_code == 422
    assert "request_id=req-dashboard-422" in caplog.text
    assert "fields=query.days:literal_error" in caplog.text
    assert "PRIVATE_VALUE" not in caplog.text


def test_file_extraction_log_does_not_include_filename_or_content(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    file_path = tmp_path / "PRIVATE_RESUME_FILENAME.docx"
    file_path.write_bytes(b"PRIVATE_RESUME_BODY")
    with caplog.at_level(logging.ERROR), pytest.raises(DomainError) as error:
        extract_file_text(file_path, "docx")

    assert error.value.code == "DOCUMENT_CONTENT_UNREADABLE"
    assert "document extraction failed source_type=docx cause_type=PackageNotFoundError" in caplog.text
    assert "PRIVATE_RESUME_FILENAME" not in caplog.text
    assert "PRIVATE_RESUME_BODY" not in caplog.text


@pytest.mark.parametrize(
    ("extract_error", "expected_code"),
    [
        (DomainError("DOCUMENT_CONTENT_UNREADABLE", "文件无法读取", 422), "DOCUMENT_CONTENT_UNREADABLE"),
        (ValueError("PRIVATE_RESUME_BODY"), "DOCUMENT_CREATE_FAILED"),
    ],
)
def test_upload_failure_log_has_stage_without_file_name_or_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture,
    extract_error: Exception, expected_code: str,
) -> None:
    path = tmp_path / "private.pdf"
    path.write_bytes(b"PRIVATE_RESUME_BODY")

    async def read_request(_: Request):
        return {"document_type": "resume", "subject_type": "self_resume", "title": "PRIVATE_TITLE"}, "pdf", b"PRIVATE_RESUME_BODY", "PRIVATE_FILENAME.pdf"

    class Db:
        rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

    db = Db()
    monkeypatch.setattr(api_module, "_read_document_request", read_request)
    monkeypatch.setattr(api_module, "_write_guard", lambda *_: None)
    monkeypatch.setattr(api_module, "_idempotency_key", lambda *_: None)
    monkeypatch.setattr(api_module, "ensure_storage_capacity", lambda *_: None)
    monkeypatch.setattr(api_module, "atomic_write_bytes", lambda *_: path)

    def fail_extract(*_: object):
        raise extract_error

    monkeypatch.setattr(api_module, "extract_file_text", fail_extract)
    with caplog.at_level(logging.WARNING), pytest.raises(DomainError) as error:
        asyncio.run(api_module.create_document_form(_request(), SimpleNamespace(id=7, registration_role="seeker"), db))

    assert db.rolled_back
    assert error.value.code == expected_code
    assert "request_id=req-test-123" in caplog.text
    assert "stage=file_extraction" in caplog.text
    assert f"error_code={expected_code}" in caplog.text
    for private_value in ("PRIVATE_FILENAME", "PRIVATE_TITLE", "PRIVATE_RESUME_BODY"):
        assert private_value not in caplog.text
