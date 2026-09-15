"""账号邮件的链接、安全日志和 SMTP 行为测试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from server.app import email_delivery


class _FakeSmtp:
    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages = []

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, **kwargs: object) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.login_values = (username, password)

    def send_message(self, message: object) -> None:
        self.messages.append(message)


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_username": "mailer",
        "smtp_password": "password",
        "smtp_from": "Purslyx <no-reply@example.test>",
        "smtp_starttls": True,
        "product_origin": "https://purslyx.example.test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_send_account_email_builds_encoded_product_link(monkeypatch) -> None:
    smtp = _FakeSmtp("unused", 0, 0)
    monkeypatch.setattr(email_delivery, "settings", _settings())
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", lambda *args, **kwargs: smtp)

    email_delivery.send_account_action_email(
        "user@example.test", "reset_password", "secret+/="
    )

    body = smtp.messages[0].get_content()
    assert "https://purslyx.example.test/app/reset-password?token=secret%2B%2F%3D" in body
    assert smtp.login_values == ("mailer", "password")


def test_delivery_failure_never_logs_raw_token(monkeypatch, caplog) -> None:
    raw_token = "never-log-this-token"
    monkeypatch.setattr(email_delivery, "settings", _settings(smtp_host=None))

    with caplog.at_level(logging.WARNING):
        delivered = email_delivery.deliver_account_action_email(
            "user@private.example", "verify_email", raw_token
        )

    assert delivered is False
    assert raw_token not in caplog.text
    assert "private.example" in caplog.text
