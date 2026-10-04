"""把 HTTP 请求 ID 延续到异步任务，不携带业务正文。"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_REQUEST_ID: ContextVar[str | None] = ContextVar("purslyx_request_id", default=None)


def validated_request_id(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) else None


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def task_request_id(input_data: Any) -> str | None:
    return validated_request_id(input_data.get("_request_id")) if isinstance(input_data, dict) else None


@contextmanager
def request_context(value: Any) -> Iterator[None]:
    token = _REQUEST_ID.set(validated_request_id(value))
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)
