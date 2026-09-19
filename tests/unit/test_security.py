"""认证令牌、密码、CSRF 与固定身份边界测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from server.app import security
from server.app.errors import DomainError
from server.app.security import (
    compare_secret,
    hash_password,
    hash_secret,
    issue_secret,
    normalize_email,
    require_csrf,
    require_recruiter,
    require_seeker,
    require_verified,
    verify_password,
)


def _request(
    method: str,
    *,
    origin: str | None = None,
    csrf_token: str | None = None,
    query_string: bytes = b"",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if csrf_token is not None:
        headers.append((b"x-csrf-token", csrf_token.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/preferences",
            "headers": headers,
            "query_string": query_string,
        }
    )


def test_email_and_secret_normalization() -> None:
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    token = issue_secret()
    assert len(token) >= 40
    assert compare_secret(token, hash_secret(token)) is True
    assert compare_secret(token + "x", hash_secret(token)) is False


def test_explicit_bearer_session_takes_precedence_over_cookie() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/me",
            "headers": [(b"cookie", b"purslyx_session=cookie-session")],
            "query_string": b"",
        }
    )
    assert security._web_token(request, "Bearer explicit-session") == "explicit-session"
    assert security._web_token(request, None) == "cookie-session"


@pytest.mark.parametrize("length", [0, 7, 129])
def test_password_length_is_enforced(length: int) -> None:
    with pytest.raises(DomainError) as error:
        hash_password("a" * length)
    assert error.value.code == "AUTH_PASSWORD_INVALID"


def test_password_hash_verification_and_invalid_hash() -> None:
    password_hash = hash_password("12345678")
    assert verify_password(password_hash, "12345678") is True
    assert verify_password(password_hash, "wrong-password") is False
    assert verify_password("not-an-argon-hash", "12345678") is False


def test_csrf_allows_read_and_valid_write(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "csrf-token"
    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(origins=["http://127.0.0.1:8001"]),
    )
    read_request = _request("GET", origin="https://untrusted.example")
    require_csrf(read_request, SimpleNamespace())

    write_request = _request(
        "POST",
        origin="http://127.0.0.1:8001",
        csrf_token=token,
    )
    write_request.state.web_session = SimpleNamespace(csrf_token_hash=hash_secret(token))
    require_csrf(write_request, SimpleNamespace())


@pytest.mark.parametrize(
    ("origin", "csrf_token", "expected_code"),
    [
        ("https://untrusted.example", "csrf-token", "CSRF_ORIGIN_INVALID"),
        ("http://127.0.0.1:8001", None, "CSRF_INVALID"),
        ("http://127.0.0.1:8001", "wrong-token", "CSRF_INVALID"),
    ],
)
def test_csrf_rejects_invalid_write(
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
    csrf_token: str | None,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(origins=["http://127.0.0.1:8001"]),
    )
    request = _request("POST", origin=origin, csrf_token=csrf_token)
    request.state.web_session = SimpleNamespace(csrf_token_hash=hash_secret("csrf-token"))
    with pytest.raises(DomainError) as error:
        require_csrf(request, SimpleNamespace())
    assert error.value.code == expected_code
    assert error.value.status_code == 403


def test_role_and_verification_guards() -> None:
    seeker = SimpleNamespace(registration_role="seeker", email_verified_at=object())
    recruiter = SimpleNamespace(registration_role="recruiter", email_verified_at=None)
    require_seeker(seeker)
    require_recruiter(recruiter)
    require_verified(seeker)

    with pytest.raises(DomainError) as role_error:
        require_seeker(recruiter)
    assert role_error.value.code == "ROLE_NOT_ALLOWED"
    with pytest.raises(DomainError) as verified_error:
        require_verified(recruiter)
    assert verified_error.value.code == "AUTH_EMAIL_UNVERIFIED"
