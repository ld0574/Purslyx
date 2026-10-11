"""机会查询的纯契约：日期、坏报告、游标、来源和独立修订。"""

import base64
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from server.app.api import HandlingStatusRequest
from server.app.errors import DomainError
from server.app.job_opportunities import (
    _cursor_decode,
    _cursor_encode,
    handling_view,
    job_conditions,
    readable_report,
    recent_range,
    source_update_view,
)
from server.app.job_sources import source_identity_url
from server.app.models import JobPoolItem, job_source_key


def test_shanghai_natural_range_across_midnight_and_new_year():
    start, end = recent_range(datetime(2026, 12, 31, 16, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-12-03T00:00:00+08:00"
    assert end.isoformat() == "2027-01-02T00:00:00+08:00"
    assert (end - start).days == 30


@pytest.mark.parametrize("value", [None, [], "report", {}, {"unexpected": True},
                                 {"ability_score": 80, "evidence_coverage": .8, "dimensions": None},
                                 {"ability_score": 80, "evidence_coverage": .8, "conditions": {}},
                                 {"ability_score": 80, "evidence_coverage": .8, "dimensions": [{"requirements": [None]}]},
                                 {"ability_score": 80, "evidence_coverage": .8, "dimensions": [{"requirements": [{"evidence": None}]}]},
                                 {"ability_score": 80, "evidence_coverage": .8, "dimensions": [{"requirements": [{"evidence": [None]}]}]}])
def test_unreadable_new_report_cannot_mask_old_valid_report(value):
    assert not readable_report(value)


def test_unknown_score_is_readable_and_not_inferred_as_zero():
    assert readable_report({"ability_score": None, "evidence_coverage": None, "dimensions": []})
    assert handling_view(None) == {"status": "pending", "label": "待投", "revision": 0, "updated_at": None}


def test_cursor_stable_and_bound_to_filter_scope():
    row = {"_rank": 2, "completed_at": "2026-10-11T01:02:03+00:00", "_analysis_id": 520}
    cursor = _cursor_encode(row, "scope-a")
    assert _cursor_decode(cursor, "scope-a") == (2, -datetime.fromisoformat(row["completed_at"]).timestamp(), -520)
    with pytest.raises(DomainError) as error:
        _cursor_decode(cursor, "scope-b")
    assert error.value.code == "OPPORTUNITY_CURSOR_STALE"


@pytest.mark.parametrize("value", ["bad!!!", "汉字", base64.urlsafe_b64encode(b'\xff').decode(), base64.urlsafe_b64encode(b'[]').decode(), base64.urlsafe_b64encode(json.dumps({"v": True, "scope": "a", "rank": 0, "id": 1, "at": "2026-10-11T01:00:00Z"}).encode()).decode()])
def test_malformed_cursor_returns_domain_error(value):
    with pytest.raises(DomainError) as error:
        _cursor_decode(value, "a")
    assert error.value.status_code == 422


def test_source_keys_do_not_fuzzy_merge_manual_jobs_or_platforms():
    assert job_source_key("manual", None, None, "a") != job_source_key("manual", None, None, "b")
    assert job_source_key("browser_capture", "boss", "x", "a") == job_source_key("browser_capture", "boss", "x", "b")
    assert job_source_key("browser_capture", "boss", "x", "a") != job_source_key("browser_capture", "liepin", "x", "a")
    assert job_source_key("browser_capture", "boss", "", "a") == "manual:a"


def test_url_identity_normalizes_tracking_but_preserves_distinct_job_ids():
    a = "https://www.zhipin.com/job_detail/abc.html?ka=one&securityId=token-a#fragment"
    b = "https://zhipin.com/job_detail/abc.html?ka=two&securityId=token-b"
    assert job_source_key("browser_capture", "boss", "hash-a", "a", a) == job_source_key("browser_capture", "boss", "hash-b", "b", b)
    assert job_source_key("browser_capture", "boss", None, "a", a) != job_source_key("browser_capture", "boss", None, "a", "https://www.zhipin.com/job_detail/xyz.html")
    assert source_identity_url("liepin", "https://www.liepin.com/job/123.shtml?utm_source=a") == "https://www.liepin.com/job/123.shtml"
    assert source_identity_url("boss", "https://www.zhipin.com/details?jobId=123&city=sh&tracking=x") == source_identity_url("boss", "https://www.zhipin.com/details?city=sh&jobId=123&tracking=y")
    assert source_identity_url("boss", "https://www.zhipin.com/details?jobId=123") != source_identity_url("boss", "https://www.zhipin.com/details?jobId=456")
    assert source_identity_url("boss", "https://zhipin.com.evil.test/job_detail/abc.html") is None


def test_conditions_keep_raw_salary_and_bound_version_update():
    fields = {"salary_text": "20-30K·14薪", "locations": ["上海", "杭州"], "work_mode": "hybrid"}
    assert job_conditions(fields)["salary"] == "20-30K·14薪"
    assert job_conditions(fields)["location"] == "上海、杭州"
    latest = JobPoolItem(public_id="latest", job_document_version_id=2)
    assert source_update_view(latest, 1)["updated"]
    assert not source_update_view(latest, 2)["updated"]


def test_duplicate_jd_does_not_claim_updated_content():
    fields = {"title": "工程师", "source_text": "同一 JD", "source_url": "https://www.zhipin.com/job_detail/a.html?ka=one"}
    latest = JobPoolItem(public_id="latest", job_document_version_id=2, job_fields={**fields, "source_url": "https://www.zhipin.com/job_detail/a.html?ka=two", "missing_field_codes": []})
    assert not source_update_view(latest, 1, fields)["updated"]
    assert source_update_view(latest, 1, {**fields, "source_text": "原版 JD"})["updated"]


def test_handling_request_does_not_accept_task_revision_or_usage_mutations():
    body = {"status": "pending", "base_revision": 0}
    assert HandlingStatusRequest(**body).base_revision == 0
    for extra in ({"status": "sent"}, {"base_revision": -1}, {"base_revision": True}, {"confirm_applied": "true"}, {"confirm_usage": True}, {"task_id": "x"}):
        with pytest.raises(ValidationError):
            HandlingStatusRequest(**{**body, **extra})


def test_handling_patch_is_allowed_by_web_cors_preflight():
    from fastapi.testclient import TestClient

    from server.app.config import settings
    from server.app.main import app

    origin = settings.origins[0]
    response = TestClient(app).options("/api/v1/job-pool/items/synthetic/handling-status", headers={
        "Origin": origin, "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "content-type,x-csrf-token,idempotency-key",
    })
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "PATCH" in response.headers["Access-Control-Allow-Methods"]
