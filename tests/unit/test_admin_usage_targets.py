"""次数发放账号选择器的授权和最小字段契约。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from server.app import api as api_module


def test_grant_only_admin_can_search_active_targets_without_user_detail_access(monkeypatch) -> None:
    permissions: list[str] = []
    statements: list[str] = []

    def guard(_request, _account, _db, permission):
        permissions.append(permission)

    def rows(_db, statement, _model, *, cursor, limit, timestamp_field):
        statements.append(str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        assert cursor is None
        assert limit == 20
        assert timestamp_field == "created_at"
        return [SimpleNamespace(public_id="user-1", email="ada@example.test", registration_role="seeker", status="active")], {"has_more": False, "next_cursor": None}

    monkeypatch.setattr(api_module, "_admin_guard", guard)
    monkeypatch.setattr(api_module, "page_rows", rows)
    request = Request({"type": "http", "method": "GET", "scheme": "https", "server": ("example.test", 443), "path": "/api/v1/admin/usage-targets", "headers": [], "query_string": b""})
    request.state.request_id = "req-picker"

    response = api_module.admin_usage_targets(request, SimpleNamespace(), search="ADA", registration_role="seeker", cursor=None, limit=20, db=object())
    payload = json.loads(response.body)["data"]

    assert permissions == ["admin.usage.grant"]
    assert "accounts.status = 'active'" in statements[0]
    assert "accounts.registration_role = 'seeker'" in statements[0]
    assert "%ada%" in statements[0]
    assert payload["items"] == [{"id": "user-1", "email": "ada@example.test", "registration_role": "seeker"}]
