"""注册验证码契约测试。"""

from __future__ import annotations

import pytest

from server.app import api
from server.app.config import Settings
from server.app.errors import DomainError


def _answer(question: str) -> str:
    left, right = question.removesuffix(" = ?").split(" + ")
    return str(int(left) + int(right))


def test_captcha_is_signed_and_answer_is_not_in_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "settings", Settings(token_secret="a" * 32))
    challenge = api._issue_captcha()

    assert challenge["question"].endswith(" = ?")
    payload_parts = challenge["captcha_id"].rsplit(".", 1)[0].split(".")
    assert _answer(challenge["question"]) not in payload_parts
    api._validate_captcha(challenge["captcha_id"], _answer(challenge["question"]))

    with pytest.raises(DomainError) as error:
        api._validate_captcha(challenge["captcha_id"], "0")
    assert error.value.code == "AUTH_CAPTCHA_INVALID"


def test_captcha_rejects_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "settings", Settings(token_secret="b" * 32))
    challenge = api._issue_captcha()
    last = challenge["captcha_id"][-1]
    tampered = challenge["captcha_id"][:-1] + ("0" if last != "0" else "1")

    with pytest.raises(DomainError) as error:
        api._validate_captcha(tampered, _answer(challenge["question"]))
    assert error.value.code == "AUTH_CAPTCHA_INVALID"
